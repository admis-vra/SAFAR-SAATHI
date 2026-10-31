import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import queueing
from conftest import TEST_DSN, execute, fetch, token

IST = queueing.IST
BUS = 11  # route 4, departs 13:20, serves A-G
# Student tests need rows in auth.users (profiles reference it). Opt in explicitly: never by default against
# a real Supabase project. With a local stub (TEST_AUTH_STUB=1) it's always allowed.
ALLOW_AUTH_ROWS = os.environ.get("TEST_ALLOW_AUTH_ROWS") == "1" or os.environ.get("TEST_AUTH_STUB") == "1"
needs_students = pytest.mark.skipif(not ALLOW_AUTH_ROWS, reason="set TEST_ALLOW_AUTH_ROWS=1 to create temp auth rows")


def at(hh, mm, days=1):
    """A time on a future day, so today's real trips on the test buses don't close the queue."""
    d = datetime.now(IST).date() + timedelta(days=days)
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=IST)


# ---------- pure rules ----------

def test_window_states():
    assert queueing.window("13:20", at(12, 49), None).state == "not_open"
    w = queueing.window("13:20", at(12, 50), None)
    assert (w.state, w.opens_at.strftime("%H:%M"), w.departs_at.strftime("%H:%M")) == ("open", "12:50", "13:20")
    assert queueing.window("13:20", at(13, 35), None).state == "open"  # late bus: still boarding


def test_window_closes_once_bus_has_left_today():
    left = at(13, 22).astimezone(timezone.utc)
    assert queueing.window("13:20", at(13, 30), left).state == "closed"
    yesterday = at(13, 22, days=0).astimezone(timezone.utc)
    assert queueing.window("13:20", at(13, 0), yesterday).state == "open"


def test_room_to_call():
    assert queueing.room_to_call(5, 2) == 3
    assert queueing.room_to_call(2, 4) == 0


# ---------- with students ----------

@pytest.fixture
def make_students():
    created = []

    def make(n, stop="B"):
        ids = [str(uuid.uuid4()) for _ in range(n)]
        for i, sid in enumerate(ids):
            execute("insert into auth.users (id) values ($1::uuid)", sid)
            execute("""insert into profiles (id, student_id, name, default_route_id, default_stop_id)
                       select $1::uuid, $2, $3, 4, s.id from stops s where s.route_id = 4 and s.stop_name = $4""",
                    sid, f"TQ{len(created) + i:04d}", f"Queue Tester {len(created) + i}", stop)
        created.extend(ids)
        return ids

    yield make
    if created:
        execute("delete from digital_tokens where student_id = any($1::uuid[])", created)
        execute("delete from profiles where id = any($1::uuid[])", created)
        execute("delete from auth.users where id = any($1::uuid[])", created)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": at(13, 0)}
    monkeypatch.setattr(queueing, "now_ist", lambda: now["t"])
    day = now["t"].date()
    yield now
    execute("delete from digital_tokens where bus_id = $1 and service_date >= $2", BUS, day)


def auth(sid):
    return {"Authorization": "Bearer " + token(sid)}


def driver():
    return {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "conductor", "bus_id": BUS, "name": "C11"})}


@needs_students
def test_fifty_concurrent_joins_get_unique_gapless_positions(make_students, clock):
    import db
    ids = make_students(50)
    day = clock["t"].date()

    async def go():
        await db.connect(TEST_DSN)
        try:
            return await asyncio.gather(*(db.join_queue(s, BUS, day) for s in ids))
        finally:
            await db.close()

    positions = sorted(r["queue_position"] for r in asyncio.run(go()))
    assert positions == list(range(1, 51))


