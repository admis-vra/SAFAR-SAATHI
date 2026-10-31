from datetime import date, datetime, timedelta

import asyncpg

pool: asyncpg.Pool | None = None


async def connect(dsn: str):
    global pool
    # statement_cache_size=0 keeps us compatible with Supabase's transaction-mode pooler.
    # Timeouts: a stalled network call to the cloud database must fail, not hang a request forever.
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, statement_cache_size=0,
                                     timeout=20, command_timeout=30)


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
        "select r.id, r.name, s.id as stop_id, s.stop_name, st_y(s.location::geometry) as lat, "
        "st_x(s.location::geometry) as lng, s.geofence_radius_meters as radius from routes r "
        "join stops s on s.route_id = r.id where r.is_active order by r.id, s.seq")
    routes, stop_ids = {}, {}
    for r in rows:
        route = routes.setdefault(r["id"], {"id": r["id"], "name": r["name"], "stops": [], "stop_points": []})
        route["stops"].append(r["stop_name"])
        route["stop_points"].append({"id": r["stop_id"], "name": r["stop_name"], "lat": r["lat"], "lng": r["lng"],
                                     "geofence_m": r["radius"]})
        stop_ids[(r["id"], r["stop_name"])] = r["stop_id"]
    return routes, stop_ids


async def load_buses() -> list[dict]:
    rows = await pool.fetch("""
        select b.id, b.route_id, b.gate, b.serves_stops, b.max_capacity, b.departure_time,
               b.mqtt_device_id,
               s.status, st_y(s.location::geometry) as lat, st_x(s.location::geometry) as lng,
               s.speed_kmh, s.people_count, s.cam_seats_free, s.cam_seats_total, s.ir_net, s.last_device_ts,
               s.ir_offset, s.fused_seats_free, s.updated_at,
               (select t.id from active_trips t where t.bus_id = b.id and t.end_time is null) as trip_id,
               (select max(t.start_time) from active_trips t where t.bus_id = b.id) as last_trip_start
        from buses b left join bus_live_state s on s.bus_id = b.id
        order by b.id""")
    return [{
        "id": r["id"], "route_id": r["route_id"], "gate": r["gate"], "stops": list(r["serves_stops"]),
        "capacity": r["max_capacity"], "departure": r["departure_time"].strftime("%H:%M"),
        "device_id": r["mqtt_device_id"],
        "status": r["status"] or "scheduled",
        "location": {"lat": r["lat"], "lng": r["lng"]} if r["lat"] is not None else None,
        "speed_kmh": r["speed_kmh"],
        "count": r["people_count"], "ir_net": r["ir_net"], "last_device_ts": r["last_device_ts"],
        "ir_offset": r["ir_offset"] or 0, "fused_seats_free": r["fused_seats_free"],
        "seat_cam": ({"free": r["cam_seats_free"], "total": r["cam_seats_total"]}
                     if r["cam_seats_total"] else None),
        "updated_at": r["updated_at"].isoformat(timespec="seconds") if r["updated_at"] else None,
        "trip_id": r["trip_id"], "last_trip_start": r["last_trip_start"], "assigned": 0,
    } for r in rows]


async def save_live_state(bus: dict):
    loc = bus["location"]
    cam = bus["seat_cam"] or {}
    await pool.execute("""
        insert into bus_live_state (bus_id, status, location, speed_kmh, people_count, cam_seats_free,
                                    cam_seats_total, ir_net, last_device_ts, ir_offset, fused_seats_free, updated_at)
        values ($1, $2::bus_status,
                case when $3::float8 is null then null
                     else st_setsrid(st_makepoint($4::float8, $3::float8), 4326)::geography end,
                $5, $6, $7, $8, $9, $10, $11, $12, now())
        on conflict (bus_id) do update set
          status = excluded.status, location = excluded.location, speed_kmh = excluded.speed_kmh,
          people_count = excluded.people_count, cam_seats_free = excluded.cam_seats_free,
          cam_seats_total = excluded.cam_seats_total, ir_net = excluded.ir_net,
          last_device_ts = excluded.last_device_ts, ir_offset = excluded.ir_offset,
          fused_seats_free = excluded.fused_seats_free, updated_at = excluded.updated_at""",
        bus["id"], bus["status"], loc["lat"] if loc else None, loc["lng"] if loc else None,
        bus.get("speed_kmh"), bus["count"], cam.get("free"), cam.get("total"), bus.get("ir_net"),
        bus.get("last_device_ts"), bus.get("ir_offset", 0), bus.get("fused_seats_free"))


