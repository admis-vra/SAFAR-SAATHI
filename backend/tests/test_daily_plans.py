"""Daily plans on the sample data: staff pick a bus, students choose a slot home, staff assign buses to slots,
morning-run status per stop. Runs on a future day so it can't clash with real use of the test project."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import queueing
from conftest import execute, token
from test_m5_queue import needs_students

IST = queueing.IST


def day_at(hh, mm, days=3):
    d = datetime.now(IST).date() + timedelta(days=days)
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": day_at(9, 0)}
    monkeypatch.setattr(queueing, "now_ist", lambda: now["t"])
    yield now
    day = now["t"].date()
    for table in ("staff_bus_picks", "departure_intents", "bus_slot_assignments"):
        execute(f"delete from {table} where service_date = $1", day)


def staff(role, bus_id=None, name=None):
    uid = str(uuid.uuid4())
    meta = {"role": role, "name": name or f"Test {role}"}
    if bus_id is not None:
        meta["bus_id"] = bus_id
    return {"Authorization": "Bearer " + token(uid, meta)}


def dispatcher():
    return {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "dispatcher", "name": "Office"})}


def test_driver_picks_a_bus_for_the_day(client_factory, clock):
    first, second, conductor = staff("driver", 14, "Driver One"), staff("driver", 14, "Driver Two"), staff("conductor")
    with client_factory() as c:
        assert c.get("/me", headers=conductor).json()["bus"] is None          # no bus on account, none picked
        assert c.get("/me", headers=first).json()["bus"]["id"] == 14          # account bus until they pick

        assert c.put("/me/bus-pick", json={"bus_id": 9}, headers=first).status_code == 200
        me = c.get("/me", headers=first).json()
        assert me["bus_id"] == 9 and me["picked_today"] is True
        # Acting for the picked bus works; for the account's old bus it doesn't.
        assert c.post("/buses/14/location", json={"lat": 30.27, "lng": 77.99}, headers=first).status_code == 403

        taken = c.put("/me/bus-pick", json={"bus_id": 9}, headers=second)
        assert taken.status_code == 409 and "Driver One" in taken.json()["detail"]
        assert c.put("/me/bus-pick", json={"bus_id": 9}, headers=conductor).status_code == 200   # conductors share
        listing = {b["id"]: b for b in c.get("/staff/buses", headers=second).json()}
        assert listing[9]["driver_today"] == "Driver One" and listing[14]["driver_today"] is None
        assert c.put("/me/bus-pick", json={"bus_id": 99999}, headers=first).status_code == 400


def test_staff_assign_buses_to_slots(client_factory, clock):
    conductor = staff("conductor", 9)
    with client_factory() as c:
        assert c.put("/buses/9/slot", json={"slot": "16:00"}, headers=conductor).json()["slot"] == "16:00"
        assert c.get("/buses/9").json()["eta"]["scheduled"] == "16:00"     # today it leaves at its slot
        assert c.put("/buses/9/slot", json={"slot": "15:00"}, headers=conductor).status_code == 422
        assert c.put("/buses/14/slot", json={"slot": "13:00"}, headers=conductor).status_code == 403   # not theirs
        assert c.put("/buses/14/slot", json={"slot": "13:00"}, headers=dispatcher()).status_code == 200

        demand = c.get("/departures/demand", headers=conductor).json()
        by_slot = {s["slot"]: s for s in demand["slots"]}
        assert by_slot["16:00"]["buses"] == [9] and by_slot["13:00"]["buses"] == [14]
        assert demand["my_bus"]["id"] == 9 and demand["my_bus"]["slot"] == "16:00"

        assert c.put("/buses/9/slot", json={"slot": None}, headers=conductor).json()["slot"] is None
        usual = c.get("/buses/9").json()
        assert usual["eta"]["scheduled"] == usual["usual_departure"]
        assert c.get("/departures/demand", headers={"Authorization": "Bearer " + token(str(uuid.uuid4()))}).status_code == 403


def test_slot_allocation_in_memory(client_factory, clock):
    """Allocation rules without creating login rows: a student's slot picks only buses assigned to it."""
    import main
    with client_factory() as c:
        sid = str(uuid.uuid4())
        kid = {"id": sid, "student_id": "MEM1", "name": "Mem", "route_id": 3, "stop": "B"}
        main.students[sid] = kid
        try:
            assert main.allocate(kid)["bus"]["id"] == 9                      # no choice yet: usual timetable
            main.plans["intents"][sid] = {"slot": "18:00", "stop": "B"}
            none_yet = main.allocate(kid)
            assert none_yet["bus"] is None and "6 PM" in none_yet["alert"]
            c.put("/buses/14/slot", json={"slot": "18:00"}, headers=dispatcher())   # D-G only
            assert main.allocate(kid)["bus"] is None
            c.put("/buses/9/slot", json={"slot": "18:00"}, headers=dispatcher())    # A-C
            assert main.allocate(kid)["bus"]["id"] == 9
            main.plans["intents"][sid] = {"slot": None, "stop": "B"}
            assert main.allocate(kid) == {"bus": None, "alert": None}         # not going home today
        finally:
            main.students.pop(sid, None)
            main.plans["intents"].pop(sid, None)


