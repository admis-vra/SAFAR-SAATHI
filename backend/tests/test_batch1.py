import uuid
from datetime import datetime, timedelta, timezone

import pytest

import stop_eta
from conftest import execute, fetch, token
from test_m5_queue import BUS as QBUS, auth, clock, make_students, needs_students  # noqa: F401

CAMPUS = (30.2687, 77.9945)
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=timezone.utc)


def dispatcher():
    return {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "dispatcher", "name": "Control"})}


def driver(bus_id=14):
    return {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "driver", "bus_id": bus_id, "name": "D"})}


# ---------- ETA at my stop (pure) ----------

def straight_path():
    # campus then 3 stops 1 km apart due north
    return [CAMPUS] + [(CAMPUS[0] + k * 0.008983, CAMPUS[1]) for k in (1, 2, 3)]


def test_eta_before_departure_uses_timetable():
    e = stop_eta.stop_eta(straight_path(), 2, None, None, False, NOW + timedelta(minutes=10), NOW)
    assert e["basis"] == "timetable" and 1900 < e["distance_m"] < 2100
    assert e["minutes"] == 16  # 10 min wait + 4.8 min at 25 km/h + one 30 s stop = 15.3, rounded up


def test_eta_bus_held_at_campus_gives_no_time():
    e = stop_eta.stop_eta(straight_path(), 2, None, None, False, NOW - timedelta(minutes=25), NOW)
    assert e["basis"] == "waiting" and e["minutes"] is None and e["at"] is None and e["overdue_min"] == 25
    # a few minutes late is still a timetable estimate starting now
    assert stop_eta.stop_eta(straight_path(), 2, None, None, False, NOW - timedelta(minutes=5), NOW)["basis"] == "timetable"


def test_eta_live_between_stops():
    p = straight_path()
    halfway = ((p[1][0] + p[2][0]) / 2, CAMPUS[1])
    e = stop_eta.stop_eta(p, 3, halfway, 30, True, None, NOW)
    assert e["basis"] == "live" and not e["passed"] and 1400 < e["distance_m"] < 1600
    assert e["minutes"] == 4  # 1.5 km at 30 km/h = 3 min + one 30 s stop = 3.5, rounded up


def test_eta_passed_and_arriving():
    p = straight_path()
    beyond = (p[2][0] + 0.004, CAMPUS[1])
    assert stop_eta.stop_eta(p, 2, beyond, 30, True, None, NOW)["passed"] is True
    at_stop = (p[2][0] + 0.0003, CAMPUS[1])  # ~33 m past
    arriving = stop_eta.stop_eta(p, 2, at_stop, 5, True, None, NOW)
    assert arriving["passed"] is False and arriving["minutes"] == 0


# ---------- announcements ----------

def test_announcements_lifecycle(client_factory):
    with client_factory() as c:
        assert c.post("/announcements", json={"kind": "delay", "message": "Bus 14 running 10 min late"},
                      headers=driver()).status_code == 403
        assert c.post("/announcements", json={"kind": "meteor", "message": "x"}, headers=dispatcher()).status_code == 422
        a = c.post("/announcements", json={"kind": "delay", "message": "Route 3 buses 10 min late (traffic)",
                                           "route_id": 3, "minutes": 30}, headers=dispatcher()).json()
        everyone = c.post("/announcements", json={"kind": "info", "message": "Gate 2 closed today, use Gate 1"},
                          headers=dispatcher()).json()
        r3 = [x["id"] for x in c.get("/announcements", params={"route_id": 3}).json()]
        r1 = [x["id"] for x in c.get("/announcements", params={"route_id": 1}).json()]
        assert a["id"] in r3 and everyone["id"] in r3 and a["id"] not in r1 and everyone["id"] in r1
        assert c.delete(f"/announcements/{a['id']}", headers=dispatcher()).status_code == 200
        assert a["id"] not in [x["id"] for x in c.get("/announcements").json()]
        c.delete(f"/announcements/{everyone['id']}", headers=dispatcher())


# ---------- issues ----------

def test_issue_report_and_office_workflow(client_factory):
    me = driver(14)
    with client_factory() as c:
        assert c.post("/issues", json={"category": "breakdown", "description": "bad"}, headers=me).status_code == 422
        issue = c.post("/issues", json={"category": "breakdown", "description": "AC not working on Bus 14"},
                       headers=me).json()
        assert issue["bus_id"] == 14 and issue["status"] == "open"  # defaults to the reporter's bus
        assert issue["id"] in [i["id"] for i in c.get("/me/issues", headers=me).json()]
        assert c.get("/issues", headers=me).status_code == 403
        assert c.patch(f"/issues/{issue['id']}", json={"status": "resolved"}, headers=me).status_code == 403
        upd = c.patch(f"/issues/{issue['id']}", json={"status": "in_progress", "note": "Mechanic assigned"},
                      headers=dispatcher()).json()
        assert upd == {"id": issue["id"], "status": "in_progress", "office_note": "Mechanic assigned"}
        mine = next(i for i in c.get("/me/issues", headers=me).json() if i["id"] == issue["id"])
        assert mine["office_note"] == "Mechanic assigned"
    execute("delete from issues where id = $1", issue["id"])


# ---------- analytics ----------

