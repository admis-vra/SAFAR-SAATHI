import os
from datetime import date
from pathlib import Path

import pytest

from conftest import SKIP_MIGRATE, TEST_DSN, execute, fetch, run, token

MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


@pytest.mark.skipif(SKIP_MIGRATE, reason="TEST_SKIP_MIGRATE=1: would apply pending migrations")
def test_migrations_tracked_and_reapply_cleanly():
    import migrate
    assert run(migrate.apply(TEST_DSN, local_auth_stub=os.environ.get("TEST_AUTH_STUB") == "1")) == []
    # Every file must also be safe to run again by hand (e.g. pasted into the Supabase SQL editor).
    for path in sorted(MIGRATIONS.glob("*.sql")):
        execute(path.read_text(encoding="utf-8"))
    assert fetch("select count(*) as n from routes")[0]["n"] == 4
    assert fetch("select count(*) as n from stops")[0]["n"] == 28


def test_rls_enabled_on_every_app_table():
    rows = fetch("""select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = 'public' and c.relkind in ('r', 'p')
                      and c.relname <> 'schema_migrations' and not c.relrowsecurity
                      and c.relname not in (select extname from pg_extension)
                      and c.relname <> 'spatial_ref_sys'""")
    assert [r["relname"] for r in rows] == []


def test_reference_data_served_from_database(client_factory):
    with client_factory() as c:
        routes = c.get("/routes").json()
        buses = c.get("/buses").json()
    assert [r["id"] for r in routes] == [1, 2, 3, 4]
    assert all(r["stops"] == list("ABCDEFG") for r in routes)
    assert sorted(b["id"] for b in buses) == [3, 5, 6, 9, 11, 14, 15]
    bus14 = next(b for b in buses if b["id"] == 14)
    assert bus14["stops"] == list("DEFG") and bus14["capacity"] == 40 and bus14["gate"] == "Gate 2"


def test_restart_restores_bus_state(client_factory, driver14):
    with client_factory() as c:
        assert c.post("/buses/14/location", json={"lat": 30.2801, "lng": 78.0012}, headers=driver14).status_code == 200
        assert c.post("/buses/14/count", json={"count": 33, "seats_free": 6, "seats_total": 40}).status_code == 200
        assert c.post("/buses/14/status", json={"status": "departed"}, headers=driver14).status_code == 200

    with client_factory() as c:  # fresh lifespan = backend restart
        bus = c.get("/buses/14").json()
    assert bus["status"] == "departed"
    assert bus["location"] == pytest.approx({"lat": 30.2801, "lng": 78.0012})
    assert bus["occupancy"] == 33
    assert bus["seats_free"] == 6 and bus["seat_source"] == "camera"


def test_departure_opens_one_trip_and_reset_closes_it(client_factory, driver14):
    with client_factory() as c:
        c.post("/buses/14/status", json={"status": "departed"}, headers=driver14)
        c.post("/buses/14/status", json={"status": "departed"}, headers=driver14)
        open_trips = fetch("select id from active_trips where bus_id = 14 and end_time is null")
        assert len(open_trips) == 1
        c.post("/buses/14/status", json={"status": "scheduled"}, headers=driver14)
    assert fetch("select count(*) as n from active_trips where bus_id = 14 and end_time is null")[0]["n"] == 0


def test_telemetry_written_to_todays_partition(client_factory, driver14):
    before = fetch("select count(*) as n from telemetry where bus_id = 15")[0]["n"]
    with client_factory() as c:
        c.post("/buses/15/count", json={"count": 12})
    rows = fetch("select tableoid::regclass::text as part, people_count from telemetry "
                 "where bus_id = 15 order by ts desc limit 1")
    assert fetch("select count(*) as n from telemetry where bus_id = 15")[0]["n"] == before + 1
    assert rows[0]["people_count"] == 12
    assert rows[0]["part"] == f"telemetry_{date.today():%Y%m%d}"


def test_telemetry_dedupes_same_bus_and_timestamp():
    execute("select ensure_telemetry_partition(current_date)")
    for _ in range(2):
        execute("insert into telemetry (bus_id, ts, people_count) values (9, '2026-01-01T00:00:00Z'::timestamptz "
                "+ (current_date - date '2026-01-01') * interval '1 day', 1) on conflict (bus_id, ts) do nothing")
    n = fetch("select count(*) as n from telemetry where bus_id = 9 and ts = current_date::timestamptz")[0]["n"]
    assert n == 1


@pytest.mark.skipif(os.environ.get("TEST_AUTH_STUB") != "1",
                    reason="needs a local auth.users stub; never writes to a real Supabase auth schema")
def test_profile_persists_across_restart(client_factory, new_uuid):
    execute("insert into auth.users (id) values ($1::uuid) on conflict do nothing", new_uuid)
    headers = {"Authorization": "Bearer " + token(new_uuid)}
    profile = {"student_id": "GEHU9001", "name": "Test Student", "route_id": 3, "stop": "B", "exam_slot": "11:00"}
    with client_factory() as c:
        assert c.get("/me/bus", headers=headers).status_code == 409
        assert c.put("/me/profile", json=profile, headers=headers).json()["bus"]["id"] == 9

    with client_factory() as c:
        me = c.get("/me", headers=headers).json()
        assert me["profile"]["student_id"] == "GEHU9001" and me["profile"]["stop"] == "B"
        assert me["profile"]["exam_slot"] == "11:00"
        assert c.get("/me/bus", headers=headers).json()["bus"]["id"] == 9
    execute("delete from profiles where id = $1::uuid", new_uuid)
    execute("delete from auth.users where id = $1::uuid", new_uuid)
