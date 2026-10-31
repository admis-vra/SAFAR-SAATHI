import 'leaflet/dist/leaflet.css'
import { useCallback, useEffect, useState } from 'react'
import { Circle, CircleMarker, MapContainer, TileLayer, Tooltip, useMapEvents } from 'react-leaflet'
import { api, logout } from '../api'
import { TopBar } from '../components/ui'

export default function Admin() {
  const [routes, setRoutes] = useState([])
  const [buses, setBuses] = useState([])
  const [routeId, setRouteId] = useState(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')

  const fail = useCallback((err) => {
    if (err.status === 401) logout()
    else setError(err.message)
  }, [])

  const load = useCallback(() => {
    Promise.all([api('/routes'), api('/buses')]).then(([r, b]) => {
      setRoutes(r)
      setBuses(b.sort((x, y) => x.id - y.id))
      setRouteId((id) => id ?? r[0]?.id ?? null)
    }).catch(fail)
  }, [fail])

  useEffect(load, [load])

  const flash = (msg) => {
    setError('')
    setSaved(msg)
    setTimeout(() => setSaved(''), 2500)
  }

  const route = routes.find((r) => r.id === routeId)
  return (
    <div className="app">
      <TopBar title="Buses & stops" subtitle="Transport office" home="/office" />
      <main className="content wide">
        {error && <div className="alert bad" role="alert">{error}</div>}
        {saved && <div className="alert good" role="status">{saved}</div>}
        <div className="segmented wrap">
          {routes.map((r) => (
            <button key={r.id} className={r.id === routeId ? 'active' : ''} onClick={() => setRouteId(r.id)}>{r.name}</button>
          ))}
        </div>
        {route && (
          <>
            <StopEditor key={route.id} route={route} onError={fail}
              onSaved={(p) => { load(); flash(`Stop ${p.name} saved. Geofences updated.`) }} />
            <section className="card">
              <div className="eyebrow">Buses on {route.name}</div>
              <div className="admin-buses">
                {buses.filter((b) => b.route_id === route.id).map((b) => (
                  <BusEditor key={b.id} bus={b} route={route} onError={fail}
                    onSaved={(nb) => { setBuses((list) => list.map((x) => (x.id === nb.id ? nb : x))); flash(`Bus ${nb.id} saved`) }} />
                ))}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}

function BusEditor({ bus, route, onSaved, onError }) {
  const [form, setForm] = useState({
    departure: bus.usual_departure ?? bus.eta.scheduled, gate: bus.gate, capacity: bus.capacity, stops: bus.stops,
  })
  const [busy, setBusy] = useState(false)

  const toggleStop = (s) => setForm((f) => ({
    ...f, stops: f.stops.includes(s) ? f.stops.filter((x) => x !== s) : [...f.stops, s],
  }))

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      onSaved(await api(`/admin/buses/${bus.id}`, { method: 'PUT', body: { ...form, capacity: Number(form.capacity) } }))
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="card admin-bus form compact" onSubmit={save}>
      <div className="bus-number small">Bus {bus.id}</div>
      <div className="row">
        <label>Usual departure<input type="time" value={form.departure} required
          onChange={(e) => setForm({ ...form, departure: e.target.value })} /></label>
        <label>Gate<input value={form.gate} maxLength={30} required
          onChange={(e) => setForm({ ...form, gate: e.target.value })} /></label>
        <label>Seats<input type="number" min={10} max={80} value={form.capacity} required
          onChange={(e) => setForm({ ...form, capacity: e.target.value })} /></label>
      </div>
      <div className="tiny muted">Stops served</div>
      <div className="chip-row">
        {route.stops.map((s) => (
          <button type="button" key={s} className={`chip ${form.stops.includes(s) ? 'on' : ''}`}
            aria-pressed={form.stops.includes(s)} onClick={() => toggleStop(s)}>{s}</button>
        ))}
      </div>
      <button className="btn primary small" disabled={busy || form.stops.length === 0}>{busy ? 'Saving…' : 'Save'}</button>
    </form>
  )
}

function ClickToPlace({ onPick }) {
  useMapEvents({ click: (e) => onPick(e.latlng) })
  return null
}

function StopEditor({ route, onSaved, onError }) {
  const [stopId, setStopId] = useState(route.stop_points[0]?.id)
  const point = route.stop_points.find((p) => p.id === stopId)
  const [draft, setDraft] = useState(point)
  const [busy, setBusy] = useState(false)

  const pick = (id) => {
    setStopId(id)
    setDraft(route.stop_points.find((p) => p.id === id))
  }

  const save = async () => {
    setBusy(true)
    try {
      onSaved(await api(`/admin/stops/${stopId}`, {
        method: 'PUT', body: { lat: Number(draft.lat), lng: Number(draft.lng), geofence_m: Number(draft.geofence_m) },
      }))
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  if (!draft) return null
  const center = [route.stop_points[0].lat, route.stop_points[0].lng]
  return (
    <section className="card">
      <div className="row-between">
        <div className="eyebrow">Stops on {route.name}</div>
        <span className="tiny muted">Pick a stop, then click the map where buses actually stop · ⚠ = estimated position</span>
      </div>
      <div className="chip-row">
        {route.stop_points.map((p) => (
          <button type="button" key={p.id} className={`chip ${p.id === stopId ? 'on' : ''} ${p.coords_source === 'estimated' ? 'estimated' : ''}`}
            title={p.coords_source === 'estimated' ? 'Position estimated, please check on the map' : p.coords_source === 'osm' ? 'Position from OpenStreetMap' : 'Position set by the office'}
            onClick={() => pick(p.id)}>{p.coords_source === 'estimated' ? '⚠ ' : ''}{p.name}</button>
        ))}
      </div>
      <div className="map-wrap">
        <MapContainer center={center} zoom={13} style={{ height: 340 }} scrollWheelZoom>
          <TileLayer attribution="&copy; OpenStreetMap" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          <ClickToPlace onPick={({ lat, lng }) => setDraft((d) => ({ ...d, lat: +lat.toFixed(6), lng: +lng.toFixed(6) }))} />
          {route.stop_points.filter((p) => p.id !== stopId).map((p) => (
            <CircleMarker key={p.id} center={[p.lat, p.lng]} radius={6} eventHandlers={{ click: () => pick(p.id) }}
              pathOptions={{ color: '#fff', weight: 2, fillColor: '#64748b', fillOpacity: 1 }}>
              <Tooltip direction="top">Stop {p.name}</Tooltip>
            </CircleMarker>
          ))}
          <Circle center={[draft.lat, draft.lng]} radius={Number(draft.geofence_m) || 0}
            pathOptions={{ color: '#2563eb', fillOpacity: 0.15 }} />
          <CircleMarker center={[draft.lat, draft.lng]} radius={8}
            pathOptions={{ color: '#fff', weight: 2, fillColor: '#2563eb', fillOpacity: 1 }}>
            <Tooltip permanent direction="top">Stop {draft.name}</Tooltip>
          </CircleMarker>
        </MapContainer>
      </div>
      <div className="form compact">
        <div className="row">
          <label>Latitude<input type="number" step="0.000001" value={draft.lat}
            onChange={(e) => setDraft({ ...draft, lat: e.target.value })} /></label>
          <label>Longitude<input type="number" step="0.000001" value={draft.lng}
            onChange={(e) => setDraft({ ...draft, lng: e.target.value })} /></label>
          <label>Geofence (m)<input type="number" min={20} max={300} value={draft.geofence_m}
            onChange={(e) => setDraft({ ...draft, geofence_m: e.target.value })} /></label>
        </div>
        <button className="btn primary small" disabled={busy} onClick={save}>{busy ? 'Saving…' : `Save stop ${draft.name}`}</button>
      </div>
    </section>
  )
}
