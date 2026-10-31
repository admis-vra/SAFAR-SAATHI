"""Tests run against a disposable dev database given by TEST_DATABASE_URL (never DATABASE_URL).

They change bus live state on that database and restore it afterwards. Point it at a local Postgres
or a Supabase *dev* project, never the demo project.
"""
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
load_dotenv(BACKEND / ".env.test")

TEST_DSN = os.environ.get("TEST_DATABASE_URL")
JWT_SECRET = "test-secret-for-local-tests-only-0123456789"
SUPABASE_URL = "http://supabase.test.invalid"

if TEST_DSN:
    os.environ["DATABASE_URL"] = TEST_DSN
    os.environ["SUPABASE_URL"] = SUPABASE_URL
    os.environ["SUPABASE_JWT_SECRET"] = JWT_SECRET
    # Don't listen to the real broker from backend/.env (a running simulator would leak into tests);
    # the MQTT test sets its own. Empty (not unset) so load_dotenv won't fill it back in.
    os.environ["MQTT_URL"] = ""

collect_ignore_glob = [] if TEST_DSN else ["test_*.py"]


def token(sub: str, app_metadata: dict | None = None, user_metadata: dict | None = None) -> str:
    import jwt
    now = int(time.time())
    claims = {"sub": sub, "aud": "authenticated", "iss": f"{SUPABASE_URL}/auth/v1", "iat": now, "exp": now + 600,
              "app_metadata": app_metadata or {}, "user_metadata": user_metadata or {}}
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


def run(coro):
    return asyncio.run(coro)


async def _fetch(sql, *args):
    import asyncpg
    conn = await asyncpg.connect(TEST_DSN, statement_cache_size=0, timeout=20, command_timeout=60)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _execute(sql, *args):
    import asyncpg
    conn = await asyncpg.connect(TEST_DSN, statement_cache_size=0, timeout=20, command_timeout=60)
    try:
        return await conn.execute(sql, *args)
    finally:
        await conn.close()


def fetch(sql, *args):
    return run(_fetch(sql, *args))


def execute(sql, *args):
    return run(_execute(sql, *args))


SKIP_MIGRATE = os.environ.get("TEST_SKIP_MIGRATE") == "1"


@pytest.fixture(scope="session", autouse=True)
def migrated():
    # TEST_SKIP_MIGRATE=1: don't apply pending migrations (e.g. another branch's unfinished one).
    if SKIP_MIGRATE:
        return
    import migrate
    run(migrate.apply(TEST_DSN, local_auth_stub=os.environ.get("TEST_AUTH_STUB") == "1"))


@pytest.fixture(autouse=True)
def restore_bus_state():
    saved = fetch("select bus_id, status::text as status, st_astext(location) as wkt, speed_kmh, people_count, "
                  "cam_seats_free, cam_seats_total, ir_net, last_device_ts from bus_live_state")
    open_trips = [r["id"] for r in fetch("select id from active_trips where end_time is null")]
    open_incidents = [r["id"] for r in fetch("select id from incidents where closed_at is null "
                                              "or (kind = 'sos' and acknowledged_at is null)")]
    yield
    execute("update active_trips set end_time = now() where end_time is null and not (id = any($1::bigint[]))",
            open_trips)
    # An SOS only stops showing in the control room once acknowledged, so test SOSes get acknowledged too.
    execute("update incidents set closed_at = coalesce(closed_at, now()), "
            "acknowledged_at = case when kind = 'sos' then coalesce(acknowledged_at, now()) else acknowledged_at end, "
            "detail = detail || ' [test]' "
            "where (closed_at is null or (kind = 'sos' and acknowledged_at is null)) "
            "and not (id = any($1::bigint[]))", open_incidents)
    for r in saved:
        execute("""update bus_live_state set status=$2::bus_status,
                   location=case when $3::text is null then null else st_geogfromtext($3::text) end,
                   speed_kmh=$4, people_count=$5, cam_seats_free=$6, cam_seats_total=$7, ir_net=$8,
                   last_device_ts=$9 where bus_id=$1""",
                r["bus_id"], r["status"], r["wkt"], r["speed_kmh"], r["people_count"],
                r["cam_seats_free"], r["cam_seats_total"], r["ir_net"], r["last_device_ts"])


@pytest.fixture
def client_factory():
    from fastapi.testclient import TestClient
    import main
    return lambda: TestClient(main.app)


@pytest.fixture
def driver14():
    return {"Authorization": "Bearer " + token("00000000-0000-0000-0000-0000000000d1",
                                               {"role": "driver", "bus_id": 14, "name": "Test Driver"})}


@pytest.fixture
def new_uuid():
    return str(uuid.uuid4())