async def set_fused_seats(bus_id: int, ts: datetime, seats: int | None):
    await pool.execute("update telemetry set fused_seats_free = $3 where bus_id = $1 and ts = $2", bus_id, ts, seats)


async def insert_device_telemetry(bus_id: int, trip_id: int | None, items: list, from_backlog: bool) -> set:
    """Insert device readings in one statement; returns the timestamps that were new (not duplicates)."""
    rows = await pool.fetch("""
        insert into telemetry (bus_id, ts, trip_id, location, speed_kmh, ir_net, ir_in, ir_out, sats,
                               people_count, from_backlog)
        select $1, t.ts, $2,
               case when t.fix then st_setsrid(st_makepoint(t.lon, t.lat), 4326)::geography end,
               t.speed, t.net, t.inn, t.outt, t.sats, greatest(t.net, 0), $3
        from unnest($4::timestamptz[], $5::float8[], $6::float8[], $7::bool[], $8::real[], $9::int[],
                    $10::int[], $11::int[], $12::smallint[]) as t(ts, lat, lon, fix, speed, net, inn, outt, sats)
        on conflict (bus_id, ts) do nothing
        returning ts""",
        bus_id, trip_id, from_backlog,
        [i.ts for i in items], [i.gps.lat for i in items], [i.gps.lon for i in items],
        [i.has_fix for i in items], [i.gps.speed for i in items], [i.pax.net_current for i in items],
        [i.pax.in_count for i in items], [i.pax.out_count for i in items], [i.gps.sats for i in items])
    return {r["ts"] for r in rows}


async def open_incident(bus_id: int, trip_id: int | None, kind: str, detail: str) -> int:
    return await pool.fetchval(
        "insert into incidents (bus_id, trip_id, kind, detail) values ($1, $2, $3::incident_kind, $4) returning id",
        bus_id, trip_id, kind, detail)


async def close_incident(incident_id: int):
    await pool.execute("update incidents set closed_at = now() where id = $1 and closed_at is null", incident_id)


async def open_incidents(kind: str) -> dict[int, int]:
    rows = await pool.fetch(
        "select bus_id, max(id) as id from incidents where kind = $1::incident_kind and closed_at is null "
        "group by bus_id", kind)
    return {r["bus_id"]: r["id"] for r in rows}


async def load_depot() -> dict:
    r = await pool.fetchrow("select id, name, st_y(location::geometry) as lat, st_x(location::geometry) as lng, "
                            "geofence_radius_meters as radius from depots order by id limit 1")
    return dict(r)


async def insert_trip_event(bus_id: int, trip_id: int | None, kind: str, ts: datetime,
                            stop_id: int | None = None, late: bool = False, detail: str | None = None):
    await pool.execute(
        "insert into trip_events (bus_id, trip_id, kind, stop_id, ts, late, detail) "
        "values ($1, $2, $3::trip_event_kind, $4, $5, $6, $7)",
        bus_id, trip_id, kind, stop_id, ts, late, detail)


async def open_sos(bus_id: int, trip_id: int | None, lat: float | None, lng: float | None,
                   late: bool, detail: str) -> int:
    return await pool.fetchval("""
        insert into incidents (bus_id, trip_id, kind, detail, late, location)
        values ($1, $2, 'sos', $3, $4,
                case when $5::float8 is null then null
                     else st_setsrid(st_makepoint($6::float8, $5::float8), 4326)::geography end)
        returning id""", bus_id, trip_id, detail, late, lat, lng)


async def open_sos_incidents() -> dict[int, int]:
    rows = await pool.fetch("select bus_id, max(id) as id from incidents where kind = 'sos' "
                            "and acknowledged_at is null group by bus_id")
    return {r["bus_id"]: r["id"] for r in rows}


async def acknowledge_incident(incident_id: int, user_id: str) -> dict | None:
    r = await pool.fetchrow("""
        update incidents set acknowledged_at = now(), acknowledged_by = $2::uuid,
                             closed_at = coalesce(closed_at, now())
        where id = $1 and acknowledged_at is null
        returning id, bus_id, kind::text as kind""", incident_id, user_id)
    return dict(r) if r else None


