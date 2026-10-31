import asyncio
import hashlib
import logging
import math
import os
import random
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone

from fastapi import Body, Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, model_validator

import db
import ingest
from auth import current_user
from eta import predict_delay_minutes

log = logging.getLogger("safarsaathi")

EXTRA_TRIP_THRESHOLD = 30
OFFLINE_AFTER_S = 30
OFFLINE_CHECK_EVERY_S = 5

routes: dict[int, dict] = {}
stop_ids: dict[tuple[int, str], int] = {}
buses: dict[int, dict] = {}
students: dict[str, dict] = {}
exam_demand: list[dict] = []


def _demo_exam_demand() -> list[dict]:
    rng = random.Random(42)
    plan = {1: (62, "09:00"), 2: (54, "11:00"), 3: (140, "11:00"), 4: (88, "14:00")}
    out = []
    for route_id, (n, slot) in plan.items():
        if route_id in routes:
            stops = routes[route_id]["stops"]
            out += [{"route_id": route_id, "stop": rng.choice(stops), "exam_slot": slot} for _ in range(n)]
    return out


async def load_state():
    loaded_routes, loaded_stop_ids = await db.load_routes()
    routes.clear(); routes.update(loaded_routes)
    stop_ids.clear(); stop_ids.update(loaded_stop_ids)
    buses.clear(); buses.update({b["id"]: b for b in await db.load_buses()})
    for bus_id, incident_id in (await db.open_incidents("device_offline")).items():
        buses[bus_id]["offline_incident"] = incident_id
    students.clear()
    exam_demand[:] = _demo_exam_demand()
    for p in await db.all_profiles():
        students[p["id"]] = p
        result = allocate(p)
        if result["bus"]:
            p["bus_id"] = result["bus"]["id"]
            buses[p["bus_id"]]["assigned"] += 1


@asynccontextmanager
async def lifespan(_app: FastAPI):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("Set DATABASE_URL in backend/.env (see backend/.env.example)")
    await db.connect(dsn)
    await db.ensure_partitions()
    await load_state()

    subscriber = None
    if os.environ.get("MQTT_URL"):
        subscriber = ingest.MqttSubscriber(os.environ["MQTT_URL"], apply_device_telemetry, asyncio.get_running_loop())
        subscriber.start()
    else:
        log.warning("MQTT_URL not set: MQTT ingest disabled (HTTP /ingest still works)")
    watcher = asyncio.create_task(watch_offline())
    yield
    watcher.cancel()
    with suppress(asyncio.CancelledError):
        await watcher
    if subscriber:
        subscriber.stop()
    await db.close()


