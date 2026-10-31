"""Count passengers on a bus camera feed and send only the number to the backend.

Usage:
  python count.py --bus 14 --source 0                 # webcam
  python count.py --bus 14 --source bus_video.mp4     # recorded CCTV clip
  python count.py --bus 14 --source 0 --show          # debug window with boxes (demo only)
"""
import argparse
import statistics
import time
from collections import deque

import cv2
import requests
from ultralytics import YOLO

PERSON = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bus", type=int, required=True)
    ap.add_argument("--source", default="0")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between reports")
    ap.add_argument("--samples", type=int, default=7, help="frames per median window")
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    model = YOLO("yolov8n.pt")
    source = int(args.source) if args.source.isdigit() else args.source
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise SystemExit(f"cannot open source {args.source}")

    window = deque(maxlen=args.samples)
    sample_every = args.interval / args.samples
    last_sample = last_report = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            if isinstance(source, str):
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            break

        now = time.time()
        if now - last_sample < sample_every and not args.show:
            continue
        last_sample = now

        result = model(frame, classes=[PERSON], conf=args.conf, verbose=False)[0]
        window.append(len(result.boxes))

        if args.show:
            annotated = result.plot()
            cv2.putText(annotated, f"Bus {args.bus}  count: {window[-1]}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.imshow("SafarSaathi seat count (debug)", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

        # Median over the window absorbs frames where people block each other.
        if now - last_report >= args.interval and window:
            count = int(statistics.median(window))
            try:
                requests.post(f"{args.api}/buses/{args.bus}/count", json={"count": count}, timeout=3)
                print(f"bus {args.bus}: sent count {count} (window {list(window)})")
            except requests.RequestException as e:
                print(f"bus {args.bus}: send failed: {e}")
            last_report = now

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
