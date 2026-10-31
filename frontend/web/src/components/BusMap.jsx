import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip } from 'react-leaflet'
import { CAMPUS } from '../geo'
import { roadRoute } from '../routing'

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

// Fetches the road-following line for a route once (cached across re-renders), falling
// back to the straight stop-to-stop line until it resolves or if OSRM can't be reached.
function useRoadPath(straightPoints) {
  const [path, setPath] = useState(straightPoints)
  const key = straightPoints.map((p) => p.join(',')).join(';')

  useEffect(() => {
    let cancelled = false
    setPath(straightPoints)
    roadRoute(straightPoints).then((line) => {
      if (!cancelled && line) setPath(line)
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return path
}

function RoutePolyline({ route, color, dim }) {
  const straight = useMemo(
    () => [CAMPUS, ...[...route.stop_points].reverse().map((p) => [p.lat, p.lng])],
    [route],
  )
  const path = useRoadPath(straight)
  return (
    <Fragment>
      <Polyline positions={path} pathOptions={{ color, weight: dim ? 2 : 4, opacity: dim ? 0.25 : 0.7 }} />
      {!dim && route.stop_points.map((p) => (
        <CircleMarker key={p.id} center={[p.lat, p.lng]} radius={4}
          pathOptions={{ color, weight: 2, fillColor: '#fff', fillOpacity: 1 }}>
          <Tooltip direction="top">{p.name} · {route.name}</Tooltip>
        </CircleMarker>
      ))}
    </Fragment>
  )
}

// The bus number is the marker itself, so riders can tell "18" from "19" at a glance.
function BusMarker({ bus, focused, marked, dimmed, onSelect }) {
  const pos = useGlide(bus.location ? [bus.location.lat, bus.location.lng] : null)
  const tone = bus.sos ? 'sos' : focused ? 'focus' : marked ? 'mark' : dimmed ? 'dim' : bus.device?.online === false ? 'off' : ''
  const icon = useMemo(() => L.divIcon({
    className: 'bus-num-icon', iconSize: null,
    html: `<span class="bus-num-marker ${tone}">${bus.id}</span>`,
  }), [bus.id, tone])
  if (!pos) return null
  return (
    <Marker position={pos} icon={icon} zIndexOffset={focused || bus.sos ? 1000 : marked ? 500 : 0}
      eventHandlers={onSelect ? { click: () => onSelect(bus.id) } : undefined}>
      <Tooltip direction="top" offset={[0, -14]}>
        Bus {bus.id}{bus.route_name ? ` · ${bus.route_name}` : ''}{bus.sos ? ' · SOS' : ''}
        {bus.speed_kmh ? ` · ${Math.round(bus.speed_kmh)} km/h` : ''}
      </Tooltip>
    </Marker>
  )
}

export default function BusMap({ buses, routes = [], focusId, highlightRoute, highlightRoutes, markIds = [], height = 260,
  zoom = 13, onSelect }) {
  const highlighted = highlightRoutes?.length ? highlightRoutes : highlightRoute != null ? [highlightRoute] : null
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
        {routes.map((r, i) => (
          <RoutePolyline key={r.id} route={r} color={ROUTE_COLORS[i % ROUTE_COLORS.length]}
            dim={highlighted != null && !highlighted.includes(r.id)} />
        ))}
        <CircleMarker center={CAMPUS} radius={7} pathOptions={{ color: '#0f172a', fillColor: '#fbbf24', fillOpacity: 1 }}>
          <Tooltip>GEHU campus</Tooltip>
        </CircleMarker>
        {buses.map((b) => (
          <BusMarker key={b.id} bus={b} focused={b.id === focusId} marked={markIds.includes(b.id)}
            dimmed={markIds.length > 0 && !markIds.includes(b.id)} onSelect={onSelect} />
        ))}
      </MapContainer>
      {!anyLocated && <p className="map-note">No bus is sharing its location yet.</p>}
    </div>
  )
}
