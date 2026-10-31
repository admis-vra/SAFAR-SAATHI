"""Driver's phone as the bus GPS: accuracy filter, device priority, speed, and trips from geofences."""
from datetime import datetime, timezone

from test_batch1 import driver

BUS = 14


def post(c, lat, lng, **extra):
    return c.post(f"/buses/{BUS}/location", json={"lat": lat, "lng": lng, **extra}, headers=driver(BUS))


def test_rough_fix_is_ignored_and_others_move_the_bus(client_factory):
    import main
    with client_factory() as c:
        d = main.depot
        before = main.buses[BUS]["location"]
        r = post(c, d["lat"] + 0.001, d["lng"], accuracy_m=900).json()
        assert r["accepted"] is False and "too rough" in r["reason"]
        assert main.buses[BUS]["location"] == before

        r = post(c, d["lat"] + 0.001, d["lng"], accuracy_m=12, speed_kmh=18).json()
        assert r["accepted"] is True
        bus = c.get(f"/buses/{BUS}").json()
        assert bus["location"] == {"lat": d["lat"] + 0.001, "lng": d["lng"]}
        assert bus["location_source"] == "phone" and bus["location_at"] and bus["speed_kmh"] == 18


def test_speed_is_derived_when_phone_gives_none(client_factory):
    import main
    with client_factory() as c:
        d = main.depot
        post(c, d["lat"] + 0.002, d["lng"])
        main.buses[BUS]["location_at"] = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - 10, timezone.utc)
        post(c, d["lat"] + 0.002 + 0.0009, d["lng"])  # ~100 m in ~10 s = ~36 km/h
        assert 30 < c.get(f"/buses/{BUS}").json()["speed_kmh"] < 42


def test_bus_device_gps_takes_priority(client_factory):
    import main
    with client_factory() as c:
        bus = main.buses[BUS]
        bus["location_source"], bus["location_at"] = "device", datetime.now(timezone.utc)
        before = bus["location"]
        r = post(c, main.depot["lat"] + 0.003, main.depot["lng"]).json()
        assert r["accepted"] is False and "device" in r["reason"]
        assert bus["location"] == before


def test_phone_gps_alone_starts_and_ends_a_trip(client_factory):
    import main
    with client_factory() as c:
        d = main.depot
        main.buses[BUS]["location_source"] = None
        # Each move is sent twice: the geofence needs 2 consecutive fixes, so one jittery fix can't start a trip.
        for _ in range(2):
            assert post(c, d["lat"], d["lng"]).json()["accepted"]                   # at campus
        assert post(c, d["lat"] + 0.012, d["lng"] + 0.012).json()["accepted"]       # ~1.7 km out
        assert c.get(f"/buses/{BUS}").json()["trip"]["id"] is None                  # one fix is not enough
        post(c, d["lat"] + 0.0125, d["lng"] + 0.012)
        out = c.get(f"/buses/{BUS}").json()
        assert out["status"] == "departed" and out["trip"]["id"] is not None
        for _ in range(2):
            post(c, d["lat"], d["lng"])                                             # back at campus
        back = c.get(f"/buses/{BUS}").json()
        assert back["trip"]["id"] is None and back["trip"]["in_depot"] is True


def test_bus_on_the_road_before_its_trip_gives_distance_not_a_time(client_factory, monkeypatch):
    """Morning run in: driver shares GPS out on the route; the stop block must not say 'still at campus'."""
    import main
    with client_factory() as c:
        bus = main.buses[BUS]
        bus["status"], bus["location_source"] = "scheduled", None
        stop = main.routes[bus["route_id"]]["stop_points"][-1]  # nearest-campus stop, served by Bus 14
        post(c, stop["lat"] + 0.01, stop["lng"])
        post(c, stop["lat"] + 0.01, stop["lng"])
        e = main.my_stop_eta(bus, stop["name"])
        assert e["basis"] == "on_road" and e["minutes"] is None and 1000 < e["distance_m"] < 1250
