-- M1: core schema. Idempotent: safe to re-run; never drops data.
-- PostGIS goes in the "extensions" schema (Supabase's recommendation): in "public" its spatial_ref_sys
-- table would be writable by anyone holding the publishable key, and it can't be locked down there.
-- Supabase already has "extensions" on the search_path; plain Postgres needs it added (see migrate.py).
create schema if not exists extensions;
create extension if not exists postgis with schema extensions;

do $$ begin
  create type bus_status as enum ('scheduled', 'boarding', 'departed', 'full', 'out_of_service');
exception when duplicate_object then null; end $$;

do $$ begin
  create type token_status as enum ('waiting', 'called', 'boarded', 'no_show', 'expired', 'cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type incident_kind as enum ('sos', 'deviation', 'device_offline');
exception when duplicate_object then null; end $$;

create table if not exists routes (
  id         int primary key,
  name       text not null,
  polyline   jsonb,                        -- GeoJSON LineString
  is_active  boolean not null default true
);

create table if not exists stops (
  id                     int generated always as identity primary key,
  route_id               int not null references routes(id),
  seq                    int not null,
  stop_name              text not null,
  location               geography(Point, 4326) not null,
  geofence_radius_meters int not null default 60 check (geofence_radius_meters > 0),
  unique (route_id, stop_name),
  unique (route_id, seq)
);
create index if not exists stops_location_gix on stops using gist (location);

create table if not exists buses (
  id                  int primary key,
  registration_number text unique,
  route_id            int not null references routes(id),
  gate                text not null,
  serves_stops        text[] not null,
  max_capacity        int not null check (max_capacity > 0),
  departure_time      time not null,
  status              bus_status not null default 'scheduled',
  mqtt_device_id      text unique
);

-- Latest known state per bus, upserted on every change so a restart restores it.
create table if not exists bus_live_state (
  bus_id            int primary key references buses(id),
  status            bus_status not null default 'scheduled',
  location          geography(Point, 4326),
  speed_kmh         real,
  people_count      int check (people_count >= 0),
  cam_seats_free    int check (cam_seats_free >= 0),
  cam_seats_total   int check (cam_seats_total > 0),
  updated_at        timestamptz not null default now(),
  check (cam_seats_free is null or cam_seats_free <= cam_seats_total)
);

-- Roles are NOT stored here: they live in auth.users.app_metadata, which only admins can change.
create table if not exists profiles (
  id                uuid primary key references auth.users(id) on delete cascade,
  student_id        text not null,
  name              text not null,
  default_route_id  int references routes(id),
  default_stop_id   int references stops(id),
  home_location     geography(Point, 4326),        -- optional, coarse, used only for fallback routing
  exam_slot         time,
  updated_at        timestamptz not null default now()
);

create table if not exists active_trips (
  id                  bigint generated always as identity primary key,
  bus_id              int not null references buses(id),
  route_id            int not null references routes(id),
  departure_scheduled time,
  start_time          timestamptz not null default now(),
  end_time            timestamptz,
  is_last_bus         boolean not null default false,
  check (end_time is null or end_time >= start_time)
);
-- At most one open trip per bus.
create unique index if not exists active_trips_one_open on active_trips (bus_id) where end_time is null;

create table if not exists telemetry (
  bus_id            int not null references buses(id),
  ts                timestamptz not null,
  trip_id           bigint references active_trips(id),
  location          geography(Point, 4326),
  speed_kmh         real,
  ir_net            int,
  cv_seats_free     int,
  fused_seats_free  int,
  people_count      int,
  from_backlog      boolean not null default false,
  received_at       timestamptz not null default now(),
  primary key (bus_id, ts)                       -- also dedupes replayed device messages
) partition by range (ts);

create table if not exists telemetry_default partition of telemetry default;

create or replace function ensure_telemetry_partition(day date) returns void
language plpgsql as $$
declare
  part text := format('telemetry_%s', to_char(day, 'YYYYMMDD'));
begin
  if to_regclass(part) is null then
    execute format(
      'create table %I partition of telemetry for values from (%L) to (%L)',
      part, day::timestamptz, (day + 1)::timestamptz);
    -- Partitions are separately exposed by the Supabase API, so each needs RLS too.
    execute format('alter table %I enable row level security', part);
  end if;
end $$;

create table if not exists digital_tokens (
  id              bigint generated always as identity primary key,
  student_id      uuid not null references profiles(id) on delete cascade,
  trip_id         bigint references active_trips(id),
  queue_position  int,
  status          token_status not null default 'waiting',
  created_at      timestamptz not null default now(),
  called_at       timestamptz,
  closed_at       timestamptz
);
create unique index if not exists digital_tokens_one_active
  on digital_tokens (student_id) where status in ('waiting', 'called');

create table if not exists incidents (
  id               bigint generated always as identity primary key,
  bus_id           int not null references buses(id),
  trip_id          bigint references active_trips(id),
  kind             incident_kind not null,
  detail           text,
  opened_at        timestamptz not null default now(),
  acknowledged_by  uuid,
  acknowledged_at  timestamptz
);

-- The browser never queries these tables; only the backend's server connection does.
-- RLS on with no policies = anon/authenticated API keys get nothing.
alter table routes          enable row level security;
alter table stops           enable row level security;
alter table buses           enable row level security;
alter table bus_live_state  enable row level security;
alter table profiles        enable row level security;
alter table active_trips    enable row level security;
alter table telemetry       enable row level security;
alter table telemetry_default enable row level security;
alter table digital_tokens  enable row level security;
alter table incidents       enable row level security;
