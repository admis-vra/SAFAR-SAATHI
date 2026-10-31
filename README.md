# SafarSaathi — AI Smart Campus Bus System

SAARTHI'26 · Graphic Era Hill University, Dehradun · Team: Siddhant (lead), Vidarbh, Vansh, Rahul

## Who does what

Everyone builds against the **API contract below**, so nobody waits on anybody else.

| Person | Owns | Folder | First milestone |
|---|---|---|---|
| **P1 — Siddhant (lead)** | Backend, integration, final demo flow | `backend/` | API running on the LAN, everyone pointing at it |
| **P2** | Website polish + demo device testing (passenger and staff pages already built) | `frontend/web` | Test on real phones on the hackathon Wi-Fi; fix anything that looks off |
| **P3** | Vision: YOLOv8n seat counting on bus video/webcam | `vision/` | `count.py` posting live counts to the backend |
| **P4** | Office dashboard + ETA model | `frontend/web` (new `/office` page), `ml/` | Dashboard chart from `/planner` and `/dashboard/summary` |

Lead rule: **integrate every 3–4 hours**. A working end-to-end demo with fewer features is better than 5 half-done pieces.

## Run

Use Python **3.12 or 3.13** (`py -3.13`). PyTorch/YOLO support for 3.14 is not guaranteed.

```bash
cd backend && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements.txt
cp .env.example .env    # then set SUPABASE_URL and DATABASE_URL (see "Supabase setup" and "Database")
.venv/Scripts/python migrate.py   # creates tables + sample data; safe to re-run
.venv/Scripts/uvicorn main:app --host 0.0.0.0 --port 8000
```

### Database (Supabase Postgres + PostGIS)

- Schema and sample data live in `supabase/migrations/*.sql`. `migrate.py` applies new files in order and
  records them in `schema_migrations`. Each file is also safe to paste into the Supabase SQL editor again.
- Enable **PostGIS** first: Supabase → Database → Extensions → `postgis`. The first migration also runs
  `create extension if not exists postgis`.
- `DATABASE_URL`: Supabase → **Connect** → *Transaction pooler* (port 6543). Server-side only.
- Routes, stops and buses load from the database at startup. Each bus's latest status, location and counts are
  written to `bus_live_state` on every change, and history goes to `telemetry` (partitioned by day), so a
  backend restart restores the live picture. Student profiles live in `profiles`.
- **Stop coordinates are samples.** Replace them using [docs/STOPS_TODO.md](docs/STOPS_TODO.md).
- Deviation from the blueprint: there is **no `role` column**. Roles stay in Supabase `app_metadata`, which only
  admins can change.

Tests (need a disposable database with PostGIS, never the demo project):

```bash
cd backend && .venv/Scripts/pip install -r requirements-dev.txt
# backend/.env.test: TEST_DATABASE_URL=...   (+ TEST_AUTH_STUB=1 for plain local Postgres)
# Optional switches:
#   TEST_ALLOW_AUTH_ROWS=1  queue tests may create placeholder auth.users rows (no email/password), deleted after
#   TEST_SKIP_MIGRATE=1     don't auto-apply pending migrations (e.g. another branch's unfinished one)
.venv/Scripts/python -m pytest tests -q
```

Restart the server after editing backend code. `--reload` tends to hang on Windows.

API docs (try every endpoint in the browser): http://localhost:8000/docs
Teammates on the same Wi-Fi use `http://<lead-laptop-IP>:8000` (find it with `ipconfig`).

```bash
cd ml && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/python train_eta.py
cd vision && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/python count.py --bus 14 --source 0 --show --interval 5
```

### Bus devices: MQTT telemetry + simulator (no hardware needed)

Each bus (ESP32, or the simulator) publishes the blueprint payload every 2 s:

| Topic | Payload |
|---|---|
| `safarsaathi/bus/{device}/telemetry` | `{"bus_id": "B-14", "timestamp": 1761900000, "gps": {"lat", "lon", "speed", "sats"}, "pax": {"in_count", "out_count", "net_current"}, "sos_active": false}` |
| `safarsaathi/bus/{device}/backlog` | JSON array of the same objects, sent once after a network drop |

Device ids are `B-<bus id>` (column `buses.mqtt_device_id`). The payload's `bus_id` must match the topic.

