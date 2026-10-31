import asyncio
import hashlib
import json
import random
import socket
import threading
import time
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

import ingest
from conftest import execute, fetch

BUS, DEVICE = 11, "B-11"
TOKEN = "test-device-token-for-bus-11"


def payload(ts=None, lat=30.30, lon=78.02, speed=25.0, sats=8, net=20, bus_id=DEVICE, sos=False):
    return {"bus_id": bus_id, "timestamp": int(ts or time.time()),
            "gps": {"lat": lat, "lon": lon, "speed": speed, "sats": sats},
            "pax": {"in_count": max(net, 0) + 5, "out_count": 5, "net_current": net}, "sos_active": sos}


def window_start() -> int:
    """A random second 1-20 h ago: telemetry dedupes on (bus, second), so tests must not reuse timestamps."""
    return int(time.time()) - random.randint(3600, 72000)


@pytest.fixture(autouse=True)
def fresh_device_clock():
    # Live updates need a reading newer than the last one; the conftest fixture restores this afterwards.
    execute("update bus_live_state set last_device_ts = null where bus_id = $1", BUS)


@pytest.fixture
def device_token():
    old = fetch("select device_token_hash from buses where id = $1", BUS)[0]["device_token_hash"]
    execute("update buses set device_token_hash = $2 where id = $1", BUS, hashlib.sha256(TOKEN.encode()).hexdigest())
    yield {"X-Device-Token": TOKEN}
    execute("update buses set device_token_hash = $2 where id = $1", BUS, old)


# ---------- payload validation (blueprint §3.2) ----------

def test_valid_payload_parses():
    [t] = ingest.parse("telemetry", json.dumps(payload()).encode())
    assert t.bus_id == DEVICE and t.has_fix and t.pax.net_current == 20


@pytest.mark.parametrize("bad", [
    payload(lat=95), payload(lon=-200), payload(speed=-1), payload(sats=99),
    payload(ts=time.time() + 3600), payload(ts=time.time() - 2 * 86400),
    {**payload(), "gps": None}, {"bus_id": DEVICE},
])
def test_invalid_payloads_rejected(bad):
    with pytest.raises((ValidationError, ValueError)):
        ingest.parse("telemetry", json.dumps(bad).encode())


def test_no_fix_detection():
    assert not ingest.Telemetry.model_validate(payload(sats=2)).has_fix
    assert not ingest.Telemetry.model_validate(payload(lat=0, lon=0)).has_fix


def test_topic_parsing():
    assert ingest.split_topic("safarsaathi/bus/B-14/backlog") == ("B-14", "backlog")
    for bad in ("safarsaathi/bus/B-14", "other/bus/B-14/telemetry", "safarsaathi/bus/a/b/telemetry"):
        with pytest.raises(ValueError):
            ingest.split_topic(bad)


# ---------- HTTP ingest ----------

def test_device_token_required(client_factory, device_token):
    with client_factory() as c:
        assert c.post("/ingest/telemetry", json=payload()).status_code == 401
        assert c.post("/ingest/telemetry", json=payload(), headers={"X-Device-Token": "nope"}).status_code == 401
        assert c.post("/ingest/telemetry", json=payload(bus_id="B-14"), headers=device_token).status_code == 403


def test_live_reading_updates_bus_and_history(client_factory, device_token):
    ts = window_start()
    with client_factory() as c:
        r = c.post("/ingest/telemetry", json=payload(ts=ts, lat=30.3001, lon=78.0201, speed=31.5, net=27),
                   headers=device_token).json()
        assert r == {"accepted": 1, "duplicates": 0, "live_updated": True}
        bus = c.get(f"/buses/{BUS}").json()
    assert bus["location"] == pytest.approx({"lat": 30.3001, "lng": 78.0201})
    assert bus["speed_kmh"] == pytest.approx(31.5) and bus["occupancy"] == 27
    assert bus["device"]["online"] and bus["device"]["ir_net"] == 27
    row = fetch("select ir_net, ir_in, ir_out, sats, from_backlog from telemetry where bus_id = $1 "
                "and ts = to_timestamp($2)", BUS, ts)[0]
    assert dict(row) == {"ir_net": 27, "ir_in": 32, "ir_out": 5, "sats": 8, "from_backlog": False}


def test_duplicate_and_out_of_order(client_factory, device_token):
    now = window_start()
    with client_factory() as c:
        c.post("/ingest/telemetry", json=payload(ts=now - 2, lat=30.31), headers=device_token)
        dup = c.post("/ingest/telemetry", json=payload(ts=now - 2, lat=30.31), headers=device_token).json()
        late = c.post("/ingest/telemetry", json=payload(ts=now - 60, lat=30.25), headers=device_token).json()
        bus = c.get(f"/buses/{BUS}").json()
    assert dup == {"accepted": 0, "duplicates": 1, "live_updated": False}
    assert late == {"accepted": 1, "duplicates": 0, "live_updated": False}  # kept as history only
    assert bus["location"]["lat"] == pytest.approx(30.31)