async def list_incidents(only_open: bool) -> list[dict]:
    rows = await pool.fetch(f"""
        select id, bus_id, kind::text as kind, detail, late, opened_at, closed_at, acknowledged_at,
               st_y(location::geometry) as lat, st_x(location::geometry) as lng
        from incidents
        {"where (kind = 'sos' and acknowledged_at is null) or (kind <> 'sos' and closed_at is null)" if only_open else ""}
        order by opened_at desc limit 200""")
    return [{**dict(r), **{k: r[k].isoformat() if r[k] else None
                           for k in ("opened_at", "closed_at", "acknowledged_at")}} for r in rows]


async def bus_for_token_hash(token_hash: str) -> int | None:
    return await pool.fetchval("select id from buses where device_token_hash = $1", token_hash)


async def set_device_token_hash(bus_id: int, token_hash: str):
    await pool.execute("update buses set device_token_hash = $2 where id = $1", bus_id, token_hash)


async def insert_telemetry(bus: dict, ts: datetime, from_backlog: bool = False):
    loc = bus["location"]
    cam = bus["seat_cam"] or {}
    await pool.execute("""
        insert into telemetry (bus_id, ts, trip_id, location, people_count, cv_seats_free, fused_seats_free,
                               from_backlog)
        values ($1, $2, $3,
                case when $4::float8 is null then null
                     else st_setsrid(st_makepoint($5::float8, $4::float8), 4326)::geography end,
                $6, $7, $8, $9)
        on conflict (bus_id, ts) do nothing""",
        bus["id"], ts, bus.get("trip_id"), loc["lat"] if loc else None, loc["lng"] if loc else None,
        bus["count"], cam.get("free"), bus.get("fused_seats_free"), from_backlog)


