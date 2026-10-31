"""Simulated ESP32 bus: publishes the blueprint telemetry payload over MQTT every few seconds.

It drives from campus along the bus's route (stops G -> A), dwells at each stop, and counts passengers
boarding/alighting with occasional IR double counts. Use it to demo without hardware.

Usage (from repo root):
  backend/.venv/Scripts/python tools/simulate_bus.py --bus 14
  backend/.venv/Scripts/python tools/simulate_bus.py --bus 14 --speedup 5 --drop-network 20 --sos-at D
"""
import argparse
import json
import math
import random
import sys
import time
import urllib.request
from urllib.parse import urlparse

import paho.mqtt.client as mqtt

CAMPUS = (30.2687, 77.9945)  # sample position, same as the frontend map


def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def lerp(a, b, f):
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r)


class Bus:
    def __init__(self, device_id, capacity, load, rng):
        self.device_id, self.capacity, self.rng = device_id, capacity, rng
        self.target_load = min(load, capacity)
        self.ir_in = self.ir_out = 0
        self.sos = False

    @property
    def net(self):
        return self.ir_in - self.ir_out

    def board(self, n):
        for _ in range(n):
            self.ir_in += 2 if self.rng.random() < 0.04 else 1  # IR double count when people overlap

    def alight(self, n):
        self.ir_out += max(0, min(n, self.net))

    def payload(self, pos, speed):
        jitter = lambda: self.rng.uniform(-0.00004, 0.00004)  # ~4 m GPS noise
        return {
            "bus_id": self.device_id,
            "timestamp": int(time.time()),
            "gps": {"lat": round(pos[0] + jitter(), 6), "lon": round(pos[1] + jitter(), 6),
                    "speed": round(speed, 1), "sats": self.rng.randint(6, 11)},
            "pax": {"in_count": self.ir_in, "out_count": self.ir_out, "net_current": self.net},
            "sos_active": self.sos,
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bus", type=int, required=True)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--broker", default="mqtt://127.0.0.1:1883")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between messages (blueprint: 2)")
    ap.add_argument("--speed-kmh", type=float, default=28)
    ap.add_argument("--speedup", type=float, default=1.0, help="move this many times faster than real time")
    ap.add_argument("--dwell", type=float, default=20, help="seconds stopped at each stop (before speedup)")
    ap.add_argument("--load", type=int, default=32, help="passengers boarding at campus")
    ap.add_argument("--drop-network", type=float, default=0, metavar="SECONDS",
                    help="after the first stop, go offline this long, then publish a backlog")
    ap.add_argument("--sos-at", metavar="STOP", help="raise SOS while dwelling at this stop (e.g. D)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--loop", action="store_true", help="repeat the trip forever")
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    bus_info = fetch_json(f"{args.api}/buses/{args.bus}")
    route = next(r for r in fetch_json(f"{args.api}/routes") if r["id"] == bus_info["route_id"])
    device_id = bus_info["device"]["id"]
    stops = [s for s in reversed(route["stop_points"])]  # G (near campus) -> A
    waypoints = [("campus", CAMPUS)] + [(s["name"], (s["lat"], s["lng"])) for s in stops]

    u = urlparse(args.broker)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{device_id}")
    if u.username:
        client.username_pw_set(u.username, u.password)
    client.connect(u.hostname, u.port or 1883, keepalive=30)
    client.loop_start()
    topic = f"safarsaathi/bus/{device_id}"
    rng = random.Random(args.seed)

    offline_until, buffer, dropped_once = 0.0, [], False

    def send(bus, pos, speed):
        nonlocal offline_until, buffer
        msg = bus.payload(pos, speed)
        now = time.time()
        if now < offline_until:
            buffer.append(msg)
            print(f"  [offline] buffered {len(buffer)}")
        else:
            if buffer:
                client.publish(f"{topic}/backlog", json.dumps(buffer), qos=1).wait_for_publish()
                print(f"  [reconnected] published backlog of {len(buffer)}")
                buffer = []
            client.publish(f"{topic}/telemetry", json.dumps(msg), qos=1)
        time.sleep(args.interval)

    def dwell(bus, name, pos):
        ticks = max(1, round(args.dwell / args.speedup / args.interval))
        for t in range(ticks):
            bus.sos = args.sos_at == name and t < ticks - 1
            send(bus, pos, 0.0)
        bus.sos = False

    try:
        while True:
            bus = Bus(device_id, bus_info["capacity"], args.load, rng)
            print(f"{device_id}: boarding at campus ({bus.target_load} passengers)")
            ticks = max(2, round(args.dwell * 2 / args.speedup / args.interval))
            for t in range(ticks):
                bus.board(round(bus.target_load * (t + 1) / ticks) - round(bus.target_load * t / ticks))
                send(bus, CAMPUS, 0.0)

            for i in range(1, len(waypoints)):
                (_, a), (name, b) = waypoints[i - 1], waypoints[i]
                dist, step = haversine_m(a, b), args.speed_kmh / 3.6 * args.interval * args.speedup
                n = max(1, math.ceil(dist / step))
                print(f"{device_id}: -> stop {name} ({dist:.0f} m), on board {bus.net}")
                for k in range(1, n + 1):
                    send(bus, lerp(a, b, k / n), args.speed_kmh * rng.uniform(0.8, 1.1))
                last = i == len(waypoints) - 1
                bus.alight(bus.net if last else rng.randint(0, max(1, bus.net // 3)))
                dwell(bus, name, b)
                if args.drop_network and not dropped_once:
                    offline_until, dropped_once = time.time() + args.drop_network, True
                    print(f"  [network drop for {args.drop_network:.0f}s]")
            if buffer:
                client.publish(f"{topic}/backlog", json.dumps(buffer), qos=1).wait_for_publish()
                print(f"  published remaining backlog of {len(buffer)}")
                buffer, offline_until = [], 0.0
            print(f"{device_id}: trip done (IR in {bus.ir_in}, out {bus.ir_out})")
            if not args.loop:
                break
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