**Real hardware:** `firmware/` is the ESP32 PlatformIO project (FreeRTOS tasks `Task_GPS`, `Task_IR_Counter`,
`Task_MQTT_Publish`; NEO-6M, two IR beams, SIM800L, SOS button, 15-minute offline buffer). Its core logic is unit-tested
on a PC (`firmware/test/host`), and the backend's own validator accepts its JSON. **It hasn't been compiled or
flashed yet.** Wiring, power warnings and the bench checklist are in `firmware/BENCH_TEST.md`.

```bash
# terminal 1: dev broker (localhost only; allows anonymous clients, never use it for a pilot)
backend/.venv/Scripts/python tools/broker.py
# terminal 2: backend with MQTT_URL=mqtt://127.0.0.1:1883 in backend/.env
# terminal 3: a simulated bus (add --loop to keep driving, --speedup 5 to move faster)
backend/.venv/Scripts/python tools/simulate_bus.py --bus 14
backend/.venv/Scripts/python tools/simulate_bus.py --bus 14 --speedup 8 --drop-network 20 --sos-at D
```

What the backend does with it:
- **Validation:** bad coordinates, times more than 5 min in the future or 24 h in the past, malformed JSON, or a
  payload claiming another bus are all dropped.
- **History:** every reading goes to `telemetry` with the device's own timestamp. Repeats of the same
  `(bus, second)` are stored once, so a device re-sending after a reconnect does no harm.
- **Live state:** only a reading **newer** than the last one moves the bus. Late and backlog data is history only.
  No GPS fix (fewer than 4 satellites, or 0,0) keeps the last known location.
- **Offline detection:** 30 s of silence opens a `device_offline` incident, which closes when the device is heard again.
- **HTTP fallback** for devices that can't do MQTT: `POST /ingest/telemetry` (one reading) or `/ingest/backlog`
  (array), with header `X-Device-Token`. Create a token with
  `backend/.venv/Scripts/python backend/provision_device.py --bus 14`. It's shown once and only its hash is stored.
- **For a real pilot:** use Mosquitto with a password per device and an ACL so each bus can publish only its own
  topic. See `tools/mosquitto/`. Over GSM the broker must be reachable from the internet, unlike the laptop broker.

### Zero-touch trips (geofencing) + SOS

Trips start and end from the bus's own GPS, so the driver doesn't need to tap anything:

| Geofence event | Effect |
|---|---|
| Bus **leaves the campus** fence (`depots`, 150 m) | Opens a trip, status `departed`. `is_last_bus` is set if it's the route's latest departure. |
| Bus **enters / leaves a stop** fence (`stops.geofence_radius_meters`, 60 m) | `stop_enter` / `stop_exit` recorded in `trip_events` |
| Bus reaches the route's **far end (stop A)** or **re-enters campus** | Closes the trip, status `scheduled` |

- **Jitter-proof:** a change counts only after 2 consecutive GPS fixes agree, and leaving needs the bus 20% beyond the
  radius. A bus wobbling at a fence edge doesn't flap.
- **Late data** (readings more than 2 min old, e.g. a backlog after a network drop) still updates trips and
  `trip_events` (with `late = true`) but **never pushes live alerts**.
- **Manual override:** the driver's status buttons still work, and each press is logged as a `manual_status` event.
- **SOS:** from the device (`sos_active`) or the driver (`POST /buses/{id}/sos`), it opens one `sos` incident per bus
  with its location. It stays open until a **dispatcher** acknowledges it, even if the button is released. A late
  SOS is still raised and marked "reported late": a delayed alarm is still an alarm.
- **Dispatcher accounts:** set `app_metadata` `{"role": "dispatcher", "name": "Control Room"}` with the same SQL
  used for drivers, without `bus_id`. They log in on the **Driver / Conductor** tab and land on `/office`.
- **Known gap:** trips are detected when they **start at campus** (afternoon departures, which the last-bus
  feature needs). Morning trips that start at stop A aren't auto-detected yet.

### Digital queue tokens (afternoon departures)

Instead of rushing the bus door, students join a queue for their assigned bus and get called when there's a seat.

- **One queue per bus departure per day.** It opens 30 min before the scheduled departure and closes when the bus
  actually leaves (geofence or driver), so a late bus keeps its queue. Anyone still waiting when it leaves has
  missed it (their token becomes `expired`; M6 will offer them a fallback route).
