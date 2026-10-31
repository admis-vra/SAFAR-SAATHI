import hashlib
import random
import time
import uuid

import pytest

from conftest import execute, fetch, token
from geofence import BusFence, Zone, distance_m
from test_m2_ingest import BUS, DEVICE, TOKEN, payload, window_start

DEPOT = (30.2687, 77.9945)


def offset(lat, lng, north_m=0.0, east_m=0.0):
    return lat + north_m / 111_320, lng + east_m / (111_320 * 0.8645)


# ---------- pure geofence logic ----------

def test_jitter_at_fence_edge_does_not_flap():
    z = Zone("stop:1", "stop", "X", *DEPOT, 60)
    fence = BusFence([z])
    events = []
    for d in [55, 55] + [58, 66, 57, 70, 59, 68] * 3:  # wobbling across 60 m but never beyond 72 m
        events += fence.update(*offset(*DEPOT, north_m=d))
    assert [e for e, _ in events] == ["enter"]


def test_single_outlier_fix_is_ignored_and_two_confirm():
    z = Zone("depot", "depot", "campus", *DEPOT, 150)
    fence = BusFence([z])
    fence.seed(*DEPOT)
    assert fence.update(*offset(*DEPOT, north_m=400)) == []   # one bad fix
    assert fence.update(*DEPOT) == []                          # back inside: streak reset
    assert fence.update(*offset(*DEPOT, north_m=400)) == []
    assert [e for e, _ in fence.update(*offset(*DEPOT, north_m=420))] == ["exit"]


def test_seed_sets_state_without_events():
    fence = BusFence([Zone("depot", "depot", "campus", *DEPOT, 150)])
    fence.seed(*DEPOT)
    assert fence.inside == {"depot"} and fence.update(*DEPOT) == []


def test_distance_sanity():
    assert distance_m(*DEPOT, *offset(*DEPOT, north_m=1000)) == pytest.approx(1000, rel=0.01)


# ---------- through the backend ----------

@pytest.fixture
def bus11(monkeypatch):
    """Bus 11 (route 4, only bus on it) with a known blank state, a device token and a broadcast recorder."""
    old = fetch("select device_token_hash from buses where id = $1", BUS)[0]["device_token_hash"]
    execute("update buses set device_token_hash = $2 where id = $1", BUS, hashlib.sha256(TOKEN.encode()).hexdigest())
    execute("update bus_live_state set location = null, last_device_ts = null, status = 'scheduled' "
            "where bus_id = $1", BUS)
    execute("update active_trips set end_time = now() where bus_id = $1 and end_time is null", BUS)
    execute("update incidents set acknowledged_at = now(), closed_at = now() where bus_id = $1 "
            "and acknowledged_at is null", BUS)
    import main
    sent = []
    real = main.hub.broadcast

    async def record(msg):
        sent.append(msg)
        await real(msg)

    monkeypatch.setattr(main.hub, "broadcast", record)
    yield {"headers": {"X-Device-Token": TOKEN}, "sent": sent}
    execute("update buses set device_token_hash = $2 where id = $1", BUS, old)


def route4_points(c):
    route = next(r for r in c.get("/routes").json() if r["id"] == 4)
    pts = {p["name"]: (p["lat"], p["lng"]) for p in route["stop_points"]}
    return pts["G"], pts["A"]


def trip_path(stop_g, stop_a):
    """depot x2, 400 m out x2, at G x3, 300 m past G x2, at A x2."""
    return ([DEPOT] * 2 + [offset(*DEPOT, north_m=400)] * 2 + [stop_g] * 3
            + [offset(*stop_g, north_m=300)] * 2 + [stop_a] * 2)


def recent_start():
    return int(time.time()) - 60 + random.randint(0, 20)


def test_live_trip_starts_and_ends_by_geofence(client_factory, bus11):
    with client_factory() as c:
        stop_g, stop_a = route4_points(c)
        t0 = recent_start()
        statuses = []
        for i, (lat, lng) in enumerate(trip_path(stop_g, stop_a)):
            c.post("/ingest/telemetry", json=payload(ts=t0 + 2 * i, lat=lat, lon=lng), headers=bus11["headers"])
            statuses.append(c.get(f"/buses/{BUS}").json()["status"])
    assert "departed" in statuses and statuses[-1] == "scheduled"
    trip = fetch("select id, end_time, is_last_bus from active_trips where bus_id = $1 order by id desc limit 1",
                 BUS)[0]
    assert trip["end_time"] is not None and trip["is_last_bus"] is True  # only bus on route 4
    events = fetch("""select e.kind::text as kind, s.stop_name, e.late from trip_events e
                      left join stops s on s.id = e.stop_id where e.trip_id = $1 order by e.ts, e.id""", trip["id"])
    assert [(e["kind"], e["stop_name"]) for e in events] == [
        ("depot_exit", None), ("stop_enter", "G"), ("stop_exit", "G"), ("stop_enter", "A")]
    assert not any(e["late"] for e in events)
    pushed = [m for m in bus11["sent"] if m["type"] == "trip_event"]
    assert [m["kind"] for m in pushed][-4:] == ["depot_exit", "stop_enter", "stop_exit", "stop_enter"]