async def open_trip(bus: dict) -> int:
    existing = await pool.fetchval(
        "select id from active_trips where bus_id = $1 and end_time is null", bus["id"])
    if existing:
        return existing
    # Last bus = no later bus on the same route serves any of the stops this one serves.
    return await pool.fetchval("""
        insert into active_trips (bus_id, route_id, departure_scheduled, is_last_bus)
        values ($1, $2, $3::time,
                exists (select 1 from unnest((select serves_stops from buses where id = $1)) as s(stop)
                        where $3::time >= (select max(o.departure_time) from buses o
                                           where o.route_id = $2 and s.stop = any (o.serves_stops))))
        returning id""",
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
           to_char(p.exam_slot, 'HH24:MI') as exam_slot, p.home_location is not null as has_home
    from profiles p join stops s on s.id = p.default_stop_id"""


async def get_profile(user_id: str) -> dict | None:
    row = await pool.fetchrow(_PROFILE_SQL + " where p.id = $1::uuid", user_id)
    return dict(row) if row else None


async def all_profiles() -> list[dict]:
    return [dict(r) for r in await pool.fetch(_PROFILE_SQL)]


# ---------- digital queue tokens ----------

class AlreadyQueued(Exception):
    pass


_ACTIVE = "('waiting', 'called')"


async def join_queue(student_id: str, bus_id: int, day: date) -> dict:
    async with pool.acquire() as c, c.transaction():
        # Serialize joins per queue so positions are 1, 2, 3... with no gaps or duplicates.
        await c.execute("select pg_advisory_xact_lock($1, $2)", bus_id, day.toordinal())
        await c.execute(f"update digital_tokens set status = 'expired', closed_at = now() "
                        f"where student_id = $1::uuid and status in {_ACTIVE} and service_date < $2", student_id, day)
        if await c.fetchval(f"select id from digital_tokens where student_id = $1::uuid and status in {_ACTIVE}",
                            student_id):
            raise AlreadyQueued()
        row = await c.fetchrow("""
            insert into digital_tokens (student_id, bus_id, service_date, queue_position)
            select $1::uuid, $2, $3, coalesce(max(queue_position), 0) + 1
            from digital_tokens where bus_id = $2 and service_date = $3
            returning id, queue_position""", student_id, bus_id, day)
        return dict(row)


async def student_token(student_id: str, day: date) -> dict | None:
    row = await pool.fetchrow(f"""
        select t.id, t.bus_id, t.queue_position as position, t.status::text as status, t.created_at, t.called_at,
               (select count(*) from digital_tokens o where o.bus_id = t.bus_id and o.service_date = t.service_date
                  and o.status in {_ACTIVE} and o.queue_position < t.queue_position) as ahead
        from digital_tokens t where t.student_id = $1::uuid and t.service_date = $2
        order by t.id desc limit 1""", student_id, day)
    return dict(row) if row else None


async def cancel_token(student_id: str) -> dict | None:
    row = await pool.fetchrow(f"""
        update digital_tokens set status = 'cancelled', closed_at = now()
        where student_id = $1::uuid and status in {_ACTIVE} returning id, bus_id""", student_id)
    return dict(row) if row else None


async def count_promised(bus_id: int, day: date, sensors_since: datetime | None) -> int:
    """Seats already spoken for: called tokens, plus students marked boarded after the last occupancy reading
    (sensors haven't counted them yet, so their seat still looks free)."""
    return await pool.fetchval("""
        select count(*) from digital_tokens where bus_id = $1 and service_date = $2
          and (status = 'called' or (status = 'boarded' and ($3::timestamptz is null or closed_at > $3)))""",
        bus_id, day, sensors_since)


async def call_next(bus_id: int, day: date, n: int) -> list[dict]:
    if n <= 0:
        return []
    rows = await pool.fetch("""
        update digital_tokens set status = 'called', called_at = now()
        where id in (select id from digital_tokens where bus_id = $1 and service_date = $2 and status = 'waiting'
                     order by queue_position limit $3 for update skip locked)
        returning id, queue_position""", bus_id, day, n)
    return [dict(r) for r in rows]


async def expire_tokens(call_window_s: int, today: date) -> list[dict]:
    rows = await pool.fetch(f"""
        update digital_tokens set status = 'expired', closed_at = now()
        where (status = 'called' and called_at < now() - make_interval(secs => $1))
           or (status in {_ACTIVE} and service_date < $2)
        returning id, bus_id""", call_window_s, today)
    return [dict(r) for r in rows]


async def close_queue(bus_id: int, day: date) -> int:
    """The bus has left: whoever is still waiting or called missed it."""
    return int((await pool.execute(f"update digital_tokens set status = 'expired', closed_at = now() "
                                   f"where bus_id = $1 and service_date = $2 and status in {_ACTIVE}",
                                   bus_id, day)).split()[-1])


async def mark_token(bus_id: int, token_id: int, status: str, at: datetime) -> bool:
    # `at` comes from the backend clock, the same clock as occupancy_seen_at it's compared against.
    row = await pool.fetchval("""
        update digital_tokens set status = $3::token_status, closed_at = $4
        where id = $2 and bus_id = $1 and status = 'called' returning id""", bus_id, token_id, status, at)
    return row is not None


async def queue_board(bus_id: int, day: date) -> list[dict]:
    rows = await pool.fetch("""
        select t.id, t.queue_position as position, t.status::text as status, t.created_at, t.called_at,
               p.name, p.student_id
        from digital_tokens t join profiles p on p.id = t.student_id
        where t.bus_id = $1 and t.service_date = $2 order by t.queue_position""", bus_id, day)
    return [{**dict(r), "created_at": r["created_at"].isoformat(),
             "called_at": r["called_at"].isoformat() if r["called_at"] else None} for r in rows]


# ---------- last-bus fallback ----------

def _point(lat: float | None, lng: float | None) -> str | None:
    return None if lat is None or lng is None else f"SRID=4326;POINT({lng} {lat})"


async def find_fallback_route(student_id: str, lat: float | None, lng: float | None) -> list[dict]:
    rows = await pool.fetch("select * from find_fallback_route($1::uuid, $2::text::geography)",
                            student_id, _point(lat, lng))
    return [dict(r) for r in rows]


async def fallback_candidates(exclude_route: int, lat: float, lng: float) -> list[dict]:
    rows = await pool.fetch("select * from fallback_candidates($1, $2::text::geography)",
                            exclude_route, _point(lat, lng))
    return [dict(r) for r in rows]


async def set_home(student_id: str, lat: float | None, lng: float | None):
    await pool.execute("update profiles set home_location = $2::text::geography, updated_at = now() "
                       "where id = $1::uuid", student_id, _point(lat, lng))
