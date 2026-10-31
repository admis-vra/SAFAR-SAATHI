import hashlib
import time
import uuid

import pytest

from conftest import execute, fetch, token
from test_m2_ingest import BUS as BUS11, TOKEN, payload, window_start
from test_m3_geofence import route4_points, trip_path
from test_m5_queue import clock, make_students, needs_students  # noqa: F401  (fixture re-exports)


def stop_point(route_id, name):
    r = fetch("select st_y(location::geometry) as lat, st_x(location::geometry) as lng from stops "
              "where route_id = $1 and stop_name = $2", route_id, name)[0]
    return r["lat"], r["lng"]


def candidates(exclude_route, lat, lng):
    return [dict(r) for r in fetch(
        "select * from fallback_candidates($1, $2::text::geography)", exclude_route, f"SRID=4326;POINT({lng} {lat})")]


@pytest.fixture
def recorder(monkeypatch):
    import main
    sent = []
    real = main.hub.broadcast

    async def record(msg):
        sent.append(msg)
        await real(msg)

    monkeypatch.setattr(main.hub, "broadcast", record)
    return sent


def staff(bus_id):
    return {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "driver", "bus_id": bus_id, "name": "D"})}


# ---------- geography (PostGIS) ----------

def test_candidates_nearest_first_and_never_own_route():
    lat, lng = stop_point(2, "C")
    rows = candidates(3, lat, lng)
    assert rows[0]["route_id"] == 2 and rows[0]["stop_name"] == "C" and rows[0]["walk_m"] < 1
    assert all(r["route_id"] != 3 for r in rows)
    assert [r["walk_m"] for r in rows] == sorted(r["walk_m"] for r in rows)
    assert {(r["stop_name"], r["bus_id"]) for r in rows if r["route_id"] == 2} >= {("C", 5), ("D", 6)}


# ---------- live filtering ----------

def test_options_skip_full_departed_far_and_duplicate_buses(client_factory):
    import main
    lat, lng = stop_point(2, "C")
    with client_factory() as c:
        rows = candidates(3, lat, lng)
        assert [o["bus_id"] for o in main.fallback_options(rows)][:2] == [5, 6]

        main.buses[5]["count"], main.buses[5]["seat_cam"] = 40, None      # full
        opts = main.fallback_options(rows)
        assert 5 not in [o["bus_id"] for o in opts] and opts[0]["bus_id"] == 6
        assert opts[0]["stop"] == "D" and 250 < opts[0]["walk_m"] < 400 and opts[0]["walk_min"] >= 4

        main.buses[6]["status"] = "departed"                                # already left
        assert 6 not in [o["bus_id"] for o in main.fallback_options(rows)]

        far = [{**r, "walk_m": main.MAX_WALK_M + 1} for r in rows]
        assert main.fallback_options(far) == []
    assert len({o["bus_id"] for o in opts}) == len(opts)


# ---------- last-bus trigger ----------

def test_only_a_stops_last_bus_triggers_the_alert(client_factory, recorder):
    with client_factory() as c:
        c.post("/buses/14/status", json={"status": "departed"}, headers=staff(14))   # D-G 13:15: 15 comes later
        assert not [m for m in recorder if m["type"] == "last_bus_left"]
        c.post("/buses/15/status", json={"status": "departed"}, headers=staff(15))   # D-G 13:20: last for D-G
        c.post("/buses/9/status", json={"status": "departed"}, headers=staff(9))     # A-C 13:15: only bus for A-C
    assert [m["bus_id"] for m in recorder if m["type"] == "last_bus_left"] == [15, 9]


@pytest.fixture
def bus11_device():
    old = fetch("select device_token_hash from buses where id = $1", BUS11)[0]["device_token_hash"]
    execute("update buses set device_token_hash = $2 where id = $1", BUS11,
            hashlib.sha256(TOKEN.encode()).hexdigest())
    execute("update bus_live_state set location = null, last_device_ts = null, status = 'scheduled' "
            "where bus_id = $1", BUS11)
    execute("update active_trips set end_time = now() where bus_id = $1 and end_time is null", BUS11)
    yield {"X-Device-Token": TOKEN}
    execute("update buses set device_token_hash = $2 where id = $1", BUS11, old)


