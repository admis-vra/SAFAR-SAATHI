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
  used for drivers, without `bus_id`. The `/office` page for them is M7. Until then a dispatcher logging into the
  website lands on the student page, which won't work, so use `/docs` for now.
- **Known gap:** trips are detected when they **start at campus** (afternoon departures, which the last-bus
  feature needs). Morning trips that start at stop A aren't auto-detected yet.

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
| Passenger | Students sign up with email + password; profile (name, student ID, route, stop, exam shift) is saved in their Supabase account | Your bus, departure, seats free, alerts, exam-day trip, live map, change stop |
| Driver / Conductor | Staff accounts created by an admin in Supabase (see below) | **Driver:** start boarding, start trip, share GPS (or "Simulate route" for the demo). **Conductor:** open boarding, mark full, manual head count, booked passenger list |

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
| GET | `/buses/{id}/passengers` 🔒 | driver/conductor **of that bus** | students booked on the bus, in stop order |
| POST | `/ingest/telemetry` | bus device (`X-Device-Token`) | one blueprint payload → `{accepted, duplicates, live_updated}` |
| POST | `/ingest/backlog` | bus device (`X-Device-Token`) | array of payloads (≤2000), stored as history |
| GET | `/planner?slot=11:00` | dashboard | students per route for an exam slot + extra-trip suggestions (≥30 students) |
| GET | `/dashboard/summary` | dashboard | buses, totals, half-empty and full bus ids |
| WS | `/ws` | student app, dashboard | pushes `{type:"buses",data:[...]}`, `{type:"bus",data:BusView}`, `{type:"alert",bus_id,message}`, `{type:"incident",id,bus_id,kind,open,late?,location?}`, `{type:"trip_event",bus_id,kind,stop,ts}` |

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

Use two phones (or two browsers): one logged in as a student, one as the Bus 14 driver account you created in Supabase.

1. Student registers: Route 3, stop E → the page shows **Bus 14, Gate 2, departure time, seats free**.
2. Run `count.py` with people in front of the webcam → seats free drops live on the student's phone.
3. Bus 14 hits full → the student's phone shows **"Bus 14 is full right now, take Bus 15"**.
4. Driver taps **Start trip** + **Simulate route** → the bus moves on the student's map, and the student is moved to the next bus.
5. Dashboard: 11 AM shift → **Route 3 has 140 students, add a 10:00 trip with 4 buses**.

## Honesty notes for the pitch

- ETA model is trained on **synthetic** data until real trip logs exist (`ml/artifacts/meta.json` says which). Say so if asked.
- Seat-count accuracy (target ≥90% vs. manual count): measure it on your own clip, count 20 frames by hand, and report the real number.
- Accounts, profiles and the latest bus state are stored in Supabase Postgres and survive restarts.
- Without hardware, buses are driven by `tools/simulate_bus.py`. Say "simulated bus" in the demo. The ESP32
  firmware (M8) publishes the same payload but hasn't been built or tested on hardware yet.
- Login needs internet, since Supabase is a cloud service. Test the venue Wi-Fi early, and keep a phone hotspot as backup.
- `/count` has no device key yet, so anyone on the network could post counts. It needs one before a real pilot.
- Map positions (campus, route line), the campus geofence and all stop coordinates in the database are approximate **sample** coordinates, not surveyed. Geofencing is only as accurate as these, so survey them before a test ride.