- **Fair positions:** joins are serialized with a per-queue database lock, and a unique index makes duplicate
  positions impossible. Each student can hold one active token.
- **Calling:** while the bus status is `boarding`, the backend calls the next N waiting tokens, where
  N = free seats (fused count, M4) minus seats already promised: called tokens, plus students marked boarded since
  the last sensor reading (their seat still looks free until IR/camera count them, so without this the queue would
  over-call). Called students have **5 min** to board, then the
  token expires and the next person is called. The conductor marks `boarded` or `no_show`. Calls are re-checked
  every 5 s and immediately on any change.
- **Token states:** `waiting → called → boarded | no_show | expired`, or `cancelled` by the student.

### Last-bus fallback

When the **last bus for a stop** leaves campus live (the latest departure on that route serving that stop; a route
can split its stops across buses, e.g. Bus 9 for A–C and Buses 14/15 for D–G), every app gets
`{type: "last_bus_left", route_id, bus_id}`. Students on that route who aren't aboard call `GET /me/fallback`.

- **Geography in PostGIS:** `find_fallback_route(student, current_location)` (blueprint §2.2) starts from where
  the student is now, else their optional home, else their usual stop. It returns the nearest stops on **other**
  routes with the buses that serve them (`fallback_candidates`). Only the backend can call it; Supabase's public
  API is refused.
- **Live filtering in the backend:** drops buses that have already left today, have no free seat (fused count),
  or are more than **2 km** away on foot. Returns the 3 nearest, each with walking distance and time, gate,
  departure and seats.
- **No option?** The answer says so and points to the transport office. It never invents a route.
  With today's timetable (all buses within 13:10–13:20) and sample stops, "no option" is common. Staggered
  departures and real stop coordinates would make this feature much more useful.
- **Privacy:** current location is used for one answer and never stored. Home is optional, rounded to ~100 m,
  and deletable (`PUT/DELETE /me/home`).
- Replayed (late) data never raises the last-bus alert.

### Website (passenger + driver/conductor)

```bash
cd frontend/web && npm install
cp .env.example .env.local    # then set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY
npm run dev
```

Open http://localhost:5173. Phones on the same Wi-Fi open `http://<laptop-IP>:5173`. The dev server forwards
`/api` and `/ws` to the backend, so the phone needs only this one address. If the backend runs on another laptop,
set `SAFAR_API=http://<backend-IP>:8000` before `npm run dev`. Allow Node through Windows Firewall when asked.

| Login tab | Who | Page |
|---|---|---|
| Passenger | Students sign up with email + password; profile (name, student ID, route, stop, exam shift) is saved in their Supabase account | Your bus, departure, seats free, alerts, **JOIN QUEUE** card (position, "likely seat", "your turn" 5-min countdown), **last-bus fallback popup** (optional "use my location", never stored), exam-day trip, live map, change stop |
| Driver / Conductor | Staff accounts created by an admin in Supabase (see below) | **Zero-touch trip view** (at campus / at stop / on the road, set by GPS), **Open boarding** (starts calling the queue), **hold-2-seconds SOS**, queue panel with Boarded / No-show, manual override (start trip / full / reset). Driver: GPS share or "Simulate route". Conductor: manual head count. Booked passenger list |
| Dispatcher | `app_metadata.role = dispatcher` (see Zero-touch section) | **`/office` control room:** pulsing SOS banners with Acknowledge, incident feed, live fleet map with all routes (buses glide between GPS fixes), a tile per bus (seats + source, fusion mode, device online, trip state), queue manager columns for the clicked bus, exam-shift planner chart |

Phone GPS needs HTTPS or localhost. Over `http://<IP>:5173` the browser blocks it, so use **Simulate route** in the demo.

### Supabase setup (one-time, about 10 minutes)

Supabase handles signup, login and passwords. The backend never sees a password; it only verifies the signed
access token Supabase gives the browser.

1. Create a project at supabase.com. In **Authentication → Sign In / Providers**, keep **Email** enabled.
   For the hackathon, turn **Confirm email** off so students can log in straight after signing up
   (with it on, the site tells them to confirm by email first).
