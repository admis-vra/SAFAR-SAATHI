"""Load the real bus schedule (import_data/bus_schedule.csv, made by tools_clean_bus_schedule.py).

Step 1 - find stop coordinates on OpenStreetMap (Nominatim, 1 request/second), review them:
  .venv/Scripts/python import_bus_schedule.py geocode
  -> import_data/stops_geocoded.csv   (fix wrong lat/lng by hand, or later in the app's Buses & stops page)

Step 2 - write routes, stops and buses to the database in DATABASE_URL (one transaction):
  .venv/Scripts/python import_bus_schedule.py load --dry-run
  .venv/Scripts/python import_bus_schedule.py load [--replace-demo]

  --replace-demo  hides the sample routes and any bus not in the CSV (rows are kept, marked inactive).

Each distinct stop sequence in the CSV becomes one route; a bus serves every stop on its route. The final
"Geu"/"Gehu" stop is the campus itself (the depot), not a stop. Stops whose coordinates were corrected in the
app (coords_source = 'manual') are never overwritten by a re-run.
"""
import argparse
import asyncio
import csv
import math
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
SCHEDULE = HERE / "import_data" / "bus_schedule.csv"
GEOCODED = HERE / "import_data" / "stops_geocoded.csv"
CAMPUS = (30.2687, 77.9945)       # same approximate campus point as the web app (frontend/web/src/geo.js)
ROUTE_ID_BASE = 100
DEFAULT_GATE = "Main gate"
DEFAULT_HOME_DEPARTURE = "16:00"  # used only until staff assign the bus to a 1/4/6 PM slot
MAX_NEIGHBOUR_KM = 20             # a geocoded stop this far from both neighbours is treated as wrong
MAX_DETOUR_KM = 3                 # ...or one that adds this much detour between its two known neighbours

CAMPUS_NAMES = {"geu", "gehu", "geu/gehu"}
# Phrase fixes on the whole route (before splitting on "-").
PHRASE_FIXES = [
    (r"\bVikasNagar - Tehsil\b", "Vikasnagar Tehsil"),
    (r"\bFountain Chowk Rispana\b", "Fountain Chowk - Rispana"),   # two stops missing a dash in the sheet
]
# Spelling fixes per stop (lower-case key). Remove a line if a guess is wrong.
NAME_FIXES = {
    "clock tower": "Ghanta Ghar (Clock Tower)",
    "asley hall": "Astley Hall",
    "darshanlal chowk": "Darshan Lal Chowk",
    "vijay coloney": "Vijay Colony",
    "yamuna coloney": "Yamuna Colony",
    "shastdhara crossing": "Sahastradhara Crossing",
    "shaspur": "Sahaspur",
    "harbatpur chowk": "Herbertpur Chowk",
    "balliwalachowk": "Balliwala Chowk",
    "baliwala chowk": "Balliwala Chowk",
    "prem nagar": "Premnagar",
    "kargi": "Kargi Chowk",
    "selaqui": "Selaqui Chowk",
    "laal tappad": "Lal Tappad",
    "hathibadkala": "Hathibarkala",
}
# Better search text for a few names Nominatim won't match as written.
QUERY_OVERRIDES = {
    "Ghanta Ghar (Clock Tower)": "Ghanta Ghar",
    "6 No. Pulia": "6 Number Pulia",
    "ISBT": "ISBT Dehradun",
    "CM House": "Chief Minister Residence",
}
# OpenStreetMap only has a same-named place elsewhere (checked by hand): always estimate these instead.
SKIP_LOOKUP = {"Mata Mandir"}
VIEWBOX = "77.65,30.60,78.30,30.10"   # Dehradun district incl. Vikasnagar, Doiwala, Ranipokhri


def parse_route(raw: str) -> tuple[list[str], list[str]]:
    """'A - B - ... - Geu' -> ([stops without campus, in travel order], [warnings])."""
    for pattern, repl in PHRASE_FIXES:
        raw = re.sub(pattern, repl, raw)
    stops, warnings = [], []
    for token in (t.strip() for t in raw.split("-")):
        if not token:
            continue
        name = NAME_FIXES.get(token.lower(), token)
        if name.lower() in CAMPUS_NAMES:
            continue
        if name in stops:
            warnings.append(f"'{name}' appears twice on this route (a loop); kept the first visit only")
            continue
        stops.append(name)
    return stops, warnings


