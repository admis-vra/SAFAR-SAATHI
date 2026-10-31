import { useCallback, useEffect, useRef, useState } from 'react'
import { api, logout } from '../api'
import { useLiveBuses } from '../live'
import BusMap from '../components/BusMap'
import { DEMO_PATH } from '../geo'
import { Eta, SeatMeter, StatusPill, TopBar } from '../components/ui'

const GPS_SEND_MS = 5000
const SIM_STEP_MS = 3000

export default function Staff() {
  const [me, setMe] = useState(null)
  const [riders, setRiders] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const reloadTimer = useRef(null)

  const fail = useCallback((err) => {
    if (err.status === 401) logout()
    else setError(err.message)
  }, [])

  const busId = me?.bus_id
  const loadRiders = useCallback(() => {
    if (busId) api(`/buses/${busId}/passengers`).then(setRiders).catch(fail)
  }, [busId, fail])

  const { buses, connected } = useLiveBuses((msg) => {
    if (msg.type === 'alert' && msg.bus_id === busId) setNotice(msg.message)
    clearTimeout(reloadTimer.current)
    reloadTimer.current = setTimeout(loadRiders, 400)
  })

  useEffect(() => {
    api('/me').then(setMe).catch(fail)
    return () => clearTimeout(reloadTimer.current)
  }, [fail])

  useEffect(loadRiders, [loadRiders])

  const bus = (busId && buses[busId]) || me?.bus
  const isDriver = me?.role === 'driver'

  const setStatus = async (status) => {
    setError('')
    try {
      await api(`/buses/${busId}/status`, { method: 'POST', body: { status } })
    } catch (err) {
      fail(err)
    }
  }

  if (!me || !bus) {
    return <div className="app"><main className="content">{error ? <div className="alert bad">{error}</div> : <div className="card skeleton">Loading…</div>}</main></div>
  }

  return (
    <div className="app">
      <TopBar title={`${me.name} · ${isDriver ? 'Driver' : 'Conductor'}`} subtitle={`Bus ${bus.id}`} connected={connected} />

      <main className="content">
        {error && <div className="alert bad" role="alert">{error}</div>}
        {notice && (
          <div className="alert warn row-between" role="alert">
            <span>{notice}</span>
            <button className="btn ghost small" onClick={() => setNotice('')}>OK</button>
          </div>
        )}

        <section className="card bus-card">
          <div className="bus-card-head">
            <div>
              <div className="eyebrow">Your bus</div>
              <div className="bus-number">Bus {bus.id}</div>
              <div className="muted">{bus.gate} · Stops {bus.stops.join(', ')}</div>
            </div>
            <StatusPill status={bus.status} />
          </div>
          <div className="grid-2">
            <div className="stat"><div className="eyebrow">Departure</div><Eta eta={bus.eta} /></div>
            <div className="stat"><div className="eyebrow">Seats</div><SeatMeter bus={bus} /></div>
          </div>
        </section>

        <section className="card">
          <div className="eyebrow">Trip controls</div>
          <div className="big-buttons">
            {isDriver ? (
              <>
                <BigButton tone="good" active={bus.status === 'boarding'} onClick={() => setStatus('boarding')}
                  en="Start boarding" hi="बोर्डिंग शुरू" />
                <BigButton tone="info" active={bus.status === 'departed'} onClick={() => setStatus('departed')}
                  en="Start trip" hi="यात्रा शुरू करें" />
              </>
            ) : (
              <>
                <BigButton tone="good" active={bus.status === 'boarding'} onClick={() => setStatus('boarding')}
                  en="Open boarding" hi="बोर्डिंग खोलें" />
                <BigButton tone="bad" active={bus.status === 'full'} onClick={() => setStatus('full')}
                  en="Bus full" hi="बस भर गई" />
              </>
            )}
            <BigButton tone="neutral" active={bus.status === 'scheduled'} onClick={() => setStatus('scheduled')}
              en="Reset" hi="रीसेट" />
          </div>
        </section>

        {isDriver ? <GpsShare busId={bus.id} onError={fail} /> : <ManualCount bus={bus} onError={fail} />}

        <section className="card">
          <div className="eyebrow">Live map</div>
          <BusMap buses={Object.values(buses)} focusId={bus.id} height={220} />
        </section>

        <section className="card">
          <div className="row-between">
            <div className="eyebrow">Passengers booked ({riders.length})</div>
          </div>
          {riders.length === 0 ? (
            <p className="muted">No students assigned to this bus yet.</p>
          ) : (
            <ul className="riders">
              {riders.map((r) => (
                <li key={r.id}><span className="stop-chip">{r.stop}</span>{r.name}<span className="muted tiny"> · {r.id}</span></li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  )
}

function BigButton({ en, hi, tone, active, onClick }) {
  return (
    <button className={`big-btn ${tone} ${active ? 'active' : ''}`} onClick={onClick} aria-pressed={active}>
      <span className="big-btn-en">{en}</span>
      <span className="big-btn-hi" lang="hi">{hi}</span>
    </button>
  )
}

function GpsShare({ busId, onError }) {
  const [mode, setMode] = useState('off')
  const [last, setLast] = useState(null)
  const [gpsError, setGpsError] = useState('')
  const lastSent = useRef(0)
  const gpsAvailable = window.isSecureContext && 'geolocation' in navigator

  const send = useCallback(async (lat, lng) => {
    try {
      await api(`/buses/${busId}/location`, { method: 'POST', body: { lat, lng } })
      setLast(new Date())
    } catch (err) {
      onError(err)
    }
  }, [busId, onError])

  useEffect(() => {
    if (mode === 'gps') {
      const id = navigator.geolocation.watchPosition(
        (pos) => {
          if (Date.now() - lastSent.current < GPS_SEND_MS) return
          lastSent.current = Date.now()
          send(pos.coords.latitude, pos.coords.longitude)
        },
        (err) => { setGpsError(err.message); setMode('off') },
        { enableHighAccuracy: true, maximumAge: 5000 },
      )
      return () => navigator.geolocation.clearWatch(id)
    }
    if (mode === 'demo') {
      let i = 0
      const step = () => { send(...DEMO_PATH[i]); i = (i + 1) % DEMO_PATH.length }
      step()
      const id = setInterval(step, SIM_STEP_MS)
      return () => clearInterval(id)
    }
  }, [mode, send])

  return (
    <section className="card">
      <div className="row-between">
        <div>
          <div className="eyebrow">Live location · <span lang="hi">लोकेशन शेयर</span></div>
          <div className={mode === 'off' ? 'muted' : 'good-text'}>
            {mode === 'off' ? 'Not sharing' : mode === 'gps' ? 'Sharing phone GPS' : 'Sharing demo route'}
            {last && mode !== 'off' && <span className="muted tiny"> · last sent {last.toLocaleTimeString()}</span>}
          </div>
        </div>
      </div>
      <div className="row gap">
        {mode === 'off' ? (
          <>
            <button className="btn primary" disabled={!gpsAvailable} onClick={() => { setGpsError(''); setMode('gps') }}>
              Share GPS
            </button>
            <button className="btn ghost" onClick={() => setMode('demo')}>Simulate route (demo)</button>
          </>
        ) : (
          <button className="btn danger" onClick={() => setMode('off')}>Stop sharing</button>
        )}
      </div>
      {!gpsAvailable && (
        <p className="tiny muted">Phone GPS needs HTTPS or localhost. Use “Simulate route” for the demo.</p>
      )}
      {gpsError && <p className="tiny bad-text">GPS error: {gpsError}</p>}
    </section>
  )
}

function ManualCount({ bus, onError }) {
  const [count, setCount] = useState(bus.occupancy)
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const clamp = (n) => Math.max(0, Math.min(bus.capacity + 20, n))

  const submit = async () => {
    setBusy(true)
    try {
      await api(`/buses/${bus.id}/count`, { method: 'POST', body: { count } })
      setSent(true)
      setTimeout(() => setSent(false), 2000)
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="eyebrow">Passenger count · <span lang="hi">यात्री गिनती</span></div>
      <p className="tiny muted">Use this if the bus camera is down. It replaces the camera reading.</p>
      <div className="stepper">
        <button className="btn ghost step" onClick={() => setCount((c) => clamp(c - 1))} aria-label="One less">−</button>
        <input type="number" value={count} min={0} onChange={(e) => setCount(clamp(Number(e.target.value) || 0))}
          aria-label="Passengers on board" />
        <button className="btn ghost step" onClick={() => setCount((c) => clamp(c + 1))} aria-label="One more">+</button>
        <button className="btn primary" onClick={submit} disabled={busy}>{sent ? 'Sent ✓' : 'Update'}</button>
      </div>
    </section>
  )
}