def test_analytics_shape_and_access(client_factory):
    with client_factory() as c:
        assert c.get("/analytics", headers=driver()).status_code == 403
        assert c.get("/analytics", params={"days": 0}, headers=dispatcher()).status_code == 400
        a = c.get("/analytics", params={"days": 7}, headers=dispatcher()).json()
    assert [r["route_id"] for r in a["routes"]] == [1, 2, 3, 4]
    assert set(a["queue"]) == {"joined", "boarded", "no_show", "expired"} and "open_issues" in a


# ---------- crowd forecast ----------

@pytest.fixture
def three_past_trips():
    """Three trips of Bus 3 on earlier days, leaving with 40, 38 and 40 people on board."""
    trip_ids = []
    for days, people in ((3, 40), (4, 38), (5, 40)):
        start = datetime.now(timezone.utc) - timedelta(days=days)
        execute("select ensure_telemetry_partition($1::date)", start.date())
        trip_ids.append(fetch("insert into active_trips (bus_id, route_id, departure_scheduled, start_time, end_time) "
                              "values (3, 1, '13:10', $1::timestamptz, $1::timestamptz + interval '40 minutes') returning id", start)[0]["id"])
        execute("insert into telemetry (bus_id, ts, people_count) values (3, $1, $2) on conflict do nothing",
                start - timedelta(seconds=30), people)
    yield
    execute("delete from active_trips where id = any($1::bigint[])", trip_ids)


def test_forecast_needs_enough_data_and_labels_it(client_factory, three_past_trips):
    with client_factory() as c:
        f = c.get("/buses/3/forecast").json()
        none = c.get("/buses/6/forecast").json()
    assert f["trips"] >= 3 and f["enough_data"] and f["label"] == "Usually full when it leaves"
    assert f["typical_seats_free"] <= 2
    assert none["enough_data"] is False or none["trips"] >= 3


# ---------- admin edits ----------

def test_admin_edits_timetable_and_stops(client_factory):
    bus_before = fetch("select departure_time, gate, max_capacity, serves_stops from buses where id = 3")[0]
    stop = fetch("select id, st_y(location::geometry) as lat, st_x(location::geometry) as lng, "
                 "geofence_radius_meters as r from stops where route_id = 1 and stop_name = 'C'")[0]
    try:
        with client_factory() as c:
            body = {"departure": "11:15", "gate": "Gate 3", "capacity": 42, "stops": ["G", "C", "A"]}
            assert c.put("/admin/buses/3", json=body, headers=driver()).status_code == 403
            assert c.put("/admin/buses/3", json={**body, "stops": ["Z"]}, headers=dispatcher()).status_code == 400
            assert c.put("/admin/buses/3", json={**body, "departure": "25:00"}, headers=dispatcher()).status_code == 422
            bus = c.put("/admin/buses/3", json=body, headers=dispatcher()).json()
            assert bus["stops"] == ["A", "C", "G"] and bus["gate"] == "Gate 3" and bus["capacity"] == 42
            assert c.get("/buses/3").json()["eta"]["scheduled"] == "11:15"

            moved = c.put(f"/admin/stops/{stop['id']}", json={"lat": 30.3101, "lng": 78.0301, "geofence_m": 90},
                          headers=dispatcher()).json()
            assert moved["geofence_m"] == 90
            pts = next(r for r in c.get("/routes").json() if r["id"] == 1)["stop_points"]
            assert next(p for p in pts if p["name"] == "C")["lat"] == pytest.approx(30.3101)
    finally:
        execute("update buses set departure_time = $1, gate = $2, max_capacity = $3, serves_stops = $4 where id = 3",
                bus_before["departure_time"], bus_before["gate"], bus_before["max_capacity"], bus_before["serves_stops"])
        execute("update stops set location = st_setsrid(st_makepoint($2::float8, $1::float8), 4326)::geography, "
                "geofence_radius_meters = $3 where id = $4", stop["lat"], stop["lng"], stop["r"], stop["id"])


# ---------- aboard: keep my bus, ETA at my stop, ride history ----------

@needs_students
def test_boarded_student_keeps_bus_with_stop_eta_and_ride_history(client_factory, make_students, clock):
    sid = make_students(1, stop="D")[0]  # route 4 stop D -> Bus 11
    me = auth(sid)
    conductor = {"Authorization": "Bearer " + token(str(uuid.uuid4()),
                                                     {"role": "conductor", "bus_id": QBUS, "name": "C"})}
    with client_factory() as c:
        before = c.get("/me/bus", headers=me).json()
        assert before["bus"]["id"] == QBUS and before["my_stop_eta"]["basis"] == "timetable"
        c.post("/me/queue", headers=me)
        c.post(f"/buses/{QBUS}/status", json={"status": "boarding"}, headers=conductor)
        tok = c.get(f"/buses/{QBUS}/queue", headers=conductor).json()["columns"]["called"][0]["id"]
        c.post(f"/buses/{QBUS}/queue/{tok}/boarded", headers=conductor)
        c.post(f"/buses/{QBUS}/status", json={"status": "departed"}, headers=conductor)
        aboard = c.get("/me/bus", headers=me).json()
        rides = c.get("/me/rides", headers=me).json()
    assert aboard["aboard"] is True and aboard["bus"]["id"] == QBUS and aboard["alert"] is None
    assert aboard["my_stop_eta"] is not None
    assert rides and rides[0]["bus_id"] == QBUS and rides[0]["route"].startswith("Route 4")
