# Master Prompt — SafarSaathi v2 (TransitLink blueprint features)

> Paste everything below this line into a fresh Claude Code session opened in the
> `SAFAR SAATHI` repo folder. One AI does all work, milestone by milestone.

---

## Role and goal

You are the sole engineer on **SafarSaathi**, an AI campus-bus system for Graphic Era Hill University,
Dehradun (SAARTHI'26 hackathon; team: Siddhant — lead/backend, Rahul — hardware, Vansh — AI/CV,
Vidarbh/"Vidhu" — frontend). Your job: add the features from the **TransitLink Architecture Blueprint**
to the **existing, working** SafarSaathi codebase, one milestone at a time, keeping the system demoable
after every milestone.

"TransitLink" is the blueprint's codename. The product name stays **SafarSaathi**.

## Read first

1. `README.md`: run steps, API contract, `seat_source` table, Supabase setup, demo script, honesty notes.
2. `git log --oneline`: 4 commits so far (scaffold, seat counts, website, Supabase auth).
3. `backend/main.py`, `backend/auth.py`, `frontend/web/src/**`, `vision/count.py`, `ml/train_eta.py`.

## Current state (do not break)

- **Backend:** FastAPI (Python 3.13, `backend/.venv`). Bus state is **in memory** (`buses`, `students` dicts).
  It has allocation with full/departed alerts, a WebSocket `/ws`, an exam-shift planner, and an ETA model
  (XGBoost on **synthetic** data, used only if it beats the baseline).
- **Auth:** Supabase Auth. The backend verifies access tokens (JWKS or legacy HS256) in `auth.py`.
  **Roles come only from `app_metadata`** (`role: driver|conductor`, `bus_id`); everyone else is a passenger.
  Student profiles are in `user_metadata`.
- **Web app:** React 19 + Vite + react-leaflet in `frontend/web`. Vite proxies `/api` and `/ws` to `:8000`.
  Pages: login (Passenger / Driver-Conductor tabs), passenger, staff (driver vs conductor controls, Hindi labels).
- **Vision:** `vision/count.py` sends YOLOv8n people counts and optional seat-map `seats_free/seats_total`
  to `POST /buses/{id}/count`.
- **Windows gotchas:** use `py -3.13`; `uvicorn --reload` hangs, so restart manually; `.env` files are git-ignored.

## Stack decision (binding unless the user says otherwise)

The blueprint lists Node/Express, Next.js 14, React Native, Zustand and React-Map-GL. **Do not rewrite.**
Implement the blueprint's *features* on the existing stack:

| Blueprint | Use instead | Why |
|---|---|---|
| Node.js + Express + Socket.io | FastAPI + existing `/ws` WebSocket | Working, tested, team knows it |
| Next.js dispatcher + React Native apps | Existing Vite React PWA (new `/office` page) | One codebase; phones use the PWA |
| React-Map-GL | Existing react-leaflet | No Mapbox token needed |
| Supabase Postgres + PostGIS | **Adopt** | Real persistence and geospatial queries |
| Redis | Skip unless a measured need appears | Postgres + in-process cache is enough at this scale |
| MQTT | **Adopt** (`aiomqtt` in backend, Mosquitto broker) | Matches the hardware |
| FCM push | In-app WebSocket alerts first; FCM/Web Push only if keys are provided | No Firebase project yet |

If you believe a deviation is wrong, say so and stop. Don't silently switch stacks.

## Non-negotiable rules

1. **Milestone loop:** plan → implement → write or extend tests → run them → manually verify (curl and/or the
   browser at mobile size) → update README → commit **only that milestone's files** with a clear message → report.
   Then continue to the next milestone. Stop and ask only when genuinely blocked (missing credentials,
   hardware, or a product decision).
2. **Never break the demo.** Every existing endpoint in the README's API table keeps working, or the README
   and frontend are updated in the same milestone.
3. **Secrets:** never print, commit or hard-code keys. Server-only secrets (`DATABASE_URL`, service role key,
   MQTT passwords) go in `backend/.env` only, and you add their names to `backend/.env.example`. The browser
   only ever gets the anon/publishable key.
4. **Supabase safety:** put schema changes in versioned SQL files in `supabase/migrations/`. Ask before running
   anything destructive (DROP, TRUNCATE, DELETE without WHERE) against a live project. Enable RLS on every
   public table. The backend uses its server connection; the browser never queries tables directly.
5. **Security:** roles only from `app_metadata`; add `dispatcher` as a new role. Devices authenticate to MQTT
   with per-bus credentials and may only publish their own topic. Validate every payload at the boundary.
