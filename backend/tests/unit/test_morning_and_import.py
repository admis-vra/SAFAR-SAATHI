"""Pure tests (no database): morning-run ETAs and parsing the real timetable.

Run without the DB fixtures:  .venv/Scripts/python -m pytest --noconftest tests/unit
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import import_bus_schedule as imp  # noqa: E402
import stop_eta  # noqa: E402

IST = stop_eta.IST
CAMPUS = (30.2687, 77.9945)
# Three stops due north of campus, ~1.1 km apart (far end first, as stored: seq 1 = first pickup).
POINTS = [{"name": n, "lat": lat, "lng": 77.9945} for n, lat in (("A", 30.3020), ("B", 30.2909), ("C", 30.2798))]
NOW = datetime(2026, 11, 2, 7, 10, tzinfo=IST)


def path():
    return stop_eta.inbound_path(CAMPUS, POINTS)


def test_inbound_path_runs_from_first_stop_to_campus():
    p = path()
    assert p[0] == (30.3020, 77.9945) and p[-1] == CAMPUS and len(p) == 4


def test_timetable_arrival_grows_along_the_route():
    start = datetime(2026, 11, 2, 7, 0, tzinfo=IST)
    times = [stop_eta.inbound_timetable(path(), i, start) for i in range(4)]
    assert times[0] == start
    assert times == sorted(times) and times[3] - start < timedelta(minutes=15)


def test_bus_still_driving_to_its_first_stop_has_not_passed_anything():
    bus = (30.3120, 77.9945)   # ~1.1 km beyond the first stop, coming towards it
    first = stop_eta.inbound_live(path(), 0, bus, 30, NOW)
    second = stop_eta.inbound_live(path(), 1, bus, 30, NOW)
    assert not first["passed"] and first["minutes"] >= 1
    assert not second["passed"] and second["distance_m"] > first["distance_m"]


def test_bus_between_stops_has_passed_earlier_and_is_coming_to_later():
    bus = (30.2950, 77.9945)   # between A and B
    assert stop_eta.inbound_live(path(), 0, bus, 30, NOW)["passed"] is True
    to_c = stop_eta.inbound_live(path(), 2, bus, 30, NOW)
    assert to_c["passed"] is False and 1000 < to_c["distance_m"] < 2000


def test_bus_at_a_stop_is_arriving_not_passed():
    at_b = stop_eta.inbound_live(path(), 1, (30.2910, 77.9945), 5, NOW)
    assert at_b["passed"] is False and at_b["minutes"] == 0
    at_a = stop_eta.inbound_live(path(), 0, (30.3021, 77.9945), 5, NOW)
    assert at_a["passed"] is False and at_a["minutes"] == 0


# ---------- timetable import ----------

def test_parse_route_drops_campus_and_fixes_names():
    stops, warnings = imp.parse_route("Clock Tower - Darshanlal Chowk - Saharanpur Chowk - ISBT - Geu/Gehu")
    assert stops == ["Ghanta Ghar (Clock Tower)", "Darshan Lal Chowk", "Saharanpur Chowk", "ISBT"]
    assert warnings == []


def test_parse_route_splits_missing_dash_and_dedupes_loops():
    stops, _ = imp.parse_route("Ladpur - 6 No. Pulia - Fountain Chowk Rispana - Kargi - ISBT - Geu")
    assert stops == ["Ladpur", "6 No. Pulia", "Fountain Chowk", "Rispana", "Kargi Chowk", "ISBT"]
    stops, warnings = imp.parse_route("Garhi Cant - Vijay Coloney - CM House - Garhi Cant - ONGC Chowk - Geu")
    assert stops == ["Garhi Cant", "Vijay Colony", "CM House", "ONGC Chowk"] and len(warnings) == 1


def test_hhmm():
    assert imp.hhmm("7:00 AM") == "07:00"
    assert imp.hhmm("6:45 AM") == "06:45"
    assert imp.hhmm("12:30 PM") == "12:30"
    assert imp.hhmm("No Service") is None


@pytest.mark.skipif(not imp.SCHEDULE.exists(), reason="import_data/bus_schedule.csv not present")
def test_real_schedule_shape():
    routes, buses, _ = imp.read_schedule()
    assert len(buses) == 97 and len({b["id"] for b in buses}) == 97
    ghanta_ghar = {b["id"] for b in buses if "Ghanta Ghar (Clock Tower)" in b["stops"]}
    assert {18, 19, 10, 9, 42} <= ghanta_ghar
    assert all(b["run1"] for b in buses)


def test_missing_and_far_off_stops_are_estimated_between_neighbours():
    routes = [{"id": 1, "stops": ["A", "B", "C"]}]
    geocoded = {"A": {"lat": "30.3020", "lng": "77.9945", "source": "osm"},
                "B": {"lat": "", "lng": ""},
                "C": {"lat": "31.5000", "lng": "77.9945", "source": "osm"}}   # ~130 km away: a wrong match
    coords = imp.resolve_coords(routes, geocoded)
    assert coords["A"][2] == "osm"
    assert coords["B"][2] == coords["C"][2] == "estimated"
    assert CAMPUS[0] < coords["C"][0] < coords["B"][0] < coords["A"][0]   # evenly between A and campus