def test_replayed_last_bus_departure_does_not_alert(client_factory, recorder, bus11_device):
    with client_factory() as c:  # bus 11 is the only (so last) bus on route 4
        stop_g, stop_a = route4_points(c)
        t0 = window_start()
        c.post("/ingest/backlog", headers=bus11_device,
               json=[payload(ts=t0 + 2 * i, lat=a, lon=b) for i, (a, b) in enumerate(trip_path(stop_g, stop_a))])
    assert not [m for m in recorder if m["type"] == "last_bus_left"]


def test_live_last_bus_departure_alerts(client_factory, recorder, bus11_device):
    with client_factory() as c:
        stop_g, stop_a = route4_points(c)
        t0 = int(time.time()) - 40
        for i, (a, b) in enumerate(trip_path(stop_g, stop_a)[:4]):  # campus x2, then 400 m out x2
            c.post("/ingest/telemetry", headers=bus11_device, json=payload(ts=t0 + 2 * i, lat=a, lon=b))
    assert [m for m in recorder if m["type"] == "last_bus_left"] == [{"type": "last_bus_left", "route_id": 4,
                                                                       "bus_id": 11}]


# ---------- student endpoint ----------

def make_route3_student(make_students, stop="E"):
    sid = make_students(1)[0]
    execute("update profiles set default_route_id = 3, default_stop_id = (select id from stops where route_id = 3 "
            "and stop_name = $2) where id = $1::uuid", sid, stop)
    return sid, {"Authorization": "Bearer " + token(sid)}


@needs_students
def test_a_later_bus_for_other_stops_does_not_strand_you(client_factory, make_students, clock):
    _, me = make_route3_student(make_students, stop="B")   # stop B is served only by Bus 9
    with client_factory() as c:
        c.post("/buses/15/status", json={"status": "departed"}, headers=staff(15))
        assert c.get("/me/fallback", headers=me).json() == {"needed": False, "last_bus": {
            "bus_id": 9, "departs": c.get("/buses/9").json()["eta"]["predicted"]}}
        c.post("/buses/9/status", json={"status": "departed"}, headers=staff(9))
        assert c.get("/me/fallback", headers=me).json()["needed"] is True


@needs_students
def test_fallback_flow(client_factory, make_students, clock):  # clock: tomorrow 13:00, nobody has left yet
    sid, me = make_route3_student(make_students)
    near_lat, near_lng = stop_point(2, "C")
    with client_factory() as c:
        assert c.get("/me/fallback", headers=me).json()["needed"] is False
        c.post("/buses/15/status", json={"status": "departed"}, headers=staff(15))

        here = c.get("/me/fallback", params={"lat": near_lat, "lng": near_lng}, headers=me).json()
        assert here["needed"] is True and here["message"] is None
        assert [o["bus_id"] for o in here["options"]][:2] == [5, 6] and here["options"][0]["walk_m"] == 0

        usual_stop = c.get("/me/fallback", headers=me).json()      # route 3 stop B: nothing walkable
        assert usual_stop["options"] == [] and "transport office" in usual_stop["message"]
    assert fetch("select home_location from profiles where id = $1::uuid", sid)[0]["home_location"] is None


@needs_students
def test_home_location_is_coarse_used_and_deletable(client_factory, make_students, clock):
    sid, me = make_route3_student(make_students)
    a_lat, a_lng = stop_point(2, "A")
    with client_factory() as c:
        saved = c.put("/me/home", json={"lat": a_lat + 0.000123, "lng": a_lng - 0.000456}, headers=me).json()
        assert saved["home"] == {"lat": round(a_lat + 0.000123, 3), "lng": round(a_lng - 0.000456, 3)}
        c.post("/buses/15/status", json={"status": "departed"}, headers=staff(15))
        opts = c.get("/me/fallback", headers=me).json()["options"]  # no lat/lng: home is used
        assert opts and opts[0]["route_id"] == 2 and opts[0]["walk_m"] < 200
        assert c.delete("/me/home", headers=me).json() == {"home": None}
    assert fetch("select home_location from profiles where id = $1::uuid", sid)[0]["home_location"] is None