def hhmm(text: str) -> str | None:
    m = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)", text.strip(), re.IGNORECASE)
    if not m:
        return None
    h, mins, ap = int(m[1]), int(m[2]), m[3].upper()
    h = h % 12 + (12 if ap == "PM" else 0)
    return f"{h:02d}:{mins:02d}"


def read_schedule(path: Path = SCHEDULE) -> tuple[list[dict], list[dict], list[str]]:
    """-> (routes [{id, name, stops}], buses [...], warnings)."""
    routes: dict[tuple, dict] = {}
    buses, warnings, seen_ids = [], [], set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stops, w = parse_route(row["Route"])
            bus_id = int(row["Bus No."])
            warnings += [f"Bus {bus_id}: {x}" for x in w]
            if bus_id in seen_ids:
                raise SystemExit(f"Bus {bus_id} appears twice in {path.name}")
            seen_ids.add(bus_id)
            if not stops:
                raise SystemExit(f"Bus {bus_id}: route has no stops besides campus")
            key = tuple(stops)
            if key not in routes:
                routes[key] = {"id": ROUTE_ID_BASE + len(routes) + 1, "stops": stops}
            buses.append({"id": bus_id, "registration": row["Reg. No."].strip(), "route_id": routes[key]["id"],
                          "stops": stops, "seats": int(row["Seats"]),
                          "run1": hhmm(row["1st Run Start"]), "run2": hhmm(row["2nd Run Start"])})
    names: dict[str, int] = {}
    for r in routes.values():
        base = r["stops"][0] if len(r["stops"]) == 1 else f"{r['stops'][0]} via {r['stops'][-1]}"
        names[base] = names.get(base, 0) + 1
        r["name"] = base if names[base] == 1 else f"{base} ({names[base]})"
    return list(routes.values()), buses, warnings


# ---------- step 1: geocode ----------

def load_geocoded(path: Path = GEOCODED) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["stop"]: r for r in csv.DictReader(f)}


def save_geocoded(rows: dict[str, dict], path: Path = GEOCODED):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["stop", "lat", "lng", "source", "osm_match"])
        w.writeheader()
        for name in sorted(rows):
            w.writerow({k: rows[name].get(k, "") for k in w.fieldnames})


def geocode(names: list[str]):
    import httpx
    rows = load_geocoded()
    for n in SKIP_LOOKUP:
        rows.pop(n, None)
    todo = [n for n in names if n not in SKIP_LOOKUP and (n not in rows or not rows[n]["lat"])]
    print(f"{len(names)} stops, {len(todo)} to look up (about {len(todo)} s)")
    headers = {"User-Agent": "SafarSaathi-bus-import/1.0 (GEHU student transport project)"}
    with httpx.Client(headers=headers, timeout=20) as http:
        for i, name in enumerate(todo, 1):
            query = QUERY_OVERRIDES.get(name, name)
            hit = None
            short = re.sub(r"\s+(Chowk|More)$", "", query)
            for q in dict.fromkeys((f"{query}, Dehradun", query, short)):   # all bounded to the district
                resp = http.get("https://nominatim.openstreetmap.org/search", params={
                    "q": q, "format": "jsonv2", "limit": 1, "countrycodes": "in", "viewbox": VIEWBOX, "bounded": 1})
                resp.raise_for_status()
                time.sleep(1.1)   # Nominatim usage policy: at most 1 request per second
                if resp.json():
                    hit = resp.json()[0]
                    break
            rows[name] = ({"stop": name, "lat": f"{float(hit['lat']):.6f}", "lng": f"{float(hit['lon']):.6f}",
                           "source": "osm", "osm_match": hit.get("display_name", "")[:120]}
                          if hit else {"stop": name, "lat": "", "lng": "", "source": "", "osm_match": "NOT FOUND"})
            print(f"  [{i}/{len(todo)}] {name}: {'found' if hit else 'NOT FOUND'}")
            save_geocoded(rows)   # keep progress if interrupted
    missing = [n for n in names if not rows[n]["lat"]]
    print(f"Wrote {GEOCODED.name}. {len(names) - len(missing)} found, {len(missing)} not found "
          f"(those get estimated positions between their neighbours at load time).")


