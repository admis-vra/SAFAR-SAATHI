"""Digital queue rules for afternoon departures (no DB access here, so they're easy to test)."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
OPENS_BEFORE_MIN = 30   # queue opens this long before the scheduled departure
CALL_WINDOW_S = 300     # a called student has this long to board


def now_ist() -> datetime:
    return datetime.now(IST)


def service_day(now: datetime) -> date:
    return now.astimezone(IST).date()


@dataclass
class Window:
    state: str            # "not_open" | "open" | "closed"
    opens_at: datetime
    departs_at: datetime


def window(departure_hhmm: str, now: datetime, last_trip_start: datetime | None) -> Window:
    """Open from 30 min before the scheduled departure until the bus actually leaves, so a late bus keeps
    its queue. A trip that started after the queue opened means today's departure has happened."""
    h, m = map(int, departure_hhmm.split(":"))
    now = now.astimezone(IST)
    departs = now.replace(hour=h, minute=m, second=0, microsecond=0)
    opens = departs - timedelta(minutes=OPENS_BEFORE_MIN)
    if last_trip_start is not None and last_trip_start >= opens:
        return Window("closed", opens, departs)
    if now < opens:
        return Window("not_open", opens, departs)
    return Window("open", opens, departs)


def room_to_call(seats_free: int, already_called: int) -> int:
    """How many more waiting students to call: free seats not already promised to someone called."""
    return max(seats_free - already_called, 0)
