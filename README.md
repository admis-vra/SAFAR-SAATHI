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
.venv/Scripts/uvicorn main:app --host 0.0.0.0 --port 8000
```

Restart the server after editing backend code. `--reload` tends to hang on Windows.

API docs (try every endpoint in the browser): http://localhost:8000/docs
Teammates on the same Wi-Fi use `http://<lead-laptop-IP>:8000` (find it with `ipconfig`).

```bash
cd ml && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/python train_eta.py
cd vision && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/python count.py --bus 14 --source 0 --show --interval 5
```

### Website (passenger + driver/conductor)

```bash
cd frontend/web && npm install && npm run dev
```

Open http://localhost:5173. Phones on the same Wi-Fi open `http://<laptop-IP>:5173`. The dev server forwards
`/api` and `/ws` to the backend, so the phone needs only this one address. If the backend runs on another laptop,
set `SAFAR_API=http://<backend-IP>:8000` before `npm run dev`. Allow Node through Windows Firewall when asked.

| Login tab | Who | Page |
|---|---|---|
| Passenger | Students; they create their own account (student ID, name, password, route, stop, optional exam shift) | Your bus, departure, seats free, alerts, exam-day trip, live map, change stop |
| Driver / Conductor | Staff accounts issued by the transport office | **Driver:** start boarding, start trip, share GPS (or "Simulate route" for the demo). **Conductor:** open boarding, mark full, manual head count, booked passenger list |

Demo staff accounts (IDs and passwords) are in `backend/seed.json` under `demo_staff`. They are for the demo only.

Phone GPS needs HTTPS or localhost. Over `http://<IP>:5173` the browser blocks it, so use **Simulate route** in the demo.

## API contract

Base URL: `http://<host>:8000`. All JSON. 🔒 = needs `Authorization: Bearer <token>` from `/auth/login` or `/auth/register`.

| Method | Path | Who calls it | Body / result |
|---|---|---|---|
| POST | `/auth/register` | passenger | `{id, name, password(≥6), route_id, stop, exam_slot?}` → `{token, user, bus, alert}` |
| POST | `/auth/login` | everyone | `{kind: passenger\|staff, id, password}` → `{token, user}` (staff `user` has `role` driver/conductor and `bus_id`) |
| POST | `/auth/logout` | everyone | ends the session |
| GET | `/me` 🔒 | everyone | the logged-in user (passenger `profile` or staff `bus`) |
| GET | `/me/bus` 🔒 | passenger | `{bus, alert}`: re-allocates if their bus left, warns if it is full |
| PUT | `/me/stop` 🔒 | passenger | `{route_id, stop, exam_slot?}` → `{bus, alert}` |
| GET | `/routes` | everyone | list of routes with ordered stops |
| GET | `/buses` | everyone | all buses (`BusView`) |
| GET | `/buses/{id}` | everyone | one `BusView` |
| POST | `/buses/{id}/count` | vision, conductor | `{count, seats_free?, seats_total?}`: seat fields optional, sent together |
| POST | `/buses/{id}/location` 🔒 | driver/conductor **of that bus** | `{lat, lng}` |
| POST | `/buses/{id}/status` 🔒 | driver/conductor **of that bus** | `{status: scheduled\|boarding\|departed\|full}` |
| GET | `/buses/{id}/passengers` 🔒 | driver/conductor **of that bus** | students booked on the bus, in stop order |
| GET | `/planner?slot=11:00` | dashboard | students per route for an exam slot + extra-trip suggestions (≥30 students) |
| GET | `/dashboard/summary` | dashboard | buses, totals, half-empty and full bus ids |
| WS | `/ws` | student app, dashboard | pushes `{type:"buses",data:[...]}`, `{type:"bus",data:BusView}`, `{type:"alert",bus_id,message}` |

`BusView`:

```json
{"id": 14, "route_id": 3, "gate": "Gate 2", "stops": ["D","E","F","G"], "capacity": 40,
 "occupancy": 28, "seats_free": 12, "seat_source": "camera", "seats_seen": 40,
 "status": "boarding", "location": {"lat": 30.27, "lng": 78.0},
 "eta": {"scheduled": "13:15", "predicted": "13:18", "delay_min": 3.2, "source": "model|baseline|timetable"},
 "updated_at": "2026-10-31T13:02:11"}
```

Occupancy = people counted by the camera if the bus has one, otherwise students allocated to it.

`seats_free` comes from the best source available, named in `seat_source`:

| `seat_source` | Meaning |
|---|---|
| `camera` | Seat map covers the whole bus; empty seats detected directly |
| `camera_partial` | Seat map covers `seats_seen` of `capacity` seats; unseen seats assumed to fill at the same rate (an estimate — show "~" in the UI) |
| `people_count` | No seat map; `capacity − people counted` |
| `allocations` | No camera; `capacity − students allocated` |

## Demo script (what judges should see)

Use two phones (or two browsers): one logged in as a student, one as driver D14.

1. Student registers: Route 3, stop E → the page shows **Bus 14, Gate 2, departure time, seats free**.
2. Run `count.py` with people in front of the webcam → seats free drops live on the student's phone.
3. Bus 14 hits full → the student's phone shows **"Bus 14 is full right now, take Bus 15"**.
4. Driver taps **Start trip** + **Simulate route** → the bus moves on the student's map, and the student is moved to the next bus.
5. Dashboard: 11 AM shift → **Route 3 has 140 students, add a 10:00 trip with 4 buses**.

## Honesty notes for the pitch

- ETA model is trained on **synthetic** data until real trip logs exist (`ml/artifacts/meta.json` says which). Say so if asked.
- Seat-count accuracy (target ≥90% vs. manual count): measure it on your own clip, count 20 frames by hand, and report the real number.
- State is in memory: a restart clears student accounts and logs everyone out. PostgreSQL + Redis are the production plan, not needed for the demo.
- Passwords are salted and hashed (PBKDF2). There's no rate limiting on login and `/count` has no device key yet. Both are needed before a real pilot.
- Map positions (campus, route line) are approximate sample coordinates.