2. **Project Settings → API**: copy the **Project URL** and the **anon / publishable key**.
   - `frontend/web/.env.local` → `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`
   - `backend/.env` → `SUPABASE_URL`
   - Older projects that still use the legacy shared JWT secret also need `SUPABASE_JWT_SECRET` in `backend/.env`.
     Projects using JWT signing keys don't. If logins fail with "Invalid login", check this first.
   - **Never** put the `service_role` / secret key in either file or in the frontend.
3. **Create staff accounts**: in **Authentication → Users → Add user**, add each driver/conductor with an email and
   password, then run this in the **SQL Editor** for each one:

   ```sql
   update auth.users
   set raw_app_meta_data = raw_app_meta_data || '{"role": "driver", "bus_id": 14, "name": "Ramesh Negi"}'
   where email = 'driver14@your-domain.example';
   ```

   Use `"role": "conductor"` for conductors. Roles come from `app_metadata`, which only admins can change. A
   student can't make themselves a driver by editing their own profile. Staff must log out and back in after a
   role change.
4. Restart `npm run dev` and the backend after editing the env files.
5. **Bulk-create passenger accounts** from a roster (name + student ID) instead of self-registration: put
   `SUPABASE_SERVICE_ROLE_KEY` in `backend/.env` (never commit it or put it in the frontend), export the roster as
   `name,student_id` CSV, then run `python bulk_import_passengers.py your_roster.csv` from `backend/`. Each
   student's login ID is their student ID and their password is their full name with spaces removed, lowercased
   (e.g. "Siddhant Singh Rawat" -> `siddhantsinghrawat`); the script prints a `passenger_credentials.csv` to hand
   out. The login page builds the matching (synthetic) email straight from the student ID, so no backend lookup
   is needed.

## API contract

Base URL: `http://<host>:8000`. All JSON. 🔒 = needs `Authorization: Bearer <Supabase access token>`
(`supabase.auth.getSession()` → `session.access_token`). Login and signup go to Supabase directly, not this API.

| Method | Path | Who calls it | Body / result |
|---|---|---|---|
| GET | `/me` 🔒 | everyone | the logged-in user: passenger `profile` (or `null` if not set up yet), or staff `bus` |
| GET | `/me/bus` 🔒 | passenger | `{bus, alert}`: re-allocates if their bus left, warns if it is full. `409 profile_required` if no profile |
| PUT | `/me/profile` 🔒 | passenger | `{student_id, name, route_id, stop, exam_slot?}` → `{bus, alert}` |
| GET | `/routes` | everyone | routes with ordered `stops` and `stop_points` (`name, lat, lng, geofence_m`) |
| GET | `/buses` | everyone | all buses (`BusView`) |
| GET | `/buses/{id}` | everyone | one `BusView` |
| POST | `/buses/{id}/count` | vision, conductor | `{count, seats_free?, seats_total?}`: seat fields optional, sent together |
| POST | `/buses/{id}/location` 🔒 | driver/conductor **of that bus** | `{lat, lng}` |
| POST | `/buses/{id}/status` 🔒 | driver/conductor **of that bus** | `{status: scheduled\|boarding\|departed\|full}`: manual override, logged |
| POST | `/buses/{id}/sos` 🔒 | driver/conductor **of that bus** | opens (or returns) the bus's SOS incident → `{incident_id}` |
| GET | `/incidents?open=true` 🔒 | dispatcher | open SOS / device-offline incidents with location (`open=false` = last 200) |
| POST | `/incidents/{id}/ack` 🔒 | dispatcher | acknowledge; the only way an SOS closes |
| GET | `/me/queue` 🔒 | passenger | `{queue: {state: not_open\|open\|closed, opens_at, departs_at}, token: {position, ahead, status, likely_seat}}` |
| POST | `/me/queue` 🔒 | passenger | join your bus's queue (409 if not open, already left, or already queued) |
| DELETE | `/me/queue` 🔒 | passenger | cancel your token |
| GET | `/me/fallback?lat=&lng=` 🔒 | passenger | `{needed, last_bus, options: [{route, stop, walk_m, walk_min, bus_id, gate, departs, seats_free}], message}` (lat/lng optional, not stored) |
| PUT / DELETE | `/me/home` 🔒 | passenger | set `{lat, lng}` (rounded ~100 m) / remove your home location |
| GET | `/buses/{id}/queue` 🔒 | staff of that bus, dispatcher | queue columns: waiting / called / boarded / no_show / expired / cancelled |
| POST | `/buses/{id}/queue/{token}/boarded\|no_show` 🔒 | driver/conductor **of that bus** | close a called token |
| GET | `/buses/{id}/passengers` 🔒 | driver/conductor **of that bus** | students booked on the bus, in stop order |
| POST | `/ingest/telemetry` | bus device (`X-Device-Token`) | one blueprint payload → `{accepted, duplicates, live_updated}` |
| POST | `/ingest/backlog` | bus device (`X-Device-Token`) | array of payloads (≤2000), stored as history |
| GET | `/planner?slot=11:00` | dashboard | students per route for an exam slot + extra-trip suggestions (≥30 students) |
| GET | `/dashboard/summary` | dashboard | buses, totals, half-empty and full bus ids |
| WS | `/ws` | student app, dashboard | pushes `{type:"buses",data:[...]}`, `{type:"bus",data:BusView}`, `{type:"alert",bus_id,message}`, `{type:"incident",id,bus_id,kind,open,late?,location?}`, `{type:"trip_event",bus_id,kind,stop,ts}`, `{type:"queue",bus_id}` (refetch), `{type:"token_called",bus_id,token_id,position,board_within_s}`, `{type:"last_bus_left",route_id,bus_id}` |

