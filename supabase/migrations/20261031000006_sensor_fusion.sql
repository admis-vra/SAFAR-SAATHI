-- M4: IR + camera sensor fusion state. Idempotent.
alter table bus_live_state add column if not exists ir_offset        int not null default 0;
alter table bus_live_state add column if not exists fused_seats_free int;

alter type trip_event_kind add value if not exists 'fusion_recalibrated';
