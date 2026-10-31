"""Stand-in for vision/count.py when no webcam is available: posts a seat-camera reading every few seconds.

Usage (from repo root):
  backend/.venv/Scripts/python tools/fake_camera.py --bus 14 --people 28 --seats-free 12
Say "simulated camera" in the demo; the real thing is vision/count.py on a webcam or bus video.
"""
import argparse
import json
import time
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", type=int, required=True)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--people", type=int, required=True)
    ap.add_argument("--seats-free", type=int, required=True)
    ap.add_argument("--seats-total", type=int, default=40)
    ap.add_argument("--every", type=float, default=5)
    args = ap.parse_args()
    body = json.dumps({"count": args.people, "seats_free": args.seats_free, "seats_total": args.seats_total}).encode()
    while True:
        req = urllib.request.Request(f"{args.api}/buses/{args.bus}/count", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            seat = json.load(r)
        print(f"bus {args.bus}: camera {args.seats_free} free -> shown {seat['seats_free']} ({seat['seat_source']})",
              flush=True)
        time.sleep(args.every)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
