# SafarSaathi — AI Smart Campus Bus System

SAARTHI'26 · Graphic Era Hill University, Dehradun · Team: Siddhant (lead), Vidarbh, Vansh, Rahul

## Who does what

Everyone builds against the **API contract below**, so nobody waits on anybody else.

| Person | Owns | Folder | First milestone |
|---|---|---|---|
| **P1 — Siddhant (lead)** | Backend, integration, final demo flow | `backend/` | API running on the LAN, everyone pointing at it |
| **P2** | Student PWA: set stop, "Your bus today", live map, alerts | `frontend/student` | Register → see allocated bus + seats free |
| **P3** | Vision: YOLOv8n seat counting on bus video/webcam | `vision/` | `count.py` posting live counts to the backend |
| **P4** | Driver app + office dashboard + ETA model | `frontend/driver`, `frontend/dashboard`, `ml/` | Driver "Start trip" + GPS; dashboard chart from `/planner` |

Lead rule: **integrate every 3–4 hours**. A working end-to-end demo with fewer features is better than 5 half-done pieces.

## Run

Use Python **3.12 or 3.13** (`py -3.13`). PyTorch/YOLO support for 3.14 is not guaranteed.

```bash
cd backend && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

API docs (try every endpoint in the browser): http://localhost:8000/docs
Teammates on the same Wi-Fi use `http://<lead-laptop-IP>:8000` (find it with `ipconfig`).

```bash
cd ml && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/python train_eta.py
cd vision && py -3.13 -m venv .venv && .venv/Scripts/pip install -r requirements.txt && .venv/Scripts/python count.py --bus 14 --source 0 --show --interval 5
```

Frontend (each app is its own Vite project):

```bash
cd frontend && npm create vite@latest student -- --template react && cd student && npm i && npm i leaflet react-leaflet && npm run dev
```

## API contract

Base URL: `http://<host>:8000`. All JSON.

| Method | Path | Who calls it | Body / result |
|---|---|---|---|
| GET | `/routes` | student app | list of routes with ordered stops |
| POST | `/students` | student app | `{id, name, route_id, stop, exam_slot?}` → `{student, bus, alert}` |
| GET | `/students/{id}/bus` | student app | `{bus, alert}` — re-allocates if their bus left |
| GET | `/buses` | everyone | all buses (`BusView`) |
| GET | `/buses/{id}` | everyone | one `BusView` |
| POST | `/buses/{id}/count` | vision | `{count, seats_free?, seats_total?}` — seat fields optional, sent together |
| POST | `/buses/{id}/location` | driver app | `{lat, lng}` (send every ~10 s) |
| POST | `/buses/{id}/status` | driver app | `{status: scheduled\|boarding\|departed\|full}` |
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

1. Student registers: Route 3, stop E → app shows **Bus 14, Gate 2, leaves ~13:18, 12/40 free**.
2. Run `count.py` with people in front of the webcam → seats free drops live on the student app.
3. Bus 14 hits full → student gets **"Bus 14 full, take Bus 15"**.
4. Driver taps **Start trip** → map shows the bus moving.
5. Dashboard: 11 AM shift → **Route 3 has 140 students, add a 10:00 trip with 4 buses**.

## Honesty notes for the pitch

- ETA model is trained on **synthetic** data until real trip logs exist (`ml/artifacts/meta.json` says which). Say so if asked.
- Seat-count accuracy (target ≥90% vs. manual count): measure it on your own clip, count 20 frames by hand, and report the real number.
- State is in memory (resets on restart). PostgreSQL + Redis are the production plan, not needed for the demo.
