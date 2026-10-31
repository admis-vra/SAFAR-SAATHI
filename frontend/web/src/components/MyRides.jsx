import { useEffect, useState } from 'react'
import { api } from '../api'
import Icon from './Icon'

export default function MyRides({ onError }) {
  const [rides, setRides] = useState(null)
  useEffect(() => {
    api('/me/rides').then(setRides).catch(onError)
  }, [onError])

  return (
    <section className="card settings-card">
      <h3 className="settings-title"><Icon name="clock" /> My rides</h3>
      <p className="tiny muted">Rides where the conductor marked you boarded through the queue.</p>
      {rides === null ? <p className="muted small-text">Loading…</p> : rides.length === 0 ? (
        <p className="muted small-text">No rides yet. Join the queue next time and they’ll appear here.</p>
      ) : (
        <>
          <div className="tiny">{rides.length} ride{rides.length > 1 ? 's' : ''} recorded</div>
          <ul className="ride-list">
            {rides.slice(0, 10).map((r) => (
              <li key={`${r.service_date}-${r.bus_id}-${r.position}`}>
                <span><b>{new Date(r.service_date).toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })}</b>
                  {' '}· Bus {r.bus_id}</span>
                <span className="tiny muted">{r.route} · boarded {r.boarded_at ? new Date(r.boarded_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '–'}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