def test_backlog_is_history_and_newest_wins(client_factory, device_token):
    now = window_start()
    backlog = [payload(ts=now - 30 + i * 2, lat=30.32 + i * 0.001, net=10 + i) for i in range(5)]
    with client_factory() as c:
        r = c.post("/ingest/backlog", json=backlog, headers=device_token).json()
        bus = c.get(f"/buses/{BUS}").json()
    assert r == {"accepted": 5, "duplicates": 0, "live_updated": True}
    assert bus["location"]["lat"] == pytest.approx(30.324) and bus["occupancy"] == 14
    flags = fetch("select count(*) filter (where from_backlog) as b, count(*) as n from telemetry where bus_id = $1 "
                  "and ts = any($2::timestamptz[])", BUS,
                  [datetime.fromtimestamp(p["timestamp"], timezone.utc) for p in backlog])[0]
    assert dict(flags) == {"b": 5, "n": 5}


def test_no_fix_keeps_location_and_negative_net_is_zero(client_factory, device_token):
    now = window_start()
    with client_factory() as c:
        c.post("/ingest/telemetry", json=payload(ts=now - 4, lat=30.33), headers=device_token)
        c.post("/ingest/telemetry", json=payload(ts=now - 2, lat=0, lon=0, sats=1, speed=12, net=-3),
               headers=device_token)
        bus = c.get(f"/buses/{BUS}").json()
    assert bus["location"]["lat"] == pytest.approx(30.33)
    assert bus["speed_kmh"] == pytest.approx(12) and bus["occupancy"] == 0 and bus["device"]["ir_net"] == -3


def test_offline_incident_opens_and_closes(client_factory, device_token):
    import main
    t0 = window_start()
    with client_factory() as c:
        c.post("/ingest/telemetry", json=payload(ts=t0), headers=device_token)
        c.portal.call(main.check_offline, time.monotonic() + main.OFFLINE_AFTER_S + 1)
        assert c.get(f"/buses/{BUS}").json()["device"]["online"] is False
        assert fetch("select count(*) as n from incidents where bus_id = $1 and kind = 'device_offline' "
                     "and closed_at is null", BUS)[0]["n"] == 1
        c.post("/ingest/telemetry", json=payload(ts=t0 + 1), headers=device_token)
        assert c.get(f"/buses/{BUS}").json()["device"]["online"] is True
    assert fetch("select count(*) as n from incidents where bus_id = $1 and kind = 'device_offline' "
                 "and closed_at is null", BUS)[0]["n"] == 0


# ---------- MQTT end to end ----------

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mqtt_broker(monkeypatch):
    from amqtt.broker import Broker
    port = _free_port()
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def start():  # amqtt looks up the running loop, so build it inside the broker's thread
        b = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
                    "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
        await b.start()
        return b

    broker = asyncio.run_coroutine_threadsafe(start(), loop).result(10)
    monkeypatch.setenv("MQTT_URL", f"mqtt://127.0.0.1:{port}")
    yield port
    asyncio.run_coroutine_threadsafe(broker.shutdown(), loop).result(10)
    loop.call_soon_threadsafe(loop.stop)


def test_mqtt_telemetry_and_backlog_reach_the_bus(client_factory, mqtt_broker):
    import paho.mqtt.client as mqtt
    pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="test-device")
    pub.connect("127.0.0.1", mqtt_broker)
    pub.loop_start()
    now = window_start()
    try:
        with client_factory() as c:
            time.sleep(1.5)  # let the backend subscribe
            pub.publish(f"safarsaathi/bus/{DEVICE}/telemetry",
                        json.dumps(payload(ts=now - 1, lat=30.3412, net=19)), qos=1).wait_for_publish()
            pub.publish(f"safarsaathi/bus/{DEVICE}/telemetry", b"not json", qos=1).wait_for_publish()
            pub.publish("safarsaathi/bus/B-14/telemetry",  # payload claims another bus: dropped
                        json.dumps(payload(ts=now, lat=10.0)), qos=1).wait_for_publish()
            bus = None
            for _ in range(40):
                bus = c.get(f"/buses/{BUS}").json()
                if bus["location"] and abs(bus["location"]["lat"] - 30.3412) < 1e-6:
                    break
                time.sleep(0.5)
            assert bus["location"]["lat"] == pytest.approx(30.3412) and bus["occupancy"] == 19

            pub.publish(f"safarsaathi/bus/{DEVICE}/backlog",
                        json.dumps([payload(ts=now + 1, lat=30.3420, net=21)]), qos=1).wait_for_publish()
            for _ in range(40):
                bus = c.get(f"/buses/{BUS}").json()
                if abs(bus["location"]["lat"] - 30.3420) < 1e-6:
                    break
                time.sleep(0.5)
            assert bus["location"]["lat"] == pytest.approx(30.3420) and bus["occupancy"] == 21
            assert c.get("/buses/14").json()["location"] is None or \
                c.get("/buses/14").json()["location"]["lat"] != pytest.approx(10.0)
    finally:
        pub.loop_stop()
        pub.disconnect()
