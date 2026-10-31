-- Daily plans: real timetable fields, driver picks a bus each day, students declare their trip home,
-- staff assign buses to departure slots. Idempotent; only adds tables and columns.

alter table buses add column if not exists active boolean not null default true;
-- Morning pickup runs towards college, as start times from the first stop (2nd run is optional).
alter table buses add column if not exists run1_start time;
alter table buses add column if not exists run2_start time;

-- Where a stop's coordinates came from, so estimated ones can be found and corrected.
alter table stops add column if not exists coords_source text not null default 'manual'
  check (coords_source in ('manual', 'osm', 'estimated'));

create table if not exists staff_bus_picks (
  user_id       uuid not null,
  role          text not null check (role in ('driver', 'conductor')),
  name          text,
  service_date  date not null,
  bus_id        int not null references buses(id),
  picked_at     timestamptz not null default now(),
  primary key (user_id, service_date)
);
-- One driver per bus per day (any number of conductors).
create unique index if not exists staff_bus_picks_one_driver
  on staff_bus_picks (service_date, bus_id) where role = 'driver';
alter table staff_bus_picks enable row level security;

-- slot null = "not taking a bus home today".
create table if not exists departure_intents (
  student_id    uuid not null references profiles(id) on delete cascade,
  service_date  date not null,
  slot          time check (slot in ('13:00', '16:00', '18:00')),
  stop_name     text not null,
  updated_at    timestamptz not null default now(),
  primary key (student_id, service_date)
);
create index if not exists departure_intents_day on departure_intents (service_date, slot);
alter table departure_intents enable row level security;

create table if not exists bus_slot_assignments (
  service_date  date not null,
  bus_id        int not null references buses(id),
  slot          time not null check (slot in ('13:00', '16:00', '18:00')),
  assigned_by   uuid,
  assigned_at   timestamptz not null default now(),
  primary key (service_date, bus_id)
);
alter table bus_slot_assignments enable row level security;
