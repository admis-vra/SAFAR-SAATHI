import asyncio
import hashlib
import logging
import math
import os
import random
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Body, Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError, model_validator

import db
import fusion
import ingest
import queueing
import stop_eta
from auth import current_user
from eta import predict_delay_minutes
from geofence import BusFence, Zone, distance_m

log = logging.getLogger("safarsaathi")

EXTRA_TRIP_THRESHOLD = 30
OFFLINE_AFTER_S = 30
OFFLINE_CHECK_EVERY_S = 5
LATE_AFTER_S = 120  # data older than this updates history but never sends live alerts
QUEUE_CHECK_EVERY_S = 5
MAX_WALK_M = 2000       # farther than this isn't a fallback, it's a different trip
FALLBACK_OPTIONS = 3

routes: dict[int, dict] = {}
stop_ids: dict[tuple[int, str], int] = {}
buses: dict[int, dict] = {}
students: dict[str, dict] = {}
exam_demand: list[dict] = []
depot: dict = {}


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
    for bus_id, incident_id in (await db.open_sos_incidents()).items():
        buses[bus_id]["sos_incident"] = incident_id
    depot.clear(); depot.update(await db.load_depot())
    for bus in buses.values():
        bus["fence"] = build_fence(bus, depot)
        bus["fence_ts"] = bus.get("last_device_ts")
    students.clear()
    exam_demand[:] = _demo_exam_demand()
    for p in await db.all_profiles():
        students[p["id"]] = p
        result = allocate(p)
        if result["bus"]:
            p["bus_id"] = result["bus"]["id"]
            buses[p["bus_id"]]["assigned"] += 1


def build_fence(bus: dict, depot: dict) -> BusFence:
    points = routes[bus["route_id"]]["stop_points"]
    zones = [Zone("depot", "depot", depot["name"], depot["lat"], depot["lng"], depot["radius"])]
    # Stops are ordered A (far end) -> G (near campus); A is where an outbound trip ends.
    zones += [Zone(f"stop:{p['id']}", "stop", p["name"], p["lat"], p["lng"], p["geofence_m"],
                   stop_id=p["id"], terminal=(i == 0)) for i, p in enumerate(points)]
    fence = BusFence(zones)
    if bus["location"]:
        fence.seed(bus["location"]["lat"], bus["location"]["lng"])
    return fence


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
    watchers = [asyncio.create_task(watch_offline()), asyncio.create_task(watch_queues())]
    yield
    for w in watchers:
        w.cancel()
        with suppress(asyncio.CancelledError):
            await w
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
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    speed_kmh: float | None = Field(default=None, ge=0, le=150)
    accuracy_m: float | None = Field(default=None, ge=0)


PHONE_MAX_ACCURACY_M = 100  # coarser fixes (Wi-Fi / cell towers) would make the bus jump around
DEVICE_GPS_PRIORITY_S = 30  # a fresh fix from the bus's own GPS device wins over a phone
LIVE_LOCATION_S = 120       # a position older than this is "last seen", not live


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
    f = fusion.compute(bus, time.monotonic())
    if f.mode == "cv":
        return bus["capacity"] - f.visual_empty
    if f.mode in ("ir", "ir_only"):
        return fusion.ir_people(bus)
    return bus["count"] if bus["count"] is not None else bus["assigned"]


def seat_source(bus: dict) -> str:
    mode = fusion.compute(bus, time.monotonic()).mode
    if mode in ("cv", "ir"):
        return "fused"
    if mode == "ir_only":
        return "ir_count"
    cam = bus["seat_cam"]
    if cam:
        return "camera" if cam["total"] >= bus["capacity"] else "camera_partial"
    return "people_count" if bus["count"] is not None else "allocations"


