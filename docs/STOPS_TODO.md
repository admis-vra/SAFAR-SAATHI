# Replace sample stop coordinates

The stops in `supabase/migrations/20261031000002_sample_seed.sql` are **sample points**: evenly spaced
between the GEHU campus and a rough route endpoint. Geofencing (M3) and fallback routing (M6) are only as
good as these coordinates, so replace them before any real test ride.

For each route, ride or walk it once with a phone and record:

| Route | Stop | Real name | Lat | Lng | Geofence radius (m) | Checked by |
|---|---|---|---|---|---|---|
| 1 | A | | | | 60 | |
| 1 | B | | | | 60 | |
| … | … | | | | | |

- Use 5 decimal places (≈1 m). Google Maps → long-press → copy the coordinates.
- Use a larger radius (80–120 m) where buses stop loosely, e.g. a wide market road.
- Add the campus depot / gate location too (used by M3 for "departed from campus").
- Put the real values in a **new** migration (e.g. `…_real_stops.sql`) with `update stops set …`,
  rather than editing the sample seed, so already-migrated databases also get them.