@needs_students
def test_join_rules(client_factory, make_students, clock):
    a, b = make_students(2)
    with client_factory() as c:
        clock["t"] = at(12, 40)
        assert "opens at 12:50" in c.post("/me/queue", headers=auth(a)).json()["detail"]
        clock["t"] = at(13, 0)
        first = c.post("/me/queue", headers=auth(a)).json()
        assert first["token"]["position"] == 1 and first["queue"]["state"] == "open"
        assert c.post("/me/queue", headers=auth(a)).status_code == 409   # one active token per student
        second = c.post("/me/queue", headers=auth(b)).json()["token"]
        assert second["position"] == 2 and second["ahead"] == 1
        assert c.delete("/me/queue", headers=auth(a)).status_code == 200
        assert c.get("/me/queue", headers=auth(b)).json()["token"]["ahead"] == 0
        assert c.delete("/me/queue", headers=auth(a)).status_code == 404


@needs_students
def test_boarding_calls_only_as_many_as_seats(client_factory, make_students, clock):
    ids = make_students(5)
    with client_factory() as c:
        for s in ids:
            c.post("/me/queue", headers=auth(s))
        c.post(f"/buses/{BUS}/count", json={"count": 37})          # 3 seats free
        c.post(f"/buses/{BUS}/status", json={"status": "boarding"}, headers=driver())
        board = c.get(f"/buses/{BUS}/queue", headers=driver()).json()["columns"]
        assert [t["position"] for t in board["called"]] == [1, 2, 3]
        assert [t["position"] for t in board["waiting"]] == [4, 5]
        assert c.get("/me/queue", headers=auth(ids[0])).json()["token"]["status"] == "called"

        first, second = board["called"][0]["id"], board["called"][1]["id"]
        assert c.post(f"/buses/{BUS}/queue/{first}/boarded", headers=driver()).status_code == 200
        assert c.post(f"/buses/{BUS}/queue/{second}/no_show", headers=driver()).status_code == 200
        assert c.post(f"/buses/{BUS}/queue/{second}/boarded", headers=driver()).status_code == 404  # already closed
        c.post(f"/buses/{BUS}/count", json={"count": 38})          # the boarded student is now on board
        import main
        c.portal.call(main.service_queues)
        board = c.get(f"/buses/{BUS}/queue", headers=driver()).json()["columns"]
        # 2 free, position 3 still called -> one more (position 4) gets called
        assert [t["position"] for t in board["called"]] == [3, 4] and [t["position"] for t in board["waiting"]] == [5]


@needs_students
def test_called_token_expires_and_departure_closes_queue(client_factory, make_students, clock):
    a, b = make_students(2)
    with client_factory() as c:
        c.post("/me/queue", headers=auth(a))
        c.post("/me/queue", headers=auth(b))
        c.post(f"/buses/{BUS}/count", json={"count": 39})          # 1 seat
        c.post(f"/buses/{BUS}/status", json={"status": "boarding"}, headers=driver())
        execute("update digital_tokens set called_at = now() - interval '6 minutes' "
                "where student_id = $1::uuid", a)
        import main
        c.post(f"/buses/{BUS}/count", json={"count": 40})          # full: nobody new gets called
        c.portal.call(main.service_queues)
        assert c.get("/me/queue", headers=auth(a)).json()["token"]["status"] == "expired"
        assert c.get("/me/queue", headers=auth(b)).json()["token"]["status"] == "waiting"
        c.post(f"/buses/{BUS}/status", json={"status": "departed"}, headers=driver())
        mine = c.get("/me/queue", headers=auth(b)).json()
        assert mine["token"]["status"] == "expired" and mine["queue"]["state"] == "closed"
        assert c.post("/me/queue", headers=auth(b)).status_code == 409


def test_queue_board_is_staff_or_dispatcher_only(client_factory):
    stranger = {"Authorization": "Bearer " + token(str(uuid.uuid4()))}
    other_bus = {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "driver", "bus_id": 14, "name": "x"})}
    dispatcher = {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "dispatcher", "name": "Ctl"})}
    with client_factory() as c:
        assert c.get(f"/buses/{BUS}/queue", headers=stranger).status_code == 403
        assert c.get(f"/buses/{BUS}/queue", headers=other_bus).status_code == 403
        assert c.get(f"/buses/{BUS}/queue", headers=dispatcher).status_code == 200
        assert c.post(f"/buses/{BUS}/queue/1/boarded", headers=dispatcher).status_code == 403