app = FastAPI(title="SafarSaathi API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class StopIn(BaseModel):
    route_id: int
    stop: str
    exam_slot: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")


class ProfileIn(StopIn):
    student_id: str = Field(min_length=3, max_length=32)
    name: str = Field(min_length=1, max_length=60)


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
        "status": bus["status"], "location": bus["location"], "speed_kmh": bus.get("speed_kmh"),
        "eta": eta(bus), "updated_at": bus["updated_at"],
        "device": {
            "id": bus.get("device_id"),
            "online": bus.get("last_seen") is not None and not bus.get("offline_incident"),
            "last_reading": bus["last_device_ts"].isoformat() if bus.get("last_device_ts") else None,
            "ir_net": bus.get("ir_net"),
        },
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


async def persist(bus: dict, telemetry: bool = True):
    now = datetime.now(timezone.utc)
    bus["updated_at"] = now.astimezone().isoformat(timespec="seconds")
    await db.save_live_state(bus)
    if telemetry:
        await db.insert_telemetry(bus, now)


async def apply_device_telemetry(device_id: str, items: list[ingest.Telemetry], from_backlog: bool) -> dict:
    bus = next((b for b in buses.values() if b.get("device_id") == device_id), None)
    if bus is None:
        log.warning("telemetry from unknown device %s", device_id)
        return {"accepted": 0, "duplicates": 0, "live_updated": False}
    if any(i.bus_id != device_id for i in items):
        log.warning("payload bus_id does not match device %s; dropped", device_id)
        return {"accepted": 0, "duplicates": 0, "live_updated": False}

    items = sorted(items, key=lambda i: i.timestamp)
    new_ts = await db.insert_device_telemetry(bus["id"], bus.get("trip_id"), items, from_backlog)
    fresh = [i for i in items if i.ts in new_ts]
    bus["last_seen"] = time.monotonic()
    if bus.get("offline_incident"):
        await db.close_incident(bus.pop("offline_incident"))
        await hub.broadcast({"type": "incident", "bus_id": bus["id"], "kind": "device_offline", "open": False})

    # Only a reading newer than what we already show may move the live state (late/out-of-order data is
    # history only). Replayed backlog never triggers alerts; that rule matters once M3 adds geofence events.
    latest = fresh[-1] if fresh else None
    live = latest is not None and (bus.get("last_device_ts") is None or latest.ts > bus["last_device_ts"])
    if live:
        bus["last_device_ts"] = latest.ts
        if latest.has_fix:
            bus["location"] = {"lat": latest.gps.lat, "lng": latest.gps.lon}
        bus["speed_kmh"] = latest.gps.speed
        bus["ir_net"] = latest.pax.net_current
        bus["count"] = max(latest.pax.net_current, 0)
        bus["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        await db.save_live_state(bus)
        await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return {"accepted": len(fresh), "duplicates": len(items) - len(fresh), "live_updated": live}


async def check_offline(now: float):
    for bus in buses.values():
        seen = bus.get("last_seen")
        if seen is None or bus.get("offline_incident") or now - seen <= OFFLINE_AFTER_S:
            continue
        bus["offline_incident"] = await db.open_incident(
            bus["id"], bus.get("trip_id"), "device_offline", f"No data for over {OFFLINE_AFTER_S} s")
        await hub.broadcast({"type": "incident", "bus_id": bus["id"], "kind": "device_offline", "open": True})


async def watch_offline():
    while True:
        await asyncio.sleep(OFFLINE_CHECK_EVERY_S)
        try:
            await check_offline(time.monotonic())
        except Exception:
            log.exception("offline check failed")


async def device_bus(x_device_token: str | None = Header(default=None)) -> dict:
    bus_id = None
    if x_device_token:
        bus_id = await db.bus_for_token_hash(hashlib.sha256(x_device_token.encode()).hexdigest())
    if bus_id is None or bus_id not in buses:
        raise HTTPException(401, "Unknown device token.")
    return buses[bus_id]


@app.post("/ingest/telemetry")
async def ingest_telemetry(body: ingest.Telemetry, bus: dict = Depends(device_bus)):
    if body.bus_id != bus["device_id"]:
        raise HTTPException(403, f"This token belongs to {bus['device_id']}, not {body.bus_id}.")
    return await apply_device_telemetry(bus["device_id"], [body], from_backlog=False)


@app.post("/ingest/backlog")
async def ingest_backlog(body: list[ingest.Telemetry] = Body(max_length=2000), bus: dict = Depends(device_bus)):
    if any(i.bus_id != bus["device_id"] for i in body):
        raise HTTPException(403, f"This token belongs to {bus['device_id']}.")
    return await apply_device_telemetry(bus["device_id"], body, from_backlog=True)


@app.get("/routes")
def list_routes():
    return list(routes.values())


@app.get("/buses")
def list_buses():
    return [bus_view(b) for b in buses.values()]


@app.get("/buses/{bus_id}")
def get_bus(bus_id: int):
    if bus_id not in buses:
        raise HTTPException(404, "Bus not found.")
    return bus_view(buses[bus_id])


def validate_stop(route_id: int, stop: str):
    if route_id not in routes or stop not in routes[route_id]["stops"]:
        raise HTTPException(400, "Unknown route or stop.")


async def assign(student: dict) -> dict:
    if student.get("bus_id") in buses:
        buses[student.pop("bus_id")]["assigned"] -= 1
    result = allocate(student)
    if result["bus"]:
        student["bus_id"] = result["bus"]["id"]
        buses[student["bus_id"]]["assigned"] += 1
        result["bus"] = bus_view(buses[student["bus_id"]])
    await hub.broadcast({"type": "buses", "data": list_buses()})
    return result


async def save_profile(user_id: str, profile: ProfileIn) -> dict:
    validate_stop(profile.route_id, profile.stop)
    data = profile.model_dump()
    await db.upsert_profile(user_id, data, stop_ids[(profile.route_id, profile.stop)])
    student = students.setdefault(user_id, {"id": user_id})
    student.update(data)
    return await assign(student)


async def require_passenger(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "passenger":
        raise HTTPException(403, "Passengers only.")
    if user["id"] not in students:
        stored = await db.get_profile(user["id"])
        if stored:
            students[user["id"]] = stored
            await assign(stored)
            return stored
        # First visit after signup: the profile is still only in Supabase user_metadata.
        try:
            profile = ProfileIn.model_validate(user["user_metadata"])
            validate_stop(profile.route_id, profile.stop)
        except (ValidationError, HTTPException):
            raise HTTPException(409, "profile_required")
        await save_profile(user["id"], profile)
    return students[user["id"]]


def require_bus_staff(bus_id: int, user: dict = Depends(current_user)) -> dict:
    if user["role"] not in {"driver", "conductor"}:
        raise HTTPException(403, "Drivers and conductors only.")
    if user["bus_id"] != bus_id:
        raise HTTPException(403, f"You are assigned to Bus {user['bus_id']}, not Bus {bus_id}.")
    return user


@app.get("/me")
async def me(user: dict = Depends(current_user)):
    public = {k: user[k] for k in ("id", "email", "role", "name", "bus_id")}
    if user["role"] == "passenger":
        try:
            profile = await require_passenger(user)
        except HTTPException:
            profile = None
        return {**public, "profile": profile}
    if user["bus_id"] not in buses:
        raise HTTPException(403, "Your staff account has no valid bus. Ask the transport office.")
    return {**public, "bus": bus_view(buses[user["bus_id"]])}


@app.get("/me/bus")
def my_bus(student: dict = Depends(require_passenger)):
    return allocate_view(student)


@app.put("/me/profile")
async def update_profile(body: ProfileIn, user: dict = Depends(current_user)):
    if user["role"] != "passenger":
        raise HTTPException(403, "Passengers only.")
    return await save_profile(user["id"], body)


def allocate_view(student: dict) -> dict:
    bus_id = student.get("bus_id")
    if bus_id is None:
        return allocate(student)
    if buses[bus_id]["status"] == "departed":
        result = allocate(student)
        if result["bus"]:
            result["alert"] = f"Bus {bus_id} has left. Next: Bus {result['bus']['id']}"
        return result
    alert = None
    if seats_free(buses[bus_id]) == 0:
        spare = next((b for b in candidate_buses(student["route_id"], student["stop"])
                      if b["id"] != bus_id and seats_free(b) > 0), None)
        alert = f"Bus {bus_id} is full right now" + (f", take Bus {spare['id']}" if spare else "")
    return {"bus": bus_view(buses[bus_id]), "alert": alert}


@app.post("/buses/{bus_id}/count")
async def report_count(bus_id: int, body: CountIn):
    bus = buses.get(bus_id) or _404()
    bus["count"] = body.count
    bus["seat_cam"] = {"free": body.seats_free, "total": body.seats_total} if body.seats_total else None
    await persist(bus)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    if seats_free(bus) == 0:
        await hub.broadcast({"type": "alert", "bus_id": bus_id, "message": f"Bus {bus_id} is full"})
    return bus_view(bus)


@app.post("/buses/{bus_id}/location")
async def report_location(bus_id: int, body: LocationIn, _: dict = Depends(require_bus_staff)):
    bus = buses.get(bus_id) or _404()
    bus["location"] = body.model_dump()
    await persist(bus)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return bus_view(bus)


@app.post("/buses/{bus_id}/status")
async def report_status(bus_id: int, body: StatusIn, _: dict = Depends(require_bus_staff)):
    if body.status not in {"scheduled", "boarding", "departed", "full"}:
        raise HTTPException(400, "status must be scheduled|boarding|departed|full")
    bus = buses.get(bus_id) or _404()
    bus["status"] = body.status
    if body.status == "departed":
        bus["trip_id"] = await db.open_trip(bus)
    elif body.status == "scheduled" and bus.get("trip_id"):
        await db.close_trip(bus_id)
        bus["trip_id"] = None
    await persist(bus, telemetry=False)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return bus_view(bus)


@app.get("/buses/{bus_id}/passengers")
def bus_passengers(bus_id: int, _: dict = Depends(require_bus_staff)):
    stops = buses[bus_id]["stops"]
    riders = [{"id": s["student_id"], "name": s["name"], "stop": s["stop"]} for s in students.values()
              if s.get("bus_id") == bus_id]
    return sorted(riders, key=lambda r: (stops.index(r["stop"]), r["name"]))


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
    raise HTTPException(404, "Bus not found.")
