import { useEffect, useState } from 'react'
import { api } from '../api'
import Icon from './Icon'

const STYLE = { delay: ['warn', 'clock', 'Delay'], cancellation: ['bad', 'alert', 'Cancelled'], info: ['neutral', 'bell', 'Notice'] }

// Control-room announcements for a route (and route-wide ones). tick: refetch when the server says something changed.
export default function Announcements({ routeId, tick, dismissable = true }) {
  const [items, setItems] = useState([])
  const [hidden, setHidden] = useState(() => new Set())

  useEffect(() => {
    const q = routeId ? `?route_id=${routeId}` : ''
    api(`/announcements${q}`).then(setItems).catch(() => {})
  }, [routeId, tick])

  const shown = items.filter((a) => !hidden.has(a.id))
  if (!shown.length) return null
  return shown.map((a) => {
    const [tone, icon, label] = STYLE[a.kind] ?? STYLE.info
    return (
      <div key={a.id} className={`alert ${tone} announcement`} role="status">
        <Icon name={icon} size={18} />
        <div className="announcement-text">
          <b>{label}{a.bus_id ? ` · Bus ${a.bus_id}` : ''}</b> {a.message}
          <div className="tiny muted">until {new Date(a.expires_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
        </div>
        {dismissable && (
          <button className="btn ghost small" onClick={() => setHidden((h) => new Set(h).add(a.id))} aria-label="Hide">×</button>
        )}
      </div>
    )
  })
}