# ---------- step 2: load ----------

def _km(a: tuple, b: tuple) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 111.32, (a[1] - b[1]) * 111.32 * math.cos(lat))


def resolve_coords(routes: list[dict], geocoded: dict[str, dict]) -> dict[str, tuple]:
    """Stop name -> (lat, lng, source). Missing or implausible points are estimated along their route,
    evenly between the nearest known stops (campus counts as known at the end)."""
    known = {n: (float(r["lat"]), float(r["lng"])) for n, r in geocoded.items() if r.get("lat")}
    # A point far from both neighbours on every route it's on is almost certainly a wrong match.
    for r in routes:
        seq = r["stops"]
        for i, name in enumerate(seq):
            if name not in known:
                continue
            prv, nxt = (seq[i - 1] if i else None), (seq[i + 1] if i + 1 < len(seq) else None)
            a, b = known.get(prv), (known.get(nxt) if nxt else CAMPUS)   # after the last stop comes campus
            nbrs = [p for p in (a, b) if p]
            if nbrs and all(_km(known[name], p) > MAX_NEIGHBOUR_KM for p in nbrs):
                print(f"  ignoring OSM match for {name}: over {MAX_NEIGHBOUR_KM} km from its neighbours")
                del known[name]
                continue
            if a and b and _km(a, known[name]) + _km(known[name], b) - _km(a, b) > MAX_DETOUR_KM:
                print(f"  ignoring OSM match for {name}: a {MAX_DETOUR_KM}+ km detour between {prv} and {nxt or 'campus'}")
                del known[name]
    out = {n: (*p, geocoded[n].get("source") or "osm") for n, p in known.items()}
    for r in routes:
        pts = [known.get(s) for s in r["stops"]] + [CAMPUS]
        for i, name in enumerate(r["stops"]):
            if name in out:
                continue
            nxt = next(j for j in range(i + 1, len(pts)) if pts[j] is not None)
            prv = next((j for j in range(i - 1, -1, -1) if pts[j] is not None), None)
            if prv is None:   # nothing known before it: place it just before the next known point
                a, b = pts[nxt], pts[nxt + 1] if nxt + 1 < len(pts) and pts[nxt + 1] else CAMPUS
                frac = -(nxt - i) * 0.5
                p = (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
            else:
                frac = (i - prv) / (nxt - prv)
                p = (pts[prv][0] + (pts[nxt][0] - pts[prv][0]) * frac, pts[prv][1] + (pts[nxt][1] - pts[prv][1]) * frac)
            pts[i] = p
            out[name] = (*p, "estimated")
    return out


async def load(dsn: str, routes: list[dict], buses: list[dict], coords: dict, replace_demo: bool, dry_run: bool):
    import asyncpg
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20, command_timeout=120)
    try:
        if not await conn.fetchval("select exists (select 1 from information_schema.columns "
                                   "where table_name = 'buses' and column_name = 'run1_start')"):
            raise SystemExit("Run migrate.py first (needs migration 20261102000010_daily_plans).")
        async with conn.transaction():
            for r in routes:
                await conn.execute("insert into routes (id, name, is_active) values ($1, $2, true) on conflict (id) "
                                   "do update set name = excluded.name, is_active = true", r["id"], r["name"])
                # Free the seq numbers first so re-ordered stops don't collide on unique (route_id, seq).
                # Stops dropped from a route keep their row (trips/profiles may point at it) with seq < 0.
                await conn.execute("update stops set seq = -abs(seq) - 1000 where route_id = $1", r["id"])
                for seq, name in enumerate(r["stops"], 1):
                    lat, lng, source = coords[name]
                    await conn.execute("""
                        insert into stops (route_id, seq, stop_name, location, coords_source)
                        values ($1, $2, $3, st_setsrid(st_makepoint($5, $4), 4326)::geography, $6)
                        on conflict (route_id, stop_name) do update set seq = excluded.seq,
                          location = case when stops.coords_source = 'manual' then stops.location else excluded.location end,
                          coords_source = case when stops.coords_source = 'manual' then 'manual' else excluded.coords_source end
                        """, r["id"], seq, name, lat, lng, source)
                await conn.execute("""
                    update routes r set polyline = (
                      select json_build_object('type', 'LineString', 'coordinates',
                               json_agg(json_build_array(st_x(s.location::geometry), st_y(s.location::geometry)) order by s.seq))::jsonb
                      from stops s where s.route_id = r.id and s.seq > 0)
                    where r.id = $1""", r["id"])
            for b in buses:
                t = lambda v: datetime.strptime(v, "%H:%M").time() if v else None
                await conn.execute("""
                    insert into buses (id, registration_number, route_id, gate, serves_stops, max_capacity,
                                       departure_time, run1_start, run2_start, active)
                    values ($1, $2, $3, $4, $5, $6, $7, $8, $9, true)
                    on conflict (id) do update set registration_number = excluded.registration_number,
                      route_id = excluded.route_id, serves_stops = excluded.serves_stops,
                      max_capacity = excluded.max_capacity, run1_start = excluded.run1_start,
                      run2_start = excluded.run2_start, active = true""",
                    b["id"], b["registration"], b["route_id"], DEFAULT_GATE, b["stops"], b["seats"],
                    t(DEFAULT_HOME_DEPARTURE), t(b["run1"]), t(b["run2"]))
            await conn.execute("insert into bus_live_state (bus_id) select id from buses on conflict (bus_id) do nothing")
            if replace_demo:
                hidden_routes = await conn.execute("update routes set is_active = false where not (id = any($1::int[]))",
                                                   [r["id"] for r in routes])
                hidden_buses = await conn.execute("update buses set active = false where not (id = any($1::int[]))",
                                                  [b["id"] for b in buses])
                print(f"  hidden: {hidden_routes.split()[-1]} other routes, {hidden_buses.split()[-1]} other buses")
            if dry_run:
                raise _DryRun()
    except _DryRun:
        print("Dry run: everything checked, nothing saved.")
        return
    finally:
        await conn.close()
    print(f"Loaded {len(routes)} routes, {sum(len(r['stops']) for r in routes)} route stops, {len(buses)} buses.")