@pytest.fixture
def student():
    sid = str(uuid.uuid4())
    execute("insert into auth.users (id) values ($1::uuid)", sid)
    execute("""insert into profiles (id, student_id, name, default_route_id, default_stop_id)
               select $1::uuid, 'TD0001', 'Plan Tester', 3, s.id from stops s where s.route_id = 3 and s.stop_name = 'B'""",
            sid)
    yield {"id": sid, "headers": {"Authorization": "Bearer " + token(sid)}}
    execute("delete from departure_intents where student_id = $1::uuid", sid)
    execute("delete from profiles where id = $1::uuid", sid)
    execute("delete from auth.users where id = $1::uuid", sid)


@needs_students
def test_student_chooses_slot_and_gets_the_assigned_bus(client_factory, clock, student):
    me = student["headers"]
    with client_factory() as c:
        assert c.get("/me/bus", headers=me).json()["bus"]["id"] == 9      # usual timetable before choosing

        d = c.put("/me/departure", json={"slot": "13:00"}, headers=me).json()
        assert (d["slot"], d["stop"], d["stop_counts"]["13:00"], d["buses"]) == ("13:00", "B", 1, [])
        waiting = c.get("/me/bus", headers=me).json()
        assert waiting["bus"] is None and "1 PM" in waiting["alert"]

        c.put("/buses/14/slot", json={"slot": "13:00"}, headers=dispatcher())   # 14 serves D-G: not stop B
        assert c.get("/me/bus", headers=me).json()["bus"] is None
        c.put("/buses/15/slot", json={"slot": "13:00"}, headers=dispatcher())   # also D-G
        c.put("/buses/9/slot", json={"slot": "13:00"}, headers=dispatcher())    # serves A-C
        got = c.get("/me/bus", headers=me).json()
        assert got["bus"]["id"] == 9 and got["bus"]["slot"] == "13:00"
        assert [b["id"] for b in c.get("/me/departure", headers=me).json()["buses"]] == [9]

        demand = c.get("/departures/demand", headers=dispatcher()).json()
        one_pm = next(s for s in demand["slots"] if s["slot"] == "13:00")
        assert one_pm["students"] == 1 and one_pm["stops"] == [{"stop": "B", "students": 1, "buses": [9]}]

        assert c.put("/me/departure", json={"slot": None}, headers=me).json()["slot"] is None
        assert c.get("/me/bus", headers=me).json() == {"bus": None, "alert": None, "aboard": False}
        assert c.put("/me/departure", json={"slot": "16:00", "stop": "Nowhere"}, headers=me).status_code == 400


@needs_students
def test_choices_close_at_the_deadline(client_factory, clock, student):
    with client_factory() as c:
        clock["t"] = day_at(10, 59)
        assert c.put("/me/departure", json={"slot": "18:00"}, headers=student["headers"]).status_code == 200
        clock["t"] = day_at(11, 0)
        late = c.put("/me/departure", json={"slot": "13:00"}, headers=student["headers"])
        assert late.status_code == 409 and "11:00" in late.json()["detail"]
        assert c.get("/me/departure", headers=student["headers"]).json()["slot"] == "18:00"


def test_morning_status_for_a_stop(client_factory, clock):
    import main
    with client_factory() as c:
        stop_b = next(s for s in c.get("/stops").json() if s["name"] == "B")
        assert 9 in stop_b["buses"] and 3 in stop_b["routes"]
        bus = main.buses[9]   # route 3, stops A, B, C (A = first pickup)
        saved = {k: bus.get(k) for k in ("runs", "location", "location_at", "speed_kmh")}
        try:
            bus["runs"] = ["07:00", "11:00"]
            now = day_at(6, 30)
            plan = main.morning_status(bus, "B", now)
            assert plan["state"] == "scheduled" and plan["run"] == "07:00" and plan["eta"]["basis"] == "timetable"

            a = next(p for p in main.routes[3]["stop_points"] if p["name"] == "A")
            c_ = next(p for p in main.routes[3]["stop_points"] if p["name"] == "C")
            now = day_at(7, 5)
            bus.update(location={"lat": a["lat"], "lng": a["lng"]}, location_at=now.astimezone(timezone.utc),
                       speed_kmh=20)
            bus["fence"].inside.discard("depot")
            assert main.morning_status(bus, "B", now)["state"] == "coming"
            assert main.morning_status(bus, "A", now)["state"] == "arriving"

            bus["location"] = {"lat": c_["lat"], "lng": c_["lng"]}   # now at C: B is behind it
            assert main.morning_status(bus, "B", now)["state"] == "passed"

            bus["location_at"] = None                                # stopped sharing, long after B's time
            assert main.morning_status(bus, "B", day_at(7, 30))["state"] == "probably_passed"
            assert main.morning_status(bus, "B", day_at(9, 30))["run"] == "11:00"   # second run is next
            assert main.morning_status(bus, "B", day_at(13, 0))["state"] == "done"
            assert main.morning_status(bus, "Nowhere", now) is None
        finally:
            bus.update(saved)
