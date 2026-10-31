from datetime import date, datetime, timedelta

import asyncpg

pool: asyncpg.Pool | None = None


async def connect(dsn: str):
    global pool
    # statement_cache_size=0 keeps us compatible with Supabase's transaction-mode pooler.
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, statement_cache_size=0)


async def close():
    global pool
    if pool:
        await pool.close()
        pool = None


async def ensure_partitions(days_ahead: int = 7):
    today = date.today()
    async with pool.acquire() as c:
        for i in range(-1, days_ahead + 1):
            await c.execute("select ensure_telemetry_partition($1)", today + timedelta(days=i))


async def load_routes() -> tuple[dict, dict]:
    rows = await pool.fetch(
        "select r.id, r.name, s.id as stop_id, s.stop_name from routes r "
        "join stops s on s.route_id = r.id where r.is_active order by r.id, s.seq")
    routes, stop_ids = {}, {}
    for r in rows:
        routes.setdefault(r["id"], {"id": r["id"], "name": r["name"], "stops": []})["stops"].append(r["stop_name"])
        stop_ids[(r["id"], r["stop_name"])] = r["stop_id"]
    return routes, stop_ids


async def load_buses() -> list[dict]:
    rows = await pool.fetch("""
        select b.id, b.route_id, b.gate, b.serves_stops, b.max_capacity, b.departure_time,
               s.status, st_y(s.location::geometry) as lat, st_x(s.location::geometry) as lng,
               s.people_count, s.cam_seats_free, s.cam_seats_total, s.updated_at,
               (select t.id from active_trips t where t.bus_id = b.id and t.end_time is null) as trip_id
        from buses b left join bus_live_state s on s.bus_id = b.id
        order by b.id""")
    return [{
        "id": r["id"], "route_id": r["route_id"], "gate": r["gate"], "stops": list(r["serves_stops"]),
        "capacity": r["max_capacity"], "departure": r["departure_time"].strftime("%H:%M"),
        "status": r["status"] or "scheduled",
        "location": {"lat": r["lat"], "lng": r["lng"]} if r["lat"] is not None else None,
        "count": r["people_count"],
        "seat_cam": ({"free": r["cam_seats_free"], "total": r["cam_seats_total"]}
                     if r["cam_seats_total"] else None),
        "updated_at": r["updated_at"].isoformat(timespec="seconds") if r["updated_at"] else None,
        "trip_id": r["trip_id"], "assigned": 0,
    } for r in rows]


async def save_live_state(bus: dict):
    loc = bus["location"]
    cam = bus["seat_cam"] or {}
    await pool.execute("""
        insert into bus_live_state (bus_id, status, location, people_count, cam_seats_free, cam_seats_total, updated_at)
        values ($1, $2::bus_status,
                case when $3::float8 is null then null
                     else st_setsrid(st_makepoint($4::float8, $3::float8), 4326)::geography end,
                $5, $6, $7, now())
        on conflict (bus_id) do update set
          status = excluded.status, location = excluded.location, people_count = excluded.people_count,
          cam_seats_free = excluded.cam_seats_free, cam_seats_total = excluded.cam_seats_total,
          updated_at = excluded.updated_at""",
        bus["id"], bus["status"], loc["lat"] if loc else None, loc["lng"] if loc else None,
        bus["count"], cam.get("free"), cam.get("total"))


async def insert_telemetry(bus: dict, ts: datetime, from_backlog: bool = False):
    loc = bus["location"]
    cam = bus["seat_cam"] or {}
    await pool.execute("""
        insert into telemetry (bus_id, ts, trip_id, location, people_count, cv_seats_free, from_backlog)
        values ($1, $2, $3,
                case when $4::float8 is null then null
                     else st_setsrid(st_makepoint($5::float8, $4::float8), 4326)::geography end,
                $6, $7, $8)
        on conflict (bus_id, ts) do nothing""",
        bus["id"], ts, bus.get("trip_id"), loc["lat"] if loc else None, loc["lng"] if loc else None,
        bus["count"], cam.get("free"), from_backlog)


async def open_trip(bus: dict) -> int:
    existing = await pool.fetchval(
        "select id from active_trips where bus_id = $1 and end_time is null", bus["id"])
    if existing:
        return existing
    return await pool.fetchval(
        "insert into active_trips (bus_id, route_id, departure_scheduled) values ($1, $2, $3::time) returning id",
        bus["id"], bus["route_id"], datetime.strptime(bus["departure"], "%H:%M").time())


async def close_trip(bus_id: int):
    await pool.execute(
        "update active_trips set end_time = now() where bus_id = $1 and end_time is null", bus_id)


async def upsert_profile(user_id: str, p: dict, stop_id: int):
    await pool.execute("""
        insert into profiles (id, student_id, name, default_route_id, default_stop_id, exam_slot, updated_at)
        values ($1::uuid, $2, $3, $4, $5, $6::time, now())
        on conflict (id) do update set
          student_id = excluded.student_id, name = excluded.name, default_route_id = excluded.default_route_id,
          default_stop_id = excluded.default_stop_id, exam_slot = excluded.exam_slot, updated_at = now()""",
        user_id, p["student_id"], p["name"], p["route_id"], stop_id,
        datetime.strptime(p["exam_slot"], "%H:%M").time() if p.get("exam_slot") else None)


_PROFILE_SQL = """
    select p.id::text as id, p.student_id, p.name, p.default_route_id as route_id, s.stop_name as stop,
           to_char(p.exam_slot, 'HH24:MI') as exam_slot
    from profiles p join stops s on s.id = p.default_stop_id"""


async def get_profile(user_id: str) -> dict | None:
    row = await pool.fetchrow(_PROFILE_SQL + " where p.id = $1::uuid", user_id)
    return dict(row) if row else None


async def all_profiles() -> list[dict]:
    return [dict(r) for r in await pool.fetch(_PROFILE_SQL)]
