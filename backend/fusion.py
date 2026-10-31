"""IR + camera sensor fusion (blueprint §4.1).

IR break-beams miscount when bodies overlap at the door; the camera struggles with occlusion while the bus
moves. So: trust the camera when stationary, the IR counter when moving, and when they disagree for a while
at rest, re-anchor the IR count to the camera.
"""
from dataclasses import dataclass

IR_STALE_AFTER_S = 10       # devices report every 2 s
CV_STALE_AFTER_S = 45       # vision/count.py reports every 30 s by default
STATIONARY_KMH = 3.0
STATIONARY_FOR_S = 5        # must be slow this long to count as stationary
RECAL_AFTER_S = 10          # stationary this long before recalibrating
RECAL_THRESHOLD = 4         # seats of disagreement that triggers recalibration


@dataclass
class Fusion:
    mode: str                     # "cv" | "ir" | "ir_only" | "none"
    seats_free: int | None
    expected_empty: int | None    # from IR (+ offset)
    visual_empty: int | None      # from camera
    deviation: int | None
    stationary_s: float


def ir_people(bus: dict) -> int | None:
    if bus.get("ir_net") is None:
        return None
    return max(bus["ir_net"] + bus.get("ir_offset", 0), 0)


def camera_empty(bus: dict) -> int | None:
    cam = bus.get("seat_cam")
    if cam:
        if cam["total"] >= bus["capacity"]:
            return min(cam["free"], bus["capacity"])
        return round(cam["free"] * bus["capacity"] / cam["total"])  # partial view: extrapolate
    if bus.get("cv_count") is not None:
        return max(bus["capacity"] - bus["cv_count"], 0)
    return None


def stationary_seconds(bus: dict) -> float:
    since, last = bus.get("stationary_since"), bus.get("last_device_ts")
    return (last - since).total_seconds() if since and last else 0.0


def compute(bus: dict, now: float) -> Fusion:
    ir_fresh = bus.get("ir_seen") is not None and now - bus["ir_seen"] <= IR_STALE_AFTER_S
    cv_fresh = bus.get("cv_seen") is not None and now - bus["cv_seen"] <= CV_STALE_AFTER_S
    people = ir_people(bus) if ir_fresh else None
    expected = max(bus["capacity"] - people, 0) if people is not None else None
    visual = camera_empty(bus) if cv_fresh else None
    still = stationary_seconds(bus)
    deviation = abs(expected - visual) if expected is not None and visual is not None else None

    if expected is not None and visual is not None:
        mode = "cv" if still >= STATIONARY_FOR_S else "ir"
        seats = visual if mode == "cv" else expected
    elif expected is not None:
        mode, seats = "ir_only", expected
    else:
        mode, seats = "none", None   # caller falls back to camera-only / allocation logic
    return Fusion(mode, seats, expected, visual, deviation, still)


def recalibrate(bus: dict, now: float) -> int | None:
    """If both sensors are fresh, the bus has been at rest long enough and they disagree, move the IR offset
    so the IR count matches the camera. Returns the new offset, or None if nothing changed."""
    f = compute(bus, now)
    if f.deviation is None or f.deviation <= RECAL_THRESHOLD or f.stationary_s < RECAL_AFTER_S:
        return None
    camera_people = bus["capacity"] - f.visual_empty
    bus["ir_offset"] = camera_people - bus["ir_net"]
    return bus["ir_offset"]