6. **Privacy (DPDP Act 2023):** counts only, no video or frames leave the bus. Students' home location is
   optional and coarse (default to their stop's location), used only for fallback routing, and deletable.
7. **Honesty:** never invent accuracy numbers, datasets or coordinates. Seed data must be marked as sample.
   Anything unmeasured is reported as "not measured".
8. **Keep it simple:** no abstractions or features beyond this spec. A working demo beats completeness.

## Milestones

### M1 — Database and geodata (Supabase Postgres + PostGIS)
- Migration: enable `postgis`. Tables (use `geography(Point,4326)` for locations so distances are in meters):
  - `profiles` (id = auth.users.id, student_id, name, default_route_id, default_stop_id, home_location nullable, exam_slot)
  - `buses` (id, registration_number, max_capacity, status enum, mqtt_device_id)
  - `routes` (id, name, polyline GeoJSON, is_active)
  - `stops` (id, route_id, seq, stop_name, location, geofence_radius_meters default 60)
  - `active_trips` (id, bus_id, route_id, start_time, end_time, is_last_bus, departure_scheduled)
  - `telemetry` partitioned by day on `ts` (id, trip_id, bus_id, location, speed_kmh, ir_net, cv_seats_free, fused_seats_free, ts, received_at, from_backlog)
  - `digital_tokens` (id, student_id, trip_id, queue_position, status enum, created_at, called_at, closed_at)
  - `incidents` (id, bus_id, trip_id, kind enum sos|deviation|device_offline, opened_at, acknowledged_by, acknowledged_at)
- **Roles are not a table column.** They stay in `app_metadata` (admin-only); state this deviation from the blueprint in the README.
- Seed the 4 routes, 7 buses and A–G stops from `backend/seed.json`, using **clearly labelled sample
  coordinates** around the GEHU campus. Add a `docs/STOPS_TODO.md` checklist for the team to replace them
  with surveyed coordinates.
- Backend: add an async DB layer (`asyncpg` or `psycopg` pool from `DATABASE_URL`). Load reference data from
  the DB at startup. Hot state (latest bus position, counts) stays in memory but is **written through** to
  `telemetry` and `active_trips`, so a restart restores the last known state.
- Tests: migrations apply cleanly to an empty dev database; the restart-restores-state test passes.

### M2 — Telemetry ingest over MQTT, plus a hardware simulator
- Topic scheme: `safarsaathi/bus/{bus_id}/telemetry` (live) and `safarsaathi/bus/{bus_id}/backlog` (bulk replay).
  The payload is exactly the blueprint's JSON (`bus_id, timestamp, gps{lat,lon,speed,sats}, pax{in_count,out_count,net_current}, sos_active`).
  Backlog is a JSON array of the same objects.
- Backend: an `aiomqtt` subscriber task with pydantic validation, dedupe on `(bus_id, timestamp)`,
  out-of-order tolerance, and backlog rows flagged `from_backlog=true`. Keep an HTTP fallback
  `POST /ingest/telemetry` authenticated with a per-device token.
- `tools/simulate_bus.py`: publishes realistic trips along the seeded route polylines every 2 s, with
  boarding/alighting pax counts, a `--sos` switch, and a `--drop-network 60` switch that buffers and then
  bulk-publishes a backlog. This is how the demo runs without hardware.
- Mark a device offline (incident) after 30 s of silence; clear it on reconnect.
- README: how to run Mosquitto locally on Windows and how to start the simulator.

### M3 — Zero-touch trips (geofencing) + SOS
- Geofence engine: entering or leaving the campus depot geofence or a stop geofence (from `stops.geofence_radius_meters`)
  creates events. Leaving the depot opens or advances an `active_trip` ("departed"); arriving at the final stop closes it.
  Use hysteresis (e.g. 2 consecutive fixes, accuracy-aware) so GPS jitter doesn't flap the status.
- The driver's manual status buttons remain as an **override**, and overrides are logged.
- **Backlog data never fires real-time notifications.** Events older than 2 min update history only.
- SOS from the payload (`sos_active`) or the driver app's giant SOS button opens an incident and pushes an alarm
  to dispatchers. It stays open until a dispatcher acknowledges it.
- Tests: jitter at the fence edge doesn't flap; a replayed backlog doesn't send notifications; SOS needs acknowledgement.

### M4 — Sensor fusion (IR + CV)
- `expected_empty = max_capacity − ir_net_current`; `visual_empty = cv seats_free` (1 FPS sampled, median-smoothed).
- Weighting: **stationary** (speed < 3 km/h for ≥ 5 s) → trust CV; **moving** → trust IR.
  If |expected − visual| > threshold (default 4) while stationary for ≥ 10 s, recalibrate the IR offset to CV.
  Reset the IR baseline at trip start.
- Expose `fused_seats_free`, `fusion_mode`, `deviation` and the inputs on the bus view; add `seat_source: "fused"`.
  Keep the existing sources as fallbacks when an input is missing or stale (>10 s).
- Unit tests with synthetic sequences: occlusion while stationary, IR double-counts at the door while moving,
  a stale CV feed, and a stale IR feed. Fusion accuracy on real footage is "not measured" until Vansh measures it.

### M5 — Digital queue tokens (afternoon departures)
- Students can `JOIN QUEUE` for an upcoming afternoon trip on their route, from 30 min before departure.
  One active token per student (partial unique index). Positions are assigned inside a transaction, so
  concurrent joins never duplicate a position.
- Calling: when a bus is boarding, call the next N waiting tokens where N = `fused_seats_free`.
  Called tokens have a 5-minute window, then become `expired`. The conductor marks `boarded` or `no_show`.
  Students can cancel.
- Student sees their position, an estimated wait and status changes live (WebSocket).
  Dispatcher sees a column per trip (waiting / called / boarded / expired).
- Tests: 50 concurrent joins give positions 1..50 with no gaps or duplicates; expiry; cancel; one-active-token rule.

### M6 — Fallback engine (last bus missed)
- SQL function `find_fallback_route(student_id, current_location)` per the blueprint: if the student's
  default route's `is_last_bus` trip has ended, search stops on **other** active routes, order by
  `ST_Distance` to their home location (or to their default stop if no home is set), keep only trips with
  `fused_seats_free > 0` that haven't departed, and return the top 3 options (route, stop, walking distance, departure).
- Trigger it when the last bus exits the depot geofence (M3). Notify affected students who haven't boarded
  through the WebSocket, and through FCM/Web Push only if keys are configured. Show a **fallback alert modal** in
  the student app with the options.
- Tests: seeded scenario returns the expected nearest option; full buses are excluded; it returns empty when nothing fits.

### M7 — Frontend
- **`/office` dispatcher page** (`role: dispatcher`): live map of all buses with **smooth marker interpolation**
  (animate between fixes, no teleporting), status/seat tiles, queue manager columns (M5), incident/SOS feed
  with acknowledge, the exam-shift planner chart from `/planner`, and a fusion debug panel (M4).
- **Student:** JOIN QUEUE card with live position, fallback modal, existing bus card and map.
- **Driver:** "zero-touch" view (trip state shown automatically, no required taps) plus a **giant SOS button**
  with confirm-hold (2 s) to avoid accidental presses. Hindi labels as before. Note that phones can't do
  reliable background geofencing in a browser; the ESP32 GPS is the source of truth.
- Verify each page in the browser at mobile size and at desktop width (dispatcher page).

### M8 — ESP32 firmware (for Rahul to flash)
- `firmware/` PlatformIO project (Arduino framework on ESP32, FreeRTOS tasks):
  - `Task_GPS`: NEO-6M NMEA via TinyGPS++ updates shared state (mutex).
  - `Task_IR_Counter`: two IR break-beams A/B with debounce and direction (A→B = +1, B→A = −1) via ISR + queue.
  - `Task_MQTT_Publish`: every 2 s over SIM800L (TinyGSM + PubSubClient), with the blueprint payload.
    When offline, push to a ring buffer (RAM, optional SPIFFS), then bulk-publish to `/backlog` on reconnect.
  - SOS input pin with debounce.
- Per-device MQTT credentials from a git-ignored `secrets.h` (commit `secrets.example.h` only).
- Compile it if PlatformIO is installed; otherwise say "not compiled". Write `firmware/BENCH_TEST.md`, a
  step-by-step bench test checklist for Rahul. You cannot flash or test the hardware, so say so plainly.

### M9 — Hardening and demo
- End-to-end demo script in the README using the simulator: departure by geofence, live seats via fusion,
  the queue being called, a full bus, a last-bus fallback alert, and an SOS acknowledged by the dispatcher.
- Update the README honesty notes: synthetic ETA data, sample coordinates, unmeasured fusion and seat accuracy,
  no hardware tested, and what a real pilot needs (TLS MQTT, device provisioning, rate limits, monitoring).

## Report format after each milestone

- **Done:** what now works, in user terms.
- **Verified:** the exact tests and manual checks, and their results.
- **Not verified / limits:** be explicit.
- **Commit:** hash and message.
- **Needs from the team:** credentials, hardware, coordinates, decisions.

## What you need from the user before M1

Ask once, up front, and continue with whatever doesn't need it:
1. A Supabase **dev** project (separate from the demo project if possible): `DATABASE_URL` (pooled connection
   string) in `backend/.env`. The user enters it themselves; never ask them to paste it in chat.
2. Whether PostGIS can be enabled on that project (Database → Extensions).
3. Mosquitto installed locally, or permission to install it.