def seats_free(bus: dict) -> int:
    f = fusion.compute(bus, time.monotonic())
    if f.mode != "none":
        return f.seats_free
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
    f = fusion.compute(bus, time.monotonic())
    return {
        "id": bus["id"], "route_id": bus["route_id"], "gate": bus["gate"], "stops": bus["stops"],
        "capacity": bus["capacity"], "occupancy": occupancy(bus), "seats_free": seats_free(bus),
        "seat_source": seat_source(bus), "seats_seen": bus["seat_cam"]["total"] if bus["seat_cam"] else None,
        "status": bus["status"], "location": bus["location"], "speed_kmh": bus.get("speed_kmh"),
        "location_source": bus.get("location_source"),
        "location_at": bus["location_at"].isoformat(timespec="seconds") if bus.get("location_at") else None,
        "eta": eta(bus), "updated_at": bus["updated_at"],
        "device": {
            "id": bus.get("device_id"),
            "online": bus.get("last_seen") is not None and not bus.get("offline_incident"),
            "last_reading": bus["last_device_ts"].isoformat() if bus.get("last_device_ts") else None,
            "ir_net": bus.get("ir_net"),
        },
        "trip": {
            "id": bus.get("trip_id"),
            "in_depot": "depot" in bus["fence"].inside if bus.get("fence") else None,
            "at_stop": next((z.name for z in bus["fence"].zones if z.kind == "stop" and z.key in bus["fence"].inside),
                            None) if bus.get("fence") else None,
        },
        "sos": bool(bus.get("sos_incident")),
        "fusion": {
            "mode": f.mode, "ir_empty": f.expected_empty, "camera_empty": f.visual_empty,
            "deviation": f.deviation, "ir_offset": bus.get("ir_offset", 0), "stationary_s": round(f.stationary_s),
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

    await handle_device_sos(bus, fresh)
    trip_changed = await run_geofence(bus, fresh)

    # Only a reading newer than what we already show may move the live state (late/out-of-order data is
    # history only).
    latest = fresh[-1] if fresh else None
    live = latest is not None and (bus.get("last_device_ts") is None or latest.ts > bus["last_device_ts"])
    if trip_changed and not live:
        await db.save_live_state(bus)
        await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    if live:
        bus["last_device_ts"] = latest.ts
        if latest.has_fix:
            bus["location"] = {"lat": latest.gps.lat, "lng": latest.gps.lon}
            bus["location_source"], bus["location_at"] = "device", datetime.now(timezone.utc)
        bus["speed_kmh"] = latest.gps.speed
        if latest.gps.speed < fusion.STATIONARY_KMH:
            bus["stationary_since"] = bus.get("stationary_since") or latest.ts
        else:
            bus["stationary_since"] = None
        bus["ir_net"] = latest.pax.net_current
        bus["ir_seen"] = time.monotonic()
        bus["occupancy_seen_at"] = datetime.now(timezone.utc)
        await maybe_recalibrate(bus)
        bus["count"] = fusion.ir_people(bus)
        bus["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        bus["fused_seats_free"] = seats_free(bus)
        await db.save_live_state(bus)
        await db.set_fused_seats(bus["id"], latest.ts, bus["fused_seats_free"])
        await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return {"accepted": len(fresh), "duplicates": len(items) - len(fresh), "live_updated": live}


def is_late(ts: datetime) -> bool:
    return (datetime.now(timezone.utc) - ts).total_seconds() > LATE_AFTER_S


async def maybe_recalibrate(bus: dict):
    before = bus.get("ir_offset", 0)
    f = fusion.compute(bus, time.monotonic())
    new_offset = fusion.recalibrate(bus, time.monotonic())
    if new_offset is not None and new_offset != before:
        await db.insert_trip_event(
            bus["id"], bus.get("trip_id"), "fusion_recalibrated", datetime.now(timezone.utc),
            detail=f"IR said {f.expected_empty} empty, camera {f.visual_empty}; IR offset {before} -> {new_offset}")


def is_last_bus(bus: dict) -> bool:
    """Last bus for at least one of the stops it serves (a route can split its stops across buses)."""
    return any(last_bus_of(bus["route_id"], stop)["id"] == bus["id"] for stop in bus["stops"])


async def start_trip(bus: dict, notify: bool = True):
    bus["trip_id"] = await db.open_trip(bus)
    bus["status"] = "departed"
    bus["last_trip_start"] = queueing.now_ist().astimezone(timezone.utc)  # same clock as the queue window
    if await db.close_queue(bus["id"], queueing.service_day(queueing.now_ist())):
        await hub.broadcast({"type": "queue", "bus_id": bus["id"]})
    if notify and is_last_bus(bus):
        # Students on this route who aren't aboard can now ask /me/fallback for another way home.
        await hub.broadcast({"type": "last_bus_left", "route_id": bus["route_id"], "bus_id": bus["id"]})


async def end_trip(bus: dict):
    await db.close_trip(bus["id"])
    bus["trip_id"] = None
    bus["status"] = "scheduled"
    # Reset the IR baseline at the END of a trip, not the start: a trip "starts" only when the bus leaves
    # campus, i.e. after boarding, which is exactly when the camera calibration is best.
    bus["ir_offset"] = 0


async def run_geofence(bus: dict, readings: list[ingest.Telemetry]) -> bool:
    """Feed readings (oldest first) through the bus's fences; returns True if a trip started or ended."""
    changed = False
    for r in readings:
        if bus.get("fence_ts") and r.ts < bus["fence_ts"]:
            continue  # out of order: already past this point
        bus["fence_ts"] = r.ts
        if not r.has_fix:
            continue
        late = is_late(r.ts)
        for action, zone in bus["fence"].update(r.gps.lat, r.gps.lon):
            kind = f"{zone.kind}_{action}"
            if kind == "depot_exit" and not bus.get("trip_id"):
                await start_trip(bus, notify=not late)
                changed = True
            await db.insert_trip_event(bus["id"], bus.get("trip_id"), kind, r.ts, zone.stop_id, late)
            ends_trip = kind == "depot_enter" or (kind == "stop_enter" and zone.terminal)
            if ends_trip and bus.get("trip_id"):
                await end_trip(bus)
                changed = True
            if not late:
                await hub.broadcast({"type": "trip_event", "bus_id": bus["id"], "kind": kind,
                                     "stop": zone.name if zone.kind == "stop" else None, "ts": r.ts.isoformat()})
    return changed


async def raise_sos(bus: dict, lat: float | None, lng: float | None, late: bool, detail: str) -> int:
    if bus.get("sos_incident"):
        return bus["sos_incident"]
    bus["sos_incident"] = await db.open_sos(bus["id"], bus.get("trip_id"), lat, lng, late, detail)
    # SOS is always pushed, even from late data: a safety alarm that arrives late is still an alarm.
    await hub.broadcast({"type": "incident", "id": bus["sos_incident"], "bus_id": bus["id"], "kind": "sos",
                         "open": True, "late": late, "detail": detail,
                         "location": {"lat": lat, "lng": lng} if lat is not None else None})
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return bus["sos_incident"]


async def handle_device_sos(bus: dict, readings: list[ingest.Telemetry]):
    for r in readings:
        if r.sos_active and not bus.get("sos_incident"):
            loc = (r.gps.lat, r.gps.lon) if r.has_fix else (
                (bus["location"]["lat"], bus["location"]["lng"]) if bus["location"] else (None, None))
            late = is_late(r.ts)
            await raise_sos(bus, *loc, late, "SOS button on bus device" + (" (reported late)" if late else ""))
            return


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


async def refill_calls(bus: dict) -> list[dict]:
    """While a bus is boarding, call as many waiting students as there are free seats not yet promised."""
    if bus["status"] != "boarding":
        return []
    day = queueing.service_day(queueing.now_ist())
    room = queueing.room_to_call(seats_free(bus),
                                 await db.count_promised(bus["id"], day, bus.get("occupancy_seen_at")))
    called = await db.call_next(bus["id"], day, room)
    for t in called:
        await hub.broadcast({"type": "token_called", "bus_id": bus["id"], "token_id": t["id"],
                             "position": t["queue_position"], "board_within_s": queueing.CALL_WINDOW_S})
    if called:
        await hub.broadcast({"type": "queue", "bus_id": bus["id"]})
    return called


async def service_queues():
    expired = await db.expire_tokens(queueing.CALL_WINDOW_S, queueing.service_day(queueing.now_ist()))
    for bus_id in {t["bus_id"] for t in expired}:
        await hub.broadcast({"type": "queue", "bus_id": bus_id})
    for bus in buses.values():
        await refill_calls(bus)


async def watch_queues():
    while True:
        await asyncio.sleep(QUEUE_CHECK_EVERY_S)
        try:
            await service_queues()
        except Exception:
            log.exception("queue service failed")


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
    if user["role"] == "dispatcher":
        return public
    if user["bus_id"] not in buses:
        raise HTTPException(403, "Your staff account has no valid bus. Ask the transport office.")
    return {**public, "bus": bus_view(buses[user["bus_id"]])}


@app.get("/me/bus")
async def my_bus(student: dict = Depends(require_passenger)):
    token = await db.student_token(student["id"], queueing.service_day(queueing.now_ist()))
    aboard = token is not None and token["status"] == "boarded" and token["bus_id"] in buses
    if aboard:
        # On board: keep showing this bus (don't reassign them to "the next bus") with arrival at their stop.
        result = {"bus": bus_view(buses[token["bus_id"]]), "alert": None, "aboard": True}
    else:
        result = {**allocate_view(student), "aboard": False}
    if result["bus"]:
        result["my_stop_eta"] = my_stop_eta(buses[result["bus"]["id"]], student["stop"])
    return result


def my_stop_eta(bus: dict, stop: str) -> dict | None:
    points = routes[bus["route_id"]]["stop_points"]
    path = stop_eta.route_path((depot["lat"], depot["lng"]), points)
    names = [p["name"] for p in reversed(points)]
    if stop not in names:
        return None
    now = queueing.now_ist()
    loc = bus["location"]
    fresh = bus.get("location_at") and 0 <= (now - bus["location_at"]).total_seconds() < LIVE_LOCATION_S
    in_depot = bus.get("fence") is not None and "depot" in bus["fence"].inside
    if bus["status"] != "departed" and fresh and not in_depot:
        # Out on the road but not on a trip from campus (e.g. the morning run in). Stop ETAs only model
        # trips leaving campus, so give the honest distance instead of a made-up time.
        stop_pt = path[1 + names.index(stop)]
        return {"basis": "on_road", "passed": False, "minutes": None, "at": None,
                "distance_m": round(distance_m(loc["lat"], loc["lng"], *stop_pt))}
    sched = datetime.combine(now.date(), datetime.strptime(eta(bus)["predicted"], "%H:%M").time(), tzinfo=queueing.IST)
    return stop_eta.stop_eta(path, 1 + names.index(stop), (loc["lat"], loc["lng"]) if loc else None,
                             bus.get("speed_kmh"), bus["status"] == "departed", sched, now)


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
    bus["count"] = bus["cv_count"] = body.count
    bus["seat_cam"] = {"free": body.seats_free, "total": body.seats_total} if body.seats_total else None
    bus["cv_seen"] = time.monotonic()
    bus["occupancy_seen_at"] = datetime.now(timezone.utc)
    await maybe_recalibrate(bus)
    bus["fused_seats_free"] = seats_free(bus)
    await persist(bus)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    if seats_free(bus) == 0:
        await hub.broadcast({"type": "alert", "bus_id": bus_id, "message": f"Bus {bus_id} is full"})
    return bus_view(bus)


@dataclass
class _Fix:
    """A phone GPS fix shaped like the device readings run_geofence expects."""
    ts: datetime
    gps: SimpleNamespace
    has_fix: bool = True


@app.post("/buses/{bus_id}/location")
async def report_location(bus_id: int, body: LocationIn, _: dict = Depends(require_bus_staff)):
    """Driver's phone as the bus GPS: moves the bus, starts and ends trips through the same geofences."""
    bus = buses.get(bus_id) or _404()
    now = datetime.now(timezone.utc)

    if body.accuracy_m is not None and body.accuracy_m > PHONE_MAX_ACCURACY_M:
        return {"accepted": False, "reason": f"GPS too rough ({round(body.accuracy_m)} m). Waiting for a better fix."}
    if bus.get("location_source") == "device" and (now - bus["location_at"]).total_seconds() < DEVICE_GPS_PRIORITY_S:
        return {"accepted": False, "reason": "The bus's own GPS device is reporting; it takes priority."}

    speed = body.speed_kmh
    prev = bus["location"]
    if speed is None and prev and bus.get("location_source") == "phone" and bus.get("location_at"):
        secs = (now - bus["location_at"]).total_seconds()
        if 0 < secs < 120:
            speed = round(distance_m(prev["lat"], prev["lng"], body.lat, body.lng) / secs * 3.6, 1)
    bus["location"] = {"lat": body.lat, "lng": body.lng}
    bus["location_source"], bus["location_at"] = "phone", now
    bus["speed_kmh"] = speed
    await run_geofence(bus, [_Fix(now, SimpleNamespace(lat=body.lat, lon=body.lng))])
    await persist(bus)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return {"accepted": True, "bus": bus_view(bus)}


@app.post("/buses/{bus_id}/status")
async def report_status(bus_id: int, body: StatusIn, user: dict = Depends(require_bus_staff)):
    """Manual override; normally the geofence sets departed/scheduled on its own."""
    if body.status not in {"scheduled", "boarding", "departed", "full"}:
        raise HTTPException(400, "status must be scheduled|boarding|departed|full")
    bus = buses.get(bus_id) or _404()
    if body.status == "departed" and not bus.get("trip_id"):
        await start_trip(bus)
    elif body.status == "scheduled" and bus.get("trip_id"):
        await end_trip(bus)
    bus["status"] = body.status
    await db.insert_trip_event(bus_id, bus.get("trip_id"), "manual_status", datetime.now(timezone.utc),
                               detail=f"{body.status} by {user['name']} ({user['role']})")
    await persist(bus, telemetry=False)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    await refill_calls(bus)
    return bus_view(bus)


@app.post("/buses/{bus_id}/sos")
async def driver_sos(bus_id: int, user: dict = Depends(require_bus_staff)):
    bus = buses.get(bus_id) or _404()
    loc = bus["location"] or {}
    incident_id = await raise_sos(bus, loc.get("lat"), loc.get("lng"), False,
                                  f"SOS pressed by {user['name']} ({user['role']})")
    return {"incident_id": incident_id}


def require_dispatcher(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "dispatcher":
        raise HTTPException(403, "Dispatchers only.")
    return user


def require_bus_staff_or_dispatcher(bus_id: int, user: dict = Depends(current_user)) -> dict:
    if user["role"] == "dispatcher":
        return user
    return require_bus_staff(bus_id, user)


# ---------- digital queue (afternoon departures) ----------

def queue_info(bus: dict) -> dict:
    w = queueing.window(bus["departure"], queueing.now_ist(), bus.get("last_trip_start"))
    return {"bus_id": bus["id"], "state": w.state, "opens_at": w.opens_at.strftime("%H:%M"),
            "departs_at": w.departs_at.strftime("%H:%M"), "bus_status": bus["status"]}


@app.get("/me/queue")
async def my_queue(student: dict = Depends(require_passenger)):
    bus = buses.get(student.get("bus_id"))
    token = await db.student_token(student["id"], queueing.service_day(queueing.now_ist()))
    if token:
        token["created_at"] = token["created_at"].isoformat()
        token["called_at"] = token["called_at"].isoformat() if token["called_at"] else None
        token["likely_seat"] = token["status"] == "called" or (
            token["status"] == "waiting" and bus is not None and token["ahead"] < seats_free(bus))
    return {"queue": queue_info(bus) if bus else None, "token": token}


@app.post("/me/queue")
async def join_my_queue(student: dict = Depends(require_passenger)):
    bus = buses.get(student.get("bus_id"))
    if bus is None:
        raise HTTPException(409, "You have no bus assigned to join a queue for.")
    info = queue_info(bus)
    if info["state"] == "not_open":
        raise HTTPException(409, f"The queue for Bus {bus['id']} opens at {info['opens_at']}.")
    if info["state"] == "closed":
        raise HTTPException(409, f"Bus {bus['id']} has already left today.")
    try:
        token = await db.join_queue(student["id"], bus["id"], queueing.service_day(queueing.now_ist()))
    except db.AlreadyQueued:
        raise HTTPException(409, "You are already in a queue. Cancel it first to join another.")
    await hub.broadcast({"type": "queue", "bus_id": bus["id"]})
    await refill_calls(bus)
    return await my_queue(student)


@app.delete("/me/queue")
async def leave_my_queue(student: dict = Depends(require_passenger)):
    token = await db.cancel_token(student["id"])
    if token is None:
        raise HTTPException(404, "You are not in a queue.")
    await hub.broadcast({"type": "queue", "bus_id": token["bus_id"]})
    if token["bus_id"] in buses:
        await refill_calls(buses[token["bus_id"]])
    return {"cancelled": token["id"]}


@app.get("/buses/{bus_id}/queue")
async def bus_queue(bus_id: int, _: dict = Depends(require_bus_staff_or_dispatcher)):
    bus = buses.get(bus_id) or _404()
    tokens = await db.queue_board(bus_id, queueing.service_day(queueing.now_ist()))
    columns = {s: [t for t in tokens if t["status"] == s]
               for s in ("waiting", "called", "boarded", "no_show", "expired", "cancelled")}
    return {"queue": queue_info(bus), "seats_free": seats_free(bus), "columns": columns}


@app.post("/buses/{bus_id}/queue/{token_id}/{outcome}")
async def mark_queue_token(bus_id: int, token_id: int, outcome: str, _: dict = Depends(require_bus_staff)):
    if outcome not in ("boarded", "no_show"):
        raise HTTPException(400, "outcome must be boarded or no_show")
    if not await db.mark_token(bus_id, token_id, outcome, datetime.now(timezone.utc)):
        raise HTTPException(404, "No called token with that id on this bus.")
    await hub.broadcast({"type": "queue", "bus_id": bus_id})
    await refill_calls(buses[bus_id])
    return {"token_id": token_id, "status": outcome}


@app.get("/incidents")
async def incidents(open: bool = True, _: dict = Depends(require_dispatcher)):
    return await db.list_incidents(only_open=open)


@app.post("/incidents/{incident_id}/ack")
async def acknowledge(incident_id: int, user: dict = Depends(require_dispatcher)):
    row = await db.acknowledge_incident(incident_id, user["id"])
    if row is None:
        raise HTTPException(404, "No such open incident (already acknowledged?).")
    bus = buses.get(row["bus_id"])
    if bus and row["kind"] == "sos" and bus.get("sos_incident") == incident_id:
        bus.pop("sos_incident")
    if bus and row["kind"] == "device_offline" and bus.get("offline_incident") == incident_id:
        bus.pop("offline_incident")
    await hub.broadcast({"type": "incident", "id": incident_id, "bus_id": row["bus_id"], "kind": row["kind"],
                         "open": False, "acknowledged_by": user["name"]})
    if bus:
        await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return {"acknowledged": incident_id}


# ---------- last-bus fallback ----------

class HomeIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


def bus_has_left(bus: dict) -> bool:
    return bus["status"] == "departed" or queueing.window(
        bus["departure"], queueing.now_ist(), bus.get("last_trip_start")).state == "closed"


def last_bus_of(route_id: int, stop: str) -> dict:
    """The latest bus on this route that actually serves this stop."""
    return max((b for b in buses.values() if b["route_id"] == route_id and stop in b["stops"]),
               key=lambda b: (b["departure"], b["id"]))


def fallback_options(candidates: list[dict]) -> list[dict]:
    """Nearest-first candidates -> buses that haven't left, have a free seat and are within walking distance."""
    options, seen = [], set()
    for c in candidates:
        bus = buses.get(c["bus_id"])
        if bus is None or c["bus_id"] in seen or c["walk_m"] > MAX_WALK_M:
            continue
        if bus_has_left(bus) or seats_free(bus) <= 0:
            continue
        seen.add(c["bus_id"])
        options.append({"route_id": c["route_id"], "route": c["route_name"], "stop": c["stop_name"],
                        "walk_m": round(c["walk_m"]), "walk_min": math.ceil(c["walk_m"] / 75),  # ~4.5 km/h
                        "bus_id": bus["id"], "gate": bus["gate"], "departs": eta(bus)["predicted"],
                        "seats_free": seats_free(bus)})
        if len(options) == FALLBACK_OPTIONS:
            break
    return options


@app.get("/me/fallback")
async def my_fallback(lat: float | None = None, lng: float | None = None,
                      student: dict = Depends(require_passenger)):
    """If the last bus on your route has left: up to 3 buses on other routes you can still walk to.
    lat/lng (optional) is where you are now; it is used for this answer only and not stored."""
    last = last_bus_of(student["route_id"], student["stop"])
    if not bus_has_left(last):
        return {"needed": False, "last_bus": {"bus_id": last["id"], "departs": eta(last)["predicted"]}}
    options = fallback_options(await db.find_fallback_route(student["id"], lat, lng))
    return {
        "needed": True,
        "last_bus": {"bus_id": last["id"], "left": True},
        "options": options,
        "message": None if options else
        "No other bus within walking distance has seats right now. Please contact the transport office.",
    }


@app.put("/me/home")
async def set_my_home(body: HomeIn, student: dict = Depends(require_passenger)):
    # Coarse on purpose (~100 m): enough to find a nearby stop, not an exact address.
    await db.set_home(student["id"], round(body.lat, 3), round(body.lng, 3))
    student["has_home"] = True
    return {"home": {"lat": round(body.lat, 3), "lng": round(body.lng, 3)}}


@app.delete("/me/home")
async def clear_my_home(student: dict = Depends(require_passenger)):
    await db.set_home(student["id"], None, None)
    student["has_home"] = False
    return {"home": None}


# ---------- announcements (control room -> riders) ----------

class AnnouncementIn(BaseModel):
    kind: str = Field(pattern="^(delay|cancellation|info)$")
    message: str = Field(min_length=3, max_length=280)
    route_id: int | None = None
    bus_id: int | None = None
    minutes: int = Field(default=60, ge=5, le=720)


@app.get("/announcements")
async def announcements(route_id: int | None = None):
    return await db.active_announcements(route_id)


@app.post("/announcements")
async def announce(body: AnnouncementIn, user: dict = Depends(require_dispatcher)):
    if body.route_id is not None and body.route_id not in routes:
        raise HTTPException(400, "Unknown route.")
    if body.bus_id is not None and body.bus_id not in buses:
        raise HTTPException(400, "Unknown bus.")
    ann = await db.create_announcement(body.route_id, body.bus_id, body.kind, body.message.strip(), user["id"],
                                       body.minutes)
    await hub.broadcast({"type": "announcement", "data": ann})
    return ann


@app.delete("/announcements/{ann_id}")
async def end_announcement(ann_id: int, _: dict = Depends(require_dispatcher)):
    if not await db.expire_announcement(ann_id):
        raise HTTPException(404, "No active announcement with that id.")
    await hub.broadcast({"type": "announcement", "ended": ann_id})
    return {"ended": ann_id}


# ---------- crowd forecast, ride history ----------

@app.get("/buses/{bus_id}/forecast")
async def forecast(bus_id: int):
    bus = buses.get(bus_id) or _404()
    f = await db.bus_forecast(bus_id)
    enough = f["trips"] >= 3
    out = {**f, "enough_data": enough, "label": None}
    if enough:
        share = f["avg_people"] / bus["capacity"]
        out["typical_seats_free"] = max(round(bus["capacity"] - f["avg_people"]), 0)
        out["label"] = ("Usually full when it leaves" if f["full_share"] >= 0.5 else
                        "Usually fills up" if share >= 0.85 else
                        "Usually has seats" if share < 0.6 else "Usually busy")
    return out


@app.get("/me/rides")
async def my_rides(student: dict = Depends(require_passenger)):
    return await db.rides_for(student["id"])


# ---------- analytics (transport office) ----------

@app.get("/analytics")
async def analytics(days: int = 7, _: dict = Depends(require_dispatcher)):
    if not 1 <= days <= 90:
        raise HTTPException(400, "days must be 1-90")
    return await db.analytics(days)


# ---------- issue reports ----------

class IssueIn(BaseModel):
    category: str = Field(pattern="^(overcrowding|driving|cleanliness|breakdown|safety|staff|other)$")
    description: str = Field(min_length=5, max_length=1000)
    bus_id: int | None = None


class IssueUpdate(BaseModel):
    status: str = Field(pattern="^(open|in_progress|resolved)$")
    note: str | None = Field(default=None, max_length=500)


@app.post("/issues")
async def report_issue(body: IssueIn, user: dict = Depends(current_user)):
    bus_id = body.bus_id
    if bus_id is None:
        bus_id = user.get("bus_id") or students.get(user["id"], {}).get("bus_id")
    if bus_id is not None and bus_id not in buses:
        raise HTTPException(400, "Unknown bus.")
    issue = await db.create_issue(user["id"], user["role"], bus_id, body.category, body.description.strip())
    await hub.broadcast({"type": "issue", "id": issue["id"]})
    return issue


@app.get("/me/issues")
async def my_issues(user: dict = Depends(current_user)):
    return await db.issues_for(user["id"])


@app.get("/issues")
async def list_issues(status: str | None = None, _: dict = Depends(require_dispatcher)):
    return await db.all_issues(status)


@app.patch("/issues/{issue_id}")
async def set_issue_status(issue_id: int, body: IssueUpdate, _: dict = Depends(require_dispatcher)):
    row = await db.update_issue(issue_id, body.status, body.note)
    if row is None:
        raise HTTPException(404, "No such issue.")
    await hub.broadcast({"type": "issue", "id": issue_id})
    return row


# ---------- admin: timetable and stops (no SQL needed) ----------

class BusEdit(BaseModel):
    departure: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    gate: str = Field(min_length=1, max_length=30)
    capacity: int = Field(ge=10, le=80)
    stops: list[str] = Field(min_length=1)


class StopEdit(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    geofence_m: int = Field(ge=20, le=300)


@app.put("/admin/buses/{bus_id}")
async def edit_bus(bus_id: int, body: BusEdit, _: dict = Depends(require_dispatcher)):
    bus = buses.get(bus_id) or _404()
    route_stops = routes[bus["route_id"]]["stops"]
    if any(s not in route_stops for s in body.stops):
        raise HTTPException(400, f"Stops must be from this route: {', '.join(route_stops)}.")
    ordered = [s for s in route_stops if s in body.stops]
    await db.update_bus(bus_id, body.departure, body.gate.strip(), body.capacity, ordered)
    bus.update(departure=body.departure, gate=body.gate.strip(), capacity=body.capacity, stops=ordered)
    await hub.broadcast({"type": "bus", "data": bus_view(bus)})
    return bus_view(bus)


@app.put("/admin/stops/{stop_id}")
async def edit_stop(stop_id: int, body: StopEdit, _: dict = Depends(require_dispatcher)):
    route = next((r for r in routes.values() if any(p["id"] == stop_id for p in r["stop_points"])), None)
    if route is None:
        raise HTTPException(404, "Stop not found.")
    await db.update_stop(stop_id, body.lat, body.lng, body.geofence_m)
    point = next(p for p in route["stop_points"] if p["id"] == stop_id)
    point.update(lat=body.lat, lng=body.lng, geofence_m=body.geofence_m)
    for bus in buses.values():
        if bus["route_id"] == route["id"]:
            bus["fence"] = build_fence(bus, depot)  # geofences follow the corrected stop position
    await hub.broadcast({"type": "routes"})
    return point


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
