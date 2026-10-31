// Road-following route geometry for the bus map, via the public OSRM demo server.
// Straight stop-to-stop lines look like flight paths (they cut through blocks); this
// snaps the line to actual roads. Results are cached in memory + sessionStorage, keyed
// by the stop coordinates, so we never re-fetch the same route twice in a session.

const OSRM_BASE = 'https://router.project-osrm.org/route/v1/driving/'
const memCache = new Map()

function cacheKey(points) {
  return points.map((p) => `${p[0].toFixed(5)},${p[1].toFixed(5)}`).join(';')
}

function readSessionCache(key) {
  try {
    const raw = sessionStorage.getItem(`road-route:${key}`)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function writeSessionCache(key, coords) {
  try {
    sessionStorage.setItem(`road-route:${key}`, JSON.stringify(coords))
  } catch {
    // storage full or unavailable: skip persistence, memory cache still works
  }
}

// points: array of [lat, lng], in travel order (at least 2). Returns a promise of
// [lat, lng] pairs following roads, or null if the lookup failed (caller should
// fall back to drawing straight lines between `points`).
export async function roadRoute(points) {
  if (points.length < 2) return null
  const key = cacheKey(points)
  if (memCache.has(key)) return memCache.get(key)
  const cached = readSessionCache(key)
  if (cached) {
    memCache.set(key, cached)
    return cached
  }
  const coordsParam = points.map(([lat, lng]) => `${lng},${lat}`).join(';')
  try {
    const res = await fetch(`${OSRM_BASE}${coordsParam}?overview=full&geometries=geojson`)
    if (!res.ok) throw new Error(`OSRM ${res.status}`)
    const data = await res.json()
    const line = data.routes?.[0]?.geometry?.coordinates
    if (!line?.length) throw new Error('no route geometry')
    const latLngs = line.map(([lng, lat]) => [lat, lng])
    memCache.set(key, latLngs)
    writeSessionCache(key, latLngs)
    return latLngs
  } catch {
    memCache.set(key, null)   // don't hammer OSRM again this session for a route it can't solve
    return null
  }
}
