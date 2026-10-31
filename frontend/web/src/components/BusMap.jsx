import 'leaflet/dist/leaflet.css'
import { CircleMarker, MapContainer, Polyline, TileLayer, Tooltip } from 'react-leaflet'
import { CAMPUS, DEMO_PATH } from '../geo'

export default function BusMap({ buses, focusId, height = 260 }) {
  const visible = buses.filter((b) => b.location)
  const focus = visible.find((b) => b.id === focusId)
  const center = focus ? [focus.location.lat, focus.location.lng] : CAMPUS

  return (
    <div className="map" style={{ height }}>
      <MapContainer key={focus ? 'focus' : 'campus'} center={center} zoom={14} scrollWheelZoom={false}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <Polyline positions={DEMO_PATH} pathOptions={{ color: '#94a3b8', dashArray: '6 6', weight: 3 }} />
        <CircleMarker center={CAMPUS} radius={7} pathOptions={{ color: '#0f172a', fillColor: '#fbbf24', fillOpacity: 1 }}>
          <Tooltip>GEHU campus</Tooltip>
        </CircleMarker>
        {visible.map((b) => (
          <CircleMarker
            key={b.id}
            center={[b.location.lat, b.location.lng]}
            radius={b.id === focusId ? 11 : 7}
            pathOptions={{
              color: '#fff',
              weight: 2,
              fillColor: b.id === focusId ? '#2563eb' : '#64748b',
              fillOpacity: 1,
            }}
          >
            <Tooltip permanent={b.id === focusId} direction="top">Bus {b.id}</Tooltip>
          </CircleMarker>
        ))}
      </MapContainer>
      {!visible.length && <p className="map-note">No bus is sharing its location yet.</p>}
    </div>
  )
}
