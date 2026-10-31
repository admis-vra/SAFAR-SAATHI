"""ETA at a student's own stop: from the bus's live position along the route, or from the timetable before it leaves."""
import math
from datetime import datetime, timedelta, timezone

from geofence import distance_m

IST = timezone(timedelta(hours=5, minutes=30))
CRUISE_KMH = 25.0      # typical town speed used when the bus is stopped or before departure
MIN_KMH, MAX_KMH = 15.0, 45.0
DWELL_S = 30           # stop time at each intermediate stop
ARRIVING_M = 80        # this close to the stop counts as arriving, not passed
OVERDUE_S = 600        # bus still at campus this long after its departure time: no honest clock estimate


def _to_xy(origin, p):
    lat0 = math.radians(origin[0])
    return ((p[1] - origin[1]) * 111_320 * math.cos(lat0), (p[0] - origin[0]) * 111_320)


def _project(p, a, b):
    """Fraction t in [0,1] along a->b closest to p, and the distance from p to that point (metres)."""
    ax, ay = 0.0, 0.0
    bx, by = _to_xy(a, b)
    px, py = _to_xy(a, p)
    seg = bx * bx + by * by
    t = 0.0 if seg == 0 else max(0.0, min(1.0, ((px - ax) * bx + (py - ay) * by) / seg))
    return t, math.hypot(px - t * bx, py - t * by)


def route_path(campus: tuple, stop_points: list[dict]) -> list[tuple]:
    """Outbound order: campus, then the stop nearest campus (G) out to the far end (A)."""
    return [campus] + [(p["lat"], p["lng"]) for p in reversed(stop_points)]


def stop_eta(path: list[tuple], stop_index: int, bus_pos: tuple | None, speed_kmh: float | None,
             departed: bool, departure_at: datetime | None, now: datetime) -> dict:
    """path[0] is campus; stop_index indexes path. Returns minutes, clock time, distance and basis."""
    if not departed or bus_pos is None:
        dist = sum(distance_m(*path[i], *path[i + 1]) for i in range(stop_index))
        if departure_at and (now - departure_at).total_seconds() > OVERDUE_S:
            # Held at campus: we don't know when it will leave, so don't invent a time.
            return {"basis": "waiting", "distance_m": round(dist), "passed": False, "minutes": None, "at": None,
                    "overdue_min": int((now - departure_at).total_seconds() // 60)}
        start = max(departure_at or now, now)
        secs = dist / (CRUISE_KMH / 3.6) + DWELL_S * max(stop_index - 1, 0)
        at = start + timedelta(seconds=secs)
        return {"basis": "timetable", "distance_m": round(dist), "passed": False,
                "minutes": max(0, math.ceil((at - now).total_seconds() / 60)), "at": at.astimezone(IST).strftime("%H:%M")}

    # Closest route segment to the bus.
    best = min(range(len(path) - 1), key=lambda i: _project(bus_pos, path[i], path[i + 1])[1])
    t, _ = _project(bus_pos, path[best], path[best + 1])
    if best >= stop_index:  # already beyond the stop's segment start
        near = distance_m(*bus_pos, *path[stop_index]) <= ARRIVING_M
        return {"basis": "live", "distance_m": round(distance_m(*bus_pos, *path[stop_index])), "passed": not near,
                "minutes": 0, "at": now.astimezone(IST).strftime("%H:%M")}
    seg_len = distance_m(*path[best], *path[best + 1])
    dist = seg_len * (1 - t) + sum(distance_m(*path[i], *path[i + 1]) for i in range(best + 1, stop_index))
    kmh = min(max(speed_kmh, MIN_KMH), MAX_KMH) if speed_kmh and speed_kmh > 3 else CRUISE_KMH
    secs = dist / (kmh / 3.6) + DWELL_S * max(stop_index - best - 1, 0)
    at = now + timedelta(seconds=secs)
    return {"basis": "live", "distance_m": round(dist), "passed": False,
            "minutes": math.ceil(secs / 60), "at": at.astimezone(IST).strftime("%H:%M")}
