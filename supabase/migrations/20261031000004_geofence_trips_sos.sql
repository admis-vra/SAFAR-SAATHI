-- M3: depot geofence, trip events, SOS details. Idempotent.

create table if not exists depots (
  id                     int primary key,
  name                   text not null,
  location               geography(Point, 4326) not null,
  geofence_radius_meters int not null default 150 check (geofence_radius_meters > 0)
);
alter table depots enable row level security;

-- SAMPLE position of the GEHU campus gate area; replace with the surveyed gate (docs/STOPS_TODO.md).
insert into depots (id, name, location, geofence_radius_meters)
values (1, 'GEHU campus', st_setsrid(st_makepoint(77.9945, 30.2687), 4326)::geography, 150)
on conflict (id) do nothing;

do $$ begin
  create type trip_event_kind as enum ('depot_exit', 'depot_enter', 'stop_enter', 'stop_exit', 'manual_status');
exception when duplicate_object then null; end $$;

create table if not exists trip_events (
  id        bigint generated always as identity primary key,
  bus_id    int not null references buses(id),
  trip_id   bigint references active_trips(id),
  kind      trip_event_kind not null,
  stop_id   int references stops(id),
  ts        timestamptz not null,                 -- when it happened (device time)
  late      boolean not null default false,       -- learned from data > 2 min old: history only, no alerts
  detail    text,
  recorded_at timestamptz not null default now()
);
create index if not exists trip_events_trip on trip_events (trip_id, ts);
alter table trip_events enable row level security;

alter table incidents add column if not exists location geography(Point, 4326);
alter table incidents add column if not exists late boolean not null default false;
create index if not exists incidents_open_sos on incidents (bus_id) where kind = 'sos' and acknowledged_at is null;