class _DryRun(Exception):
    pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("geocode")
    lp = sub.add_parser("load")
    lp.add_argument("--replace-demo", action="store_true")
    lp.add_argument("--dry-run", action="store_true")
    sub.add_parser("show", help="print the routes parsed from the CSV")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # route names contain "→"

    routes, buses, warnings = read_schedule()
    for w in warnings:
        print("warning:", w)
    names = sorted({s for r in routes for s in r["stops"]})
    if args.cmd == "show":
        for r in routes:
            ids = [b["id"] for b in buses if b["route_id"] == r["id"]]
            print(f"{r['id']} {r['name']}  buses {ids}\n    " + " - ".join(r["stops"]))
        print(f"{len(routes)} routes, {len(names)} distinct stops, {len(buses)} buses")
    elif args.cmd == "geocode":
        geocode(names)
    else:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
        dsn = os.environ.get("DATABASE_URL") or sys.exit("Set DATABASE_URL in backend/.env")
        geocoded = load_geocoded()
        if not geocoded:
            sys.exit(f"Run 'geocode' first (no {GEOCODED.name}).")
        coords = resolve_coords(routes, geocoded)
        est = sorted(n for n, c in coords.items() if c[2] == "estimated")
        print(f"{len(est)} of {len(coords)} stops have estimated positions: {', '.join(est) or '-'}")
        asyncio.run(load(dsn, routes, buses, coords, args.replace_demo, args.dry_run))


if __name__ == "__main__":
    main()
