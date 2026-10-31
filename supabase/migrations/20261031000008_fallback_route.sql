-- M6: last-bus fallback (blueprint §2.2). Geography only; live seat/departure filtering happens in the backend,
-- which holds the fused seat counts. Idempotent.

-- Stops on routes other than p_exclude_route, nearest to p_origin first, with the buses that serve each stop.
create or replace function fallback_candidates(p_exclude_route int, p_origin geography, p_limit int default 30)
returns table (route_id int, route_name text, stop_id int, stop_name text, walk_m double precision,
               bus_id int, departure time)
language sql stable
set search_path = public, extensions
as $$
  select s.route_id, r.name, s.id, s.stop_name, st_distance(s.location, p_origin), b.id, b.departure_time
  from stops s
  join routes r on r.id = s.route_id and r.is_active
  join buses b on b.route_id = s.route_id and s.stop_name = any (b.serves_stops)
  where s.route_id <> p_exclude_route and p_origin is not null
  order by st_distance(s.location, p_origin), b.departure_time
  limit p_limit
$$;

-- Blueprint signature: where the student is now, else their (optional) home, else their usual stop.
create or replace function find_fallback_route(p_student uuid, p_location geography default null)
returns table (route_id int, route_name text, stop_id int, stop_name text, walk_m double precision,
               bus_id int, departure time)
language sql stable
set search_path = public, extensions
as $$
  select c.*
  from profiles p
  left join stops ds on ds.id = p.default_stop_id
  cross join lateral fallback_candidates(p.default_route_id, coalesce(p_location, p.home_location, ds.location)) c
  where p.id = p_student
$$;

-- Supabase exposes public functions over its REST API; only the backend should call these.
revoke execute on function fallback_candidates(int, geography, int) from public, anon, authenticated;
revoke execute on function find_fallback_route(uuid, geography) from public, anon, authenticated;
