"""Per-bus geofence tracking with hysteresis, so GPS jitter at a fence edge doesn't flap enter/exit."""
import math
from dataclasses import dataclass, field

CONFIRM_FIXES = 2     # consecutive fixes needed before an enter/exit counts
EXIT_MARGIN = 1.2     # must be 20% beyond the radius to count as outside


def distance_m(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class Zone:
    key: str             # "depot" or "stop:<id>"
    kind: str            # "depot" | "stop"
    name: str
    lat: float
    lng: float
    radius_m: float
    stop_id: int | None = None
    terminal: bool = False   # far end of the route: arriving here ends the trip


@dataclass
class BusFence:
    zones: list[Zone]
    inside: set[str] = field(default_factory=set)
    _streak: dict[str, int] = field(default_factory=dict)

    def seed(self, lat: float, lng: float):
        """Start from a known position without emitting events (e.g. after a backend restart)."""
        self.inside = {z.key for z in self.zones if distance_m(lat, lng, z.lat, z.lng) <= z.radius_m}
        self._streak.clear()

    def update(self, lat: float, lng: float) -> list[tuple[str, Zone]]:
        events = []
        for z in self.zones:
            d = distance_m(lat, lng, z.lat, z.lng)
            was_inside = z.key in self.inside
            now_inside = d <= z.radius_m * EXIT_MARGIN if was_inside else d <= z.radius_m
            if now_inside == was_inside:
                self._streak[z.key] = 0
                continue
            self._streak[z.key] = self._streak.get(z.key, 0) + 1
            if self._streak[z.key] >= CONFIRM_FIXES:
                self._streak[z.key] = 0
                if now_inside:
                    self.inside.add(z.key)
                    events.append(("enter", z))
                else:
                    self.inside.discard(z.key)
                    events.append(("exit", z))
        return events
