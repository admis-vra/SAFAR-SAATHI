import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { notify } from '../prefs'

const canLocate = typeof window !== 'undefined' && window.isSecureContext && 'geolocation' in navigator

// alert: opened because the last bus just left (live), not just a check when the app opens
export default function FallbackModal({ onClose, onError, alert = false }) {
  const [data, setData] = useState(null)
  const [here, setHere] = useState(null)
  const [locating, setLocating] = useState(false)
  const [locError, setLocError] = useState('')
  const alerted = useRef(false)

  useEffect(() => {
    const q = here ? `?lat=${here.lat}&lng=${here.lng}` : ''
    api(`/me/fallback${q}`).then((d) => {
      if (alert && d.needed && !alerted.current) {
        alerted.current = true
        notify('Your last bus has left', d.options.length ? `${d.options.length} other buses can still take you.`
          : 'No other bus nearby has seats. Contact the transport office.')
      }
      setData(d)
    }).catch(onError)
  }, [here, onError, alert])

  // Location is asked for only when the student taps the button, used for this answer and never stored.
  const locate = () => {
    setLocating(true)
    setLocError('')
    navigator.geolocation.getCurrentPosition(
      (p) => { setHere({ lat: p.coords.latitude, lng: p.coords.longitude }); setLocating(false) },
      (e) => { setLocError(e.message); setLocating(false) },
      { enableHighAccuracy: true, timeout: 10000 },
    )
  }

  if (!data?.needed) return null
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="fallback-title">
      <div className="modal card">
        <h2 id="fallback-title">Your last bus has left</h2>
        <p className="muted">Bus {data.last_bus.bus_id} was the last one on your route today.
          {data.options.length > 0 && ' These buses on other routes can still take you:'}</p>

        {data.options.map((o) => (
          <div key={o.bus_id} className="fallback-option">
            <div className="row-between">
              <b>Bus {o.bus_id} · {o.route}</b>
              <span className="pill good">{o.seats_free} seats</span>
            </div>
            <div className="small-text">
              Walk {o.walk_m >= 1000 ? `${(o.walk_m / 1000).toFixed(1)} km` : `${o.walk_m} m`} (~{o.walk_min} min)
              to stop {o.stop} · departs ~{o.departs} from {o.gate}
            </div>
          </div>
        ))}
        {data.message && <div className="alert warn">{data.message}</div>}

        <div className="row gap">
          {canLocate && (
            <button className="btn ghost" onClick={locate} disabled={locating}>
              {locating ? 'Locating…' : here ? 'Update my location' : 'Use my current location'}
            </button>
          )}
          <button className="btn primary" onClick={onClose}>OK</button>
        </div>
        {locError && <p className="tiny bad-text">Location unavailable: {locError}</p>}
        <p className="tiny muted">
          {here ? 'Options from where you are now (location not saved).' : 'Options from your home or usual stop.'}
        </p>
      </div>
    </div>
  )
}
