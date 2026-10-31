import { useEffect, useState } from 'react'

const STALE_S = 60
const SOURCE = { phone: 'driver’s phone', device: 'bus GPS' }

// "Live · 4 s ago" under the map, so a frozen marker is never mistaken for a moving bus.
export default function LocationAge({ bus }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  if (!bus) return null
  if (!bus.location_at) return <div className="tiny muted">Bus {bus.id} location: not shared yet today.</div>
  const secs = Math.max(0, Math.round((now - new Date(bus.location_at).getTime()) / 1000))
  const stale = secs > STALE_S
  const age = secs < 60 ? `${secs} s ago` : secs < 3600 ? `${Math.round(secs / 60)} min ago` : 'over an hour ago'
  return (
    <div className={`tiny loc-age ${stale ? 'stale' : 'muted'}`}>
      {!stale && <span className="live-dot on">Live</span>}
      Bus {bus.id} · {stale ? 'last seen' : 'updated'} {age} · {SOURCE[bus.location_source] ?? 'GPS'}
    </div>
  )
}