def test_backlog_trip_is_history_without_alerts(client_factory, bus11):
    with client_factory() as c:
        stop_g, stop_a = route4_points(c)
        t0 = window_start()
        backlog = [payload(ts=t0 + 2 * i, lat=lat, lon=lng) for i, (lat, lng) in enumerate(trip_path(stop_g, stop_a))]
        r = c.post("/ingest/backlog", json=backlog, headers=bus11["headers"]).json()
        assert r["accepted"] == len(backlog)
        assert c.get(f"/buses/{BUS}").json()["status"] == "scheduled"
    trip = fetch("select id, end_time from active_trips where bus_id = $1 order by id desc limit 1", BUS)[0]
    events = fetch("select kind::text as kind, late from trip_events where trip_id = $1", trip["id"])
    assert len(events) == 4 and all(e["late"] for e in events) and trip["end_time"] is not None
    assert not [m for m in bus11["sent"] if m["type"] in ("trip_event", "alert")]


def test_manual_override_is_logged(client_factory, bus11):
    driver = {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "driver", "bus_id": BUS, "name": "D11"})}
    with client_factory() as c:
        assert c.post(f"/buses/{BUS}/status", json={"status": "departed"}, headers=driver).status_code == 200
    row = fetch("select kind::text as kind, detail, trip_id from trip_events where bus_id = $1 "
                "order by id desc limit 1", BUS)[0]
    assert row["kind"] == "manual_status" and row["detail"] == "departed by D11 (driver)" and row["trip_id"]


# ---------- SOS ----------

def test_device_sos_needs_dispatcher_ack(client_factory, bus11):
    dispatcher = {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "dispatcher", "name": "Control"})}
    driver = {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "driver", "bus_id": BUS, "name": "D11"})}
    t0 = recent_start()
    with client_factory() as c:
        c.post("/ingest/telemetry", json=payload(ts=t0, sos=True), headers=bus11["headers"])
        c.post("/ingest/telemetry", json=payload(ts=t0 + 2, sos=False), headers=bus11["headers"])
        assert c.get(f"/buses/{BUS}").json()["sos"] is True  # button released, still open
        open_list = c.get("/incidents", headers=dispatcher).json()
        incident = next(i for i in open_list if i["bus_id"] == BUS and i["kind"] == "sos")
        assert incident["lat"] == pytest.approx(30.30) and incident["late"] is False

        assert c.get("/incidents", headers=driver).status_code == 403
        assert c.post(f"/incidents/{incident['id']}/ack", headers=driver).status_code == 403
        assert c.post(f"/incidents/{incident['id']}/ack", headers=dispatcher).status_code == 200
        assert c.post(f"/incidents/{incident['id']}/ack", headers=dispatcher).status_code == 404
        assert c.get(f"/buses/{BUS}").json()["sos"] is False
    pushed = [m for m in bus11["sent"] if m["type"] == "incident" and m["kind"] == "sos"]
    assert [m["open"] for m in pushed] == [True, False]


def test_driver_sos_button_and_permissions(client_factory, bus11):
    own = {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "driver", "bus_id": BUS, "name": "D11"})}
    other = {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "driver", "bus_id": 14, "name": "D14"})}
    with client_factory() as c:
        assert c.post(f"/buses/{BUS}/sos", headers=other).status_code == 403
        first = c.post(f"/buses/{BUS}/sos", headers=own).json()["incident_id"]
        again = c.post(f"/buses/{BUS}/sos", headers=own).json()["incident_id"]
    assert first == again  # one open SOS per bus, not one per press
    assert fetch("select detail from incidents where id = $1", first)[0]["detail"] == "SOS pressed by D11 (driver)"


def test_late_sos_still_raised_and_marked(client_factory, bus11):
    with client_factory() as c:
        c.post("/ingest/backlog", json=[payload(ts=window_start(), sos=True)], headers=bus11["headers"])
        assert c.get(f"/buses/{BUS}").json()["sos"] is True
    pushed = [m for m in bus11["sent"] if m["type"] == "incident" and m["kind"] == "sos"]
    assert pushed and pushed[0]["late"] is True and "reported late" in pushed[0]["detail"]
