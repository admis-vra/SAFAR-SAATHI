-- M2: device identity + raw sensor fields. Idempotent.

-- Device ids follow the blueprint's "B-402" style; the payload's bus_id must match its topic.
update buses set mqtt_device_id = 'B-' || id where mqtt_device_id is null;

-- HTTP fallback auth: only a SHA-256 hash of each device token is stored (backend/provision_device.py).
alter table buses add column if not exists device_token_hash text;

alter table telemetry add column if not exists ir_in  int;
alter table telemetry add column if not exists ir_out int;
alter table telemetry add column if not exists sats   smallint;

alter table bus_live_state add column if not exists ir_net         int;
alter table bus_live_state add column if not exists last_device_ts timestamptz;

-- Incidents that resolve themselves (device back online) get closed_at; SOS still needs acknowledged_at.
alter table incidents add column if not exists closed_at timestamptz;
create index if not exists incidents_open on incidents (bus_id, kind) where closed_at is null;
