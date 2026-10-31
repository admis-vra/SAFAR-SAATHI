import json
import math
import random
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from eta import predict_delay_minutes

SEED = json.loads((Path(__file__).parent / "seed.json").read_text())
EXTRA_TRIP_THRESHOLD = 30

app = FastAPI(title="SafarSaathi API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

routes = {r["id"]: r for r in SEED["routes"]}
buses = {
    b["id"]: {**b, "count": None, "seat_cam": None, "assigned": 0, "status": "scheduled", "location": None,
              "updated_at": None}
    for b in SEED["buses"]
}
students: dict[str, dict] = {}


def _demo_exam_demand() -> list[dict]:
    rng = random.Random(42)
    plan = {1: (62, "09:00"), 2: (54, "11:00"), 3: (140, "11:00"), 4: (88, "14:00")}
    out = []
    for route_id, (n, slot) in plan.items():
        stops = routes[route_id]["stops"]
        out += [{"route_id": route_id, "stop": rng.choice(stops), "exam_slot": slot} for _ in range(n)]
    return out


exam_demand = _demo_exam_demand()


class StudentIn(BaseModel):
    id: str
    name: str
    route_id: int
    stop: str
    exam_slot: str | None = None


class CountIn(BaseModel):
    count: int = Field(ge=0)
    seats_free: int | None = Field(default=None, ge=0)
    seats_total: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _seats_together(self):
        if (self.seats_free is None) != (self.seats_total is None):
            raise ValueError("send seats_free and seats_total together")
        if self.seats_free is not None and self.seats_free > self.seats_total:
            raise ValueError("seats_free cannot exceed seats_total")
        return self


class LocationIn(BaseModel):
    lat: float
    lng: float


class StatusIn(BaseModel):
    status: str


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def broadcast(self, msg: dict):
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()


def occupancy(bus: dict) -> int:
    return bus["count"] if bus["count"] is not None else bus["assigned"]


def seat_source(bus: dict) -> str:
    cam = bus["seat_cam"]
    if cam:
        return "camera" if cam["total"] >= bus["capacity"] else "camera_partial"
    return "people_count" if bus["count"] is not None else "allocations"


def seats_free(bus: dict) -> int:
    cam = bus["seat_cam"]
    if cam:
        if cam["total"] >= bus["capacity"]:
            return min(cam["free"], bus["capacity"])
        # Camera sees only part of the bus: assume unseen seats fill at the same rate as seen ones.
        return round(cam["free"] * bus["capacity"] / cam["total"])
    return max(bus["capacity"] - occupancy(bus), 0)


def eta(bus: dict) -> dict:
    sched = datetime.combine(datetime.now().date(), datetime.strptime(bus["departure"], "%H:%M").time())
    delay, source = predict_delay_minutes(
        weekday=sched.weekday(), hour=sched.hour, exam_day=False, load=occupancy(bus) / bus["capacity"]
    )
    return {"scheduled": bus["departure"], "predicted": (sched + timedelta(minutes=delay)).strftime("%H:%M"),
            "delay_min": round(delay, 1), "source": source}


def bus_view(bus: dict) -> dict:
    return {
        "id": bus["id"], "route_id": bus["route_id"], "gate": bus["gate"], "stops": bus["stops"],
        "capacity": bus["capacity"], "occupancy": occupancy(bus), "seats_free": seats_free(bus),
        "seat_source": seat_source(bus), "seats_seen": bus["seat_cam"]["total"] if bus["seat_cam"] else None,
        "status": bus["status"], "location": bus["location"], "eta": eta(bus), "updated_at": bus["updated_at"],
    }


def candidate_buses(route_id: int, stop: str) -> list[dict]:
    cands = [b for b in buses.values() if b["route_id"] == route_id and stop in b["stops"] and b["status"] != "departed"]
    return sorted(cands, key=lambda b: b["departure"])


def allocate(student: dict) -> dict:
    cands = candidate_buses(student["route_id"], student["stop"])
    if not cands:
        return {"bus": None, "alert": "No bus serves your stop right now. Contact the transport office."}
    primary = cands[0]
    for bus in cands:
        if seats_free(bus) > 0:
            alert = None if bus is primary else f"Bus {primary['id']} full, take Bus {bus['id']}"
            return {"bus": bus_view(bus), "alert": alert}
    return {"bus": bus_view(primary), "alert": f"All buses for stop {student['stop']} are full."}


def touch(bus: dict):
    bus["updated_at"] = datetime.now().isoformat(timespec="seconds")


@app.get("/routes")
def list_routes():
    return list(routes.values())


@app.get("/buses")
def list_buses():
    return [bus_view(b) for b in buses.values()]


@app.get("/buses/{bus_id}")
def get_bus(bus_id: int):
    if bus_id not in buses:
        raise HTTPException(404, "bus not found")
    return bus_view(buses[bus_id])


@app.post("/students")
async def register_student(s: StudentIn):
    if s.route_id not in routes or s.stop not in routes[s.route_id]["stops"]:
        raise HTTPException(400, "unknown route or stop")
    old = students.get(s.id)
    if old and old.get("bus_id") in buses:
        buses[old["bus_id"]]["assigned"] -= 1
    student = s.model_dump()
    result = allocate(student)
    if result["bus"]:
        student["bus_id"] = result["bus"]["id"]
        buses[student["bus_id"]]["assigned"] += 1
    students[s.id] = student
    await hub.broadcast({"type": "buses", "data": list_buses()})
    bus = bus_view(buses[student["bus_id"]]) if "bus_id" in student else None
    return {"student": student, "bus": bus, "alert": result["alert"]}


def allocate_view(student: dict) -> dict:
    bus_id = student.get("bus_id")
    if bus_id is None:
        return allocate(student)
    if buses[bus_id]["status"] == "departed":
        result = allocate(student)
        if result["bus"]:
            result["alert"] = f"Bus {bus_id} has left. Next: Bus {result['bus']['id']}"
        return result
    return {"bus": bus_view(buses[bus_id]), "alert": None}


@app.get("/students/{student_id}/bus")
def my_bus(student_id: str):
    if student_id not in students:
        raise HTTPException(404, "student not registered")
    return allocate_view(students[student_id])


@app.post("/buses/{bus_id}/count")
async def report_count(bus_id: int, body: CountIn):
    bus = buses.get(bus_id) or _404()
    bus["count"] = body.count
    bus["seat_cam"] = {"free": body.seats_free, "total": body.seats_total} if body.seats_total else None
    touch(bus)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    if seats_free(bus) == 0:
        await hub.broadcast({"type": "alert", "bus_id": bus_id, "message": f"Bus {bus_id} is full"})
    return bus_view(bus)


@app.post("/buses/{bus_id}/location")
async def report_location(bus_id: int, body: LocationIn):
    bus = buses.get(bus_id) or _404()
    bus["location"] = body.model_dump()
    touch(bus)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return bus_view(bus)


@app.post("/buses/{bus_id}/status")
async def report_status(bus_id: int, body: StatusIn):
    if body.status not in {"scheduled", "boarding", "departed", "full"}:
        raise HTTPException(400, "status must be scheduled|boarding|departed|full")
    bus = buses.get(bus_id) or _404()
    bus["status"] = body.status
    touch(bus)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return bus_view(bus)


@app.get("/planner")
def planner(slot: str = "11:00"):
    slot_dt = datetime.strptime(slot, "%H:%M")
    per_route = []
    for route_id, route in routes.items():
        n = sum(1 for d in exam_demand if d["route_id"] == route_id and d["exam_slot"] == slot)
        n += sum(1 for s in students.values() if s["route_id"] == route_id and s.get("exam_slot") == slot)
        row = {"route_id": route_id, "route": route["name"], "students": n, "suggestion": None}
        if n >= EXTRA_TRIP_THRESHOLD:
            leave = (slot_dt - timedelta(hours=1)).strftime("%H:%M")
            row["suggestion"] = {"depart": leave, "buses": math.ceil(n / 40),
                                 "text": f"{route['name']} has {n} students for the {slot} shift. "
                                         f"Add a {leave} trip with {math.ceil(n / 40)} buses."}
        per_route.append(row)
    return {"slot": slot, "threshold": EXTRA_TRIP_THRESHOLD, "routes": per_route}


@app.get("/dashboard/summary")
def dashboard_summary():
    views = list_buses()
    return {
        "buses": views,
        "total_capacity": sum(b["capacity"] for b in views),
        "total_onboard": sum(b["occupancy"] for b in views),
        "half_empty": [b["id"] for b in views if b["occupancy"] < b["capacity"] / 2],
        "full": [b["id"] for b in views if b["seats_free"] == 0],
    }


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    hub.clients.add(websocket)
    await websocket.send_json({"type": "buses", "data": list_buses()})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.clients.discard(websocket)


def _404():
    raise HTTPException(404, "bus not found")
