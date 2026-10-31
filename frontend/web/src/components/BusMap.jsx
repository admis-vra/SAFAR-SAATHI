import 'leaflet/dist/leaflet.css'
import { Fragment, useEffect, useRef, useState } from 'react'
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip } from 'react-leaflet'
import { CAMPUS } from '../geo'

const GLIDE_MS = 1800      // a little under the 2 s telemetry interval, so the bus keeps moving
const SNAP_IF_DEG = 0.02   // ~2 km: a jump that big (e.g. backlog replay) is snapped, not animated
const ROUTE_COLORS = ['#2563eb', '#16a34a', '#d97706', '#9333ea', '#0891b2']

// Interpolate between GPS fixes so markers glide instead of teleporting.
function useGlide(target) {
  const [pos, setPos] = useState(null)
  const current = useRef(null)
  const [lat, lng] = target ?? []

  useEffect(() => {
    if (lat == null) return
    const from = current.current
    let raf
    if (!from || Math.abs(from[0] - lat) + Math.abs(from[1] - lng) > SNAP_IF_DEG) {
      raf = requestAnimationFrame(() => {
        current.current = [lat, lng]
        setPos([lat, lng])
      })
      return () => cancelAnimationFrame(raf)
    }
    const start = performance.now()
    const step = (now) => {
      const t = Math.min((now - start) / GLIDE_MS, 1)
      const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
      current.current = [from[0] + (lat - from[0]) * e, from[1] + (lng - from[1]) * e]
      setPos(current.current)
      if (t < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [lat, lng])

  return pos ?? (lat == null ? null : [lat, lng])
}

function BusMarker({ bus, focused, onSelect }) {
  const pos = useGlide(bus.location ? [bus.location.lat, bus.location.lng] : null)
  if (!pos) return null
  const fill = bus.sos ? '#dc2626' : focused ? '#2563eb' : bus.device?.online === false ? '#94a3b8' : '#334155'
  return (
    <CircleMarker center={pos} radius={focused || bus.sos ? 11 : 8}
      pathOptions={{ color: '#fff', weight: 2, fillColor: fill, fillOpacity: 1 }}
      eventHandlers={onSelect ? { click: () => onSelect(bus.id) } : undefined}>
      <Tooltip permanent={focused || bus.sos} direction="top">
        Bus {bus.id}{bus.sos ? ' · SOS' : ''}{bus.speed_kmh ? ` · ${Math.round(bus.speed_kmh)} km/h` : ''}
      </Tooltip>
    </CircleMarker>
  )
}

export default function BusMap({ buses, routes = [], focusId, highlightRoute, height = 260, zoom = 13, onSelect }) {
  const focus = buses.find((b) => b.id === focusId && b.location)
  const center = focus ? [focus.location.lat, focus.location.lng] : CAMPUS
  const anyLocated = buses.some((b) => b.location)

  return (
    <div className="map" style={{ height }}>
      <MapContainer key={focus ? `focus-${focusId}` : 'campus'} center={center} zoom={zoom} scrollWheelZoom>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {routes.map((r, i) => {
          const dim = highlightRoute != null && r.id !== highlightRoute
          const color = ROUTE_COLORS[i % ROUTE_COLORS.length]
          return (
            <Fragment key={r.id}>
              <Polyline positions={[CAMPUS, ...[...r.stop_points].reverse().map((p) => [p.lat, p.lng])]}
                pathOptions={{ color, weight: dim ? 2 : 4, opacity: dim ? 0.25 : 0.7 }} />
              {!dim && r.stop_points.map((p) => (
                <CircleMarker key={p.id} center={[p.lat, p.lng]} radius={4}
                  pathOptions={{ color, weight: 2, fillColor: '#fff', fillOpacity: 1 }}>
                  <Tooltip direction="top">{r.name} · Stop {p.name}</Tooltip>
                </CircleMarker>
              ))}
            </Fragment>
          )
        })}
        <CircleMarker center={CAMPUS} radius={7} pathOptions={{ color: '#0f172a', fillColor: '#fbbf24', fillOpacity: 1 }}>
          <Tooltip>GEHU campus</Tooltip>
        </CircleMarker>
        {buses.map((b) => <BusMarker key={b.id} bus={b} focused={b.id === focusId} onSelect={onSelect} />)}
      </MapContainer>
      {!anyLocated && <p className="map-note">No bus is sharing its location yet.</p>}
    </div>
  )
}
