import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import fusion
from conftest import execute, fetch, token
from test_m2_ingest import BUS, TOKEN, payload, window_start

NOW = 1000.0
T0 = datetime(2026, 10, 31, 8, 0, tzinfo=timezone.utc)


def bus(ir_net=None, ir_age=1, cv_free=None, cv_total=40, cv_count=None, cv_age=1, still_s=0, offset=0):
    b = {"capacity": 40, "ir_net": ir_net, "ir_offset": offset,
         "ir_seen": NOW - ir_age if ir_net is not None else None,
         "seat_cam": {"free": cv_free, "total": cv_total} if cv_free is not None else None,
         "cv_count": cv_count,
         "cv_seen": NOW - cv_age if (cv_free is not None or cv_count is not None) else None,
         "last_device_ts": T0 + timedelta(seconds=still_s),
         "stationary_since": T0 if still_s else None}
    return b


# ---------- pure fusion rules (synthetic sequences) ----------

def test_stationary_trusts_camera_over_occluded_ir():
    f = fusion.compute(bus(ir_net=30, cv_free=14, still_s=6), NOW)  # IR says 10 empty, camera 14
    assert (f.mode, f.seats_free, f.deviation) == ("cv", 14, 4)


def test_moving_trusts_ir_over_camera():
    f = fusion.compute(bus(ir_net=33, cv_free=12, still_s=0), NOW)  # door double-count, but camera occluded
    assert (f.mode, f.seats_free) == ("ir", 7)


def test_just_stopped_is_not_yet_stationary():
    assert fusion.compute(bus(ir_net=30, cv_free=14, still_s=3), NOW).mode == "ir"


def test_stale_camera_falls_back_to_ir():
    f = fusion.compute(bus(ir_net=25, cv_free=30, cv_age=60, still_s=30), NOW)
    assert (f.mode, f.seats_free, f.visual_empty) == ("ir_only", 15, None)


def test_stale_ir_leaves_it_to_camera_logic():
    f = fusion.compute(bus(ir_net=25, ir_age=20, cv_free=30, still_s=30), NOW)
    assert (f.mode, f.seats_free) == ("none", None)


def test_partial_seat_map_and_people_count_camera():
    assert fusion.camera_empty(bus(cv_free=5, cv_total=10)) == 20
    assert fusion.camera_empty(bus(cv_count=31)) == 9
    assert fusion.camera_empty(bus(cv_count=55)) == 0


def test_recalibrates_after_long_disagreement_at_rest():
    b = bus(ir_net=30, cv_free=16, still_s=12)        # IR 10 empty vs camera 16: off by 6
    assert fusion.recalibrate(b, NOW) == -6            # IR now reads 24 people = camera's 24
    b["stationary_since"], b["last_device_ts"] = None, T0  # bus moves off; two more board through the door
    b["ir_net"] = 32
    assert fusion.compute(b, NOW).seats_free == 14     # 40 - (32 - 6)


@pytest.mark.parametrize("kw", [
    dict(ir_net=30, cv_free=16, still_s=8),    # not at rest long enough
    dict(ir_net=30, cv_free=14, still_s=30),   # within threshold (4)
    dict(ir_net=30, cv_free=16, cv_age=60, still_s=30),  # camera stale
])
def test_no_recalibration(kw):
    b = bus(**kw)
    assert fusion.recalibrate(b, NOW) is None and b["ir_offset"] == 0


def test_negative_corrected_count_is_clamped():
    assert fusion.ir_people(bus(ir_net=2, offset=-5)) == 0


# ---------- through the backend ----------

@pytest.fixture
def bus11():
    old = fetch("select device_token_hash from buses where id = $1", BUS)[0]["device_token_hash"]
    execute("update buses set device_token_hash = $2 where id = $1", BUS, hashlib.sha256(TOKEN.encode()).hexdigest())
    execute("update bus_live_state set last_device_ts = null, ir_offset = 0, cam_seats_free = null, "
            "cam_seats_total = null where bus_id = $1", BUS)
    yield {"X-Device-Token": TOKEN}
    execute("update buses set device_token_hash = $2 where id = $1", BUS, old)


def test_fusion_end_to_end(client_factory, bus11):
    t0 = window_start()
    driver = {"Authorization": "Bearer " + token(str(uuid.uuid4()), {"role": "driver", "bus_id": BUS, "name": "D11"})}
    with client_factory() as c:
        c.post(f"/buses/{BUS}/count", json={"count": 24, "seats_free": 16, "seats_total": 40})
        for i in range(7):  # stationary 0..12 s, IR thinks 30 on board
            c.post("/ingest/telemetry", json=payload(ts=t0 + 2 * i, speed=0, net=30), headers=bus11)
        at_rest = c.get(f"/buses/{BUS}").json()
        c.post("/ingest/telemetry", json=payload(ts=t0 + 16, speed=24, net=32), headers=bus11)
        moving = c.get(f"/buses/{BUS}").json()
        c.post(f"/buses/{BUS}/status", json={"status": "departed"}, headers=driver)
        departed = c.get(f"/buses/{BUS}").json()
        c.post(f"/buses/{BUS}/status", json={"status": "scheduled"}, headers=driver)
        trip_over = c.get(f"/buses/{BUS}").json()

    assert at_rest["seat_source"] == "fused" and at_rest["fusion"]["mode"] == "cv"
    assert at_rest["seats_free"] == 16 and at_rest["fusion"]["ir_offset"] == -6
    assert moving["fusion"]["mode"] == "ir" and moving["seats_free"] == 14 and moving["occupancy"] == 26
    assert departed["fusion"]["ir_offset"] == -6   # calibration made while boarding survives departure
    assert trip_over["fusion"]["ir_offset"] == 0   # and is cleared when the trip ends

    event = fetch("select detail from trip_events where bus_id = $1 and kind = 'fusion_recalibrated' "
                  "order by id desc limit 1", BUS)[0]["detail"]
    assert event == "IR said 10 empty, camera 16; IR offset 0 -> -6"
    saved = fetch("select fused_seats_free from telemetry where bus_id = $1 and ts = to_timestamp($2)",
                  BUS, t0 + 16)[0]["fused_seats_free"]
    assert saved == 14
