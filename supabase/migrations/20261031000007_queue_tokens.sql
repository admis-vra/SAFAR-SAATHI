-- M5: digital queue tokens. A queue belongs to one bus departure on one service day (a trip row only exists
-- once the bus leaves, but students queue before that). Idempotent.
alter table digital_tokens add column if not exists bus_id       int references buses(id);
alter table digital_tokens add column if not exists service_date date;

-- No two tokens in the same queue can ever share a position, even under concurrent joins.
create unique index if not exists digital_tokens_position
  on digital_tokens (bus_id, service_date, queue_position);
create index if not exists digital_tokens_queue
  on digital_tokens (bus_id, service_date, status, queue_position);

do $$ begin
  alter table digital_tokens alter column bus_id set not null;
  alter table digital_tokens alter column service_date set not null;
  alter table digital_tokens alter column queue_position set not null;
exception when others then
  raise notice 'digital_tokens has rows without bus/date/position; NOT NULL not applied';
end $$;

-- Needed to tell whether today's departure already happened (closes the queue).
create index if not exists active_trips_bus_start on active_trips (bus_id, start_time desc);