`BusView`:

```json
{"id": 14, "route_id": 3, "gate": "Gate 2", "stops": ["D","E","F","G"], "capacity": 40,
 "occupancy": 28, "seats_free": 12, "seat_source": "camera", "seats_seen": 40,
 "status": "boarding", "location": {"lat": 30.27, "lng": 78.0}, "speed_kmh": 24.5,
 "eta": {"scheduled": "13:15", "predicted": "13:18", "delay_min": 3.2, "source": "model|baseline|timetable"},
 "updated_at": "2026-10-31T13:02:11",
 "device": {"id": "B-14", "online": true, "last_reading": "2026-10-31T07:32:10+00:00", "ir_net": 28},
 "trip": {"id": 42, "in_depot": false, "at_stop": "E"}, "sos": false,
 "fusion": {"mode": "cv", "ir_empty": 10, "camera_empty": 14, "deviation": 4, "ir_offset": 0, "stationary_s": 12}}
```

`seats_free` comes from the best source available, named in `seat_source`:

| `seat_source` | Meaning |
|---|---|
| `fused` | IR door counter **and** camera both fresh. Stationary (under 3 km/h for 5 s or more): camera; moving: IR (`fusion.mode` = `cv` / `ir`) |
| `ir_count` | Only the IR counter is fresh: `capacity − (IR net + offset)` |
| `camera` | Seat map covers the whole bus; empty seats detected directly |
| `camera_partial` | Seat map covers `seats_seen` of `capacity` seats; unseen seats assumed to fill at the same rate (an estimate — show "~" in the UI) |
| `people_count` | No seat map; `capacity − people counted` |
| `allocations` | No camera; `capacity − students allocated` |

**Sensor fusion (IR + camera):** IR break-beams double-count people crowding the door; the camera misses people
who are blocked from view. So the bus trusts the camera at rest and the IR counter on the move. "Fresh" means IR
heard within 10 s (it reports every 2 s) and camera within 45 s (`count.py` reports every 30 s). If the two
disagree by more than 4 seats after 10 s at rest, the IR count is re-anchored to the camera (`ir_offset`), and that
is logged as a `fusion_recalibrated` trip event. The offset resets when a trip **ends**, not when it starts,
because a trip starts when the bus leaves campus, i.e. after boarding, which is when calibration is best. Fused results are
stored in `bus_live_state.fused_seats_free` and `telemetry.fused_seats_free`. Fusion **accuracy on real footage
is not measured** yet; the tests use synthetic sequences.

## Demo script (what judges should see)

**Devices:** a laptop showing `/office` (log in as a dispatcher) on the projector, phone 1 logged in as a student,
phone 2 as the Bus 14 conductor. All on the same Wi-Fi as the laptop (`http://<laptop-IP>:5173`).

**Before judges arrive (10 min):**

```bash
backend/.venv/Scripts/python tools/broker.py                      # terminal 1
cd backend && .venv/Scripts/uvicorn main:app --host 0.0.0.0 --port 8000   # terminal 2
cd frontend/web && npm run dev                                     # terminal 3
```

