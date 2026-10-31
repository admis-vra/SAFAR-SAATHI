-- Batch 1: control-room announcements and issue reports. Idempotent.

do $$ begin
  create type announcement_kind as enum ('delay', 'cancellation', 'info');
exception when duplicate_object then null; end $$;

create table if not exists announcements (
  id          bigint generated always as identity primary key,
  route_id    int references routes(id),          -- null = every route
  bus_id      int references buses(id),           -- optional: a specific bus
  kind        announcement_kind not null,
  message     text not null check (length(message) between 3 and 280),
  created_by  uuid,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null
);
create index if not exists announcements_active on announcements (expires_at);
alter table announcements enable row level security;

do $$ begin
  create type issue_category as enum ('overcrowding', 'driving', 'cleanliness', 'breakdown', 'safety', 'staff', 'other');
exception when duplicate_object then null; end $$;

do $$ begin
  create type issue_status as enum ('open', 'in_progress', 'resolved');
exception when duplicate_object then null; end $$;

create table if not exists issues (
  id            bigint generated always as identity primary key,
  reporter_id   uuid not null,
  reporter_role text not null,
  bus_id        int references buses(id),
  category      issue_category not null,
  description   text not null check (length(description) between 5 and 1000),
  status        issue_status not null default 'open',
  office_note   text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists issues_open on issues (status, created_at desc);
create index if not exists issues_reporter on issues (reporter_id, created_at desc);
alter table issues enable row level security;
