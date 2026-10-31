-- M1: SAMPLE reference data for the demo. Idempotent.
-- Stop coordinates are NOT surveyed: they are evenly spaced points between the GEHU campus and an
-- approximate route endpoint. Replace them with real coordinates (see docs/STOPS_TODO.md).

insert into routes (id, name) values
  (1, 'Route 1 - Clock Tower'),
  (2, 'Route 2 - ISBT'),
  (3, 'Route 3 - Prem Nagar'),
  (4, 'Route 4 - Rajpur Road')
on conflict (id) do nothing;

insert into stops (route_id, seq, stop_name, location)
select route_id, seq, stop_name, st_setsrid(st_makepoint(lng, lat), 4326)::geography
from (values
  (1, 1, 'A', 30.32430, 78.04180), (1, 2, 'B', 30.31642, 78.03510), (1, 3, 'C', 30.30855, 78.02840),
  (1, 4, 'D', 30.30067, 78.02170), (1, 5, 'E', 30.29279, 78.01500), (1, 6, 'F', 30.28492, 78.00830),
  (1, 7, 'G', 30.27704, 78.00159),
  (2, 1, 'A', 30.28790, 77.99900), (2, 2, 'B', 30.28518, 77.99836), (2, 3, 'C', 30.28246, 77.99773),
  (2, 4, 'D', 30.27974, 77.99709), (2, 5, 'E', 30.27702, 77.99645), (2, 6, 'F', 30.27430, 77.99581),
  (2, 7, 'G', 30.27158, 77.99518),
  (3, 1, 'A', 30.33650, 77.96000), (3, 2, 'B', 30.32690, 77.96489), (3, 3, 'C', 30.31729, 77.96977),
  (3, 4, 'D', 30.30768, 77.97466), (3, 5, 'E', 30.29808, 77.97955), (3, 6, 'F', 30.28847, 77.98444),
  (3, 7, 'G', 30.27887, 77.98932),
  (4, 1, 'A', 30.35600, 78.06400), (4, 2, 'B', 30.34363, 78.05415), (4, 3, 'C', 30.33127, 78.04431),
  (4, 4, 'D', 30.31890, 78.03446), (4, 5, 'E', 30.30653, 78.02462), (4, 6, 'F', 30.29416, 78.01477),
  (4, 7, 'G', 30.28179, 78.00493)
) as v(route_id, seq, stop_name, lat, lng)
on conflict (route_id, stop_name) do nothing;

-- Route polyline = stops in order (GeoJSON LineString, [lng, lat]).
update routes r
set polyline = (
  select json_build_object('type', 'LineString',
           'coordinates', json_agg(json_build_array(st_x(s.location::geometry), st_y(s.location::geometry))
                                   order by s.seq))::jsonb
  from stops s where s.route_id = r.id)
where r.polyline is null;

insert into buses (id, route_id, gate, serves_stops, max_capacity, departure_time) values
  (9,  3, 'Gate 1', '{A,B,C}',         40, '13:15'),
  (14, 3, 'Gate 2', '{D,E,F,G}',       40, '13:15'),
  (15, 3, 'Gate 2', '{D,E,F,G}',       40, '13:20'),
  (3,  1, 'Gate 1', '{A,B,C,D,E,F,G}', 40, '13:10'),
  (5,  2, 'Gate 1', '{A,B,C}',         40, '13:10'),
  (6,  2, 'Gate 1', '{D,E,F,G}',       40, '13:15'),
  (11, 4, 'Gate 2', '{A,B,C,D,E,F,G}', 40, '13:20')
on conflict (id) do nothing;

insert into bus_live_state (bus_id) select id from buses on conflict (bus_id) do nothing;