Queues open 30 min before the scheduled departure, so move the demo buses close to the demo slot in the Supabase
SQL editor (example for a demo at 11:00), then restart the backend:

```sql
update buses set departure_time = '11:15' where id = 14;
update buses set departure_time = '11:20' where id = 15;
```

**The story (≈5 min):**

1. **Control room** (laptop): fleet map with all routes, the bus tiles, and the exam-shift planner ("Route 3 has
   140 students for 11:00, add a 10:00 trip with 4 buses").
2. **Student** (phone 1) signs up: Route 3, stop E. The page shows **Bus 14, Gate 2, ETA, seats**. Tap
   **Join queue**: "#1 in line, likely seat".
3. **Bus arrives** (simulated): `tools/simulate_bus.py --bus 14 --speedup 3 --loop` plus the camera,
   `vision/count.py --bus 14 --source 0` (or `tools/fake_camera.py --bus 14 --people 28 --seats-free 12`).
   The office tile shows **Fusion CV** while the bus stands at campus. Point out that the camera is trusted at rest
   and the IR counter on the move.
4. **Conductor** (phone 2) taps **Open boarding**: phone 1 flips to **"Your turn! Board Bus 14 now 4:59"**.
   The conductor taps **Boarded**.
5. **Zero-touch departure:** the simulated bus leaves campus and the trip starts by itself. Status `departed`, the
   map marker glides along the route, and stops G, F, E… light up on the conductor's trip card. Nobody pressed
   anything.
6. **SOS:** the conductor holds **SOS for 2 s**. The control room shows a pulsing red banner with the bus location.
   Acknowledge it.
7. **Last bus:** run the simulator for Bus 15 (the last bus for stops D–G). A second student at stop F who didn't
   board gets the **"Your last bus has left"** popup. "Use my current location" lists the nearest buses on other
   routes that still have seats, or honestly says there are none.
8. **Network drop:** `simulate_bus.py --bus 14 --drop-network 40`. The office shows the device offline, then the
   backlog replays and the trip history fills in, **without** sending any late alerts.

Say "simulated bus" and "simulated camera" (if `fake_camera.py` is used) out loud.

## Honesty notes for the pitch

**Real and tested** (61+ backend tests against Supabase, plus browser checks of every page):
accounts and roles (Supabase), PostGIS database, MQTT ingest with dedupe and backlog, geofenced zero-touch trips,
SOS with acknowledgement, IR + camera fusion logic, digital queue (fair under 50 concurrent joins),
last-bus fallback, and the student / conductor / control-room pages.

**Simulated or not yet measured. Say so if asked:**

- **Buses:** driven by `tools/simulate_bus.py` (same payload as the hardware). The ESP32 firmware (`firmware/`)
  has PC-tested core logic but **has not been compiled or run on a device**; see `firmware/BENCH_TEST.md`.
- **Accuracy:** IR count accuracy, camera seat accuracy and fusion accuracy are **not measured**. Measure them
  on real footage and rides (count by hand, at least 3 stops) and quote only those numbers.
- **ETA model:** trained on **synthetic** data (`ml/artifacts/meta.json` says so).
- **Exam-shift demand** in the planner includes sample data.
- **Coordinates:** campus, stops and route lines are **sample** coordinates, not surveyed
  (`docs/STOPS_TODO.md`). Geofencing and fallback distances are only as good as these.
- **Fallback usefulness:** with today's timetable (all buses within 13:10–13:20), "no other bus" is the
  common answer. Staggered departures would make it genuinely useful.

**Before any real pilot:**

- Replace sample coordinates with surveyed ones, and run test rides to tune geofence radii.
- MQTT over the internet with per-device passwords and an ACL (`tools/mosquitto/`), TLS or a private APN/VPN.
  Consider 4G modules instead of the 2G-only SIM800L.
- A device key on `POST /buses/{id}/count` (camera), and rate limiting on logins.
- **Bulk-imported student accounts must get random one-time passwords** that are changed at first login. Never use
  passwords derived from names or student IDs, which are public.
- A DPDP-compliant privacy notice and consent: what's stored (profile, optional coarse home), what's not (video,
  current location), retention, and how to delete.
- Monitoring and alerting for the backend, broker and device-offline incidents. Back up the database.
- Login needs internet (Supabase is a cloud service): test the venue network early and keep a hotspot as backup.
