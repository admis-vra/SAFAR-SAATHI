import { useCallback, useEffect, useRef, useState } from 'react'
import { api, logout } from '../api'
import { useLiveBuses, useRoutes } from '../live'
import BusMap from '../components/BusMap'
import { DEMO_PATH } from '../geo'
import { BottomNav, Eta, SeatMeter, StatusPill, TopBar } from '../components/ui'
import { notify, usePrefs } from '../prefs'

const GPS_SEND_MS = 5000
const SIM_STEP_MS = 3000
const SOS_HOLD_MS = 2000

export default function Staff() {
  const [me, setMe] = useState(null)
  const [riders, setRiders] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [queueTick, setQueueTick] = useState(0)
  const reloadTimer = useRef(null)
  const routes = useRoutes()

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
    if (msg.type === 'incident' && msg.kind === 'sos' && msg.bus_id === busId && msg.open === false) {
      notify('SOS acknowledged', 'The control room has seen your SOS and will call you.')
    }
    if ((msg.type === 'queue' || msg.type === 'token_called') && msg.bus_id === busId) setQueueTick((t) => t + 1)
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
    <div className="app has-bottom-nav">
      <TopBar title={`${me.name} · ${isDriver ? 'Driver' : 'Conductor'}`} subtitle={`Bus ${bus.id}`} connected={connected} home="/staff" />

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

        <TripState bus={bus} />

        {(bus.status === 'scheduled' || bus.status === 'boarding') && (
          <section className="card">
            <BigButton tone="good" active={bus.status === 'boarding'} onClick={() => setStatus('boarding')}
              en={bus.status === 'boarding' ? 'Boarding open · calling the queue' : 'Open boarding'}
              hi="बोर्डिंग खोलें" />
          </section>
        )}

        <SosButton bus={bus} onError={fail} />

        <StaffQueue busId={bus.id} tick={queueTick} onError={fail} />

        <details className="card override">
          <summary className="eyebrow">Manual override · <span lang="hi">मैनुअल</span></summary>
          <p className="tiny muted">Trips start and end on their own from GPS. Use these only if that fails.</p>
          <div className="big-buttons">
            <BigButton tone="info" active={bus.status === 'departed'} onClick={() => setStatus('departed')}
              en="Start trip" hi="यात्रा शुरू करें" />
            <BigButton tone="bad" active={bus.status === 'full'} onClick={() => setStatus('full')}
              en="Bus full" hi="बस भर गई" />
            <BigButton tone="neutral" active={bus.status === 'scheduled'} onClick={() => setStatus('scheduled')}
              en="Reset" hi="रीसेट" />
          </div>
        </details>

        {isDriver ? <GpsShare busId={bus.id} onError={fail} /> : <ManualCount bus={bus} onError={fail} />}

        <section className="card" id="live-map">
          <div className="eyebrow">Live map</div>
          <BusMap buses={Object.values(buses)} routes={routes} highlightRoute={bus.route_id} focusId={bus.id}
            height={220} />
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
      <BottomNav home="/staff" />
    </div>
  )
}

function TripState({ bus }) {
  const { hindi } = usePrefs()
  const t = bus.trip ?? {}
  const [en, hi, tone] = t.in_depot ? ['At campus', 'कैंपस में', 'neutral']
    : t.at_stop ? [`At stop ${t.at_stop}`, `स्टॉप ${t.at_stop} पर`, 'good']
      : bus.status === 'departed' ? ['On the road', 'रास्ते में', 'info']
        : ['Waiting for GPS', 'जीपीएस का इंतज़ार', 'neutral']
  return (
    <section className={`card trip-state ${tone}`}>
      <div className="eyebrow">Trip · automatic</div>
      <div className="trip-state-main">{en} {hindi && <span className="muted" lang="hi">· {hi}</span>}</div>
      <div className="tiny muted">
        {t.id ? `Trip ${t.id} started automatically when the bus left campus.` :
          'The trip starts by itself when the bus leaves campus. Nothing to press.'}
        {bus.device?.online === false && ' · Bus device offline'}
      </div>
    </section>
  )
}

function SosButton({ bus, onError }) {
  const [progress, setProgress] = useState(0)
  const [sending, setSending] = useState(false)
  const timer = useRef(null)

  const stop = () => {
    cancelAnimationFrame(timer.current)
    setProgress(0)
  }

  // Hold for 2 s so a pocket tap or a bump on a bumpy road can't raise a false alarm.
  const start = (e) => {
    e.preventDefault()
    if (sending || bus.sos) return
    const t0 = performance.now()
    const tick = async (now) => {
      const p = Math.min((now - t0) / SOS_HOLD_MS, 1)
      setProgress(p)
      if (p < 1) {
        timer.current = requestAnimationFrame(tick)
        return
      }
      setSending(true)
      try {
        await api(`/buses/${bus.id}/sos`, { method: 'POST' })
      } catch (err) {
        onError(err)
      } finally {
        setSending(false)
        setProgress(0)
      }
    }
    timer.current = requestAnimationFrame(tick)
  }

  useEffect(() => () => cancelAnimationFrame(timer.current), [])

  if (bus.sos) {
    return (
      <section className="card sos-sent" role="status">
        <div className="sos-sent-title">SOS sent · <span lang="hi">मदद भेजी गई</span></div>
        <div className="small-text">The control room has your location and will call you. Stay with the bus.</div>
      </section>
    )
  }
  return (
    <button className="sos-btn" onPointerDown={start} onPointerUp={stop} onPointerLeave={stop} onPointerCancel={stop}
      onContextMenu={(e) => e.preventDefault()} disabled={sending} aria-label="Hold for 2 seconds to send SOS">
      <span className="sos-fill" style={{ transform: `scaleX(${progress})` }} />
      <span className="sos-label">{sending ? 'Sending…' : 'SOS'}</span>
      <span className="sos-hint">Hold 2 seconds · <span lang="hi">2 सेकंड दबाए रखें</span></span>
    </button>
  )
}

function StaffQueue({ busId, tick, onError }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(null)
  useEffect(() => {
    api(`/buses/${busId}/queue`).then(setData).catch(onError)
  }, [busId, tick, onError])

  const mark = async (id, outcome) => {
    setBusy(id)
    try {
      await api(`/buses/${busId}/queue/${id}/${outcome}`, { method: 'POST' })
      setData(await api(`/buses/${busId}/queue`))
    } catch (err) {
      onError(err)
    } finally {
      setBusy(null)
    }
  }

  if (!data) return null
  const { called, waiting, boarded } = data.columns
  return (
    <section className="card">
      <div className="row-between">
        <div className="eyebrow">Queue · <span lang="hi">कतार</span></div>
        <span className="tiny muted">{waiting.length} waiting · {boarded.length} boarded · {data.queue.state}</span>
      </div>
      {called.length === 0 ? (
        <p className="muted small-text">
          {data.queue.state !== 'open' ?
            `Queue ${data.queue.state === 'not_open' ? `opens at ${data.queue.opens_at}` : 'closed'}.` :
            data.queue.bus_status !== 'boarding' ? 'Nobody called yet. Open boarding to call the queue.' :
              waiting.length ? 'No free seat to call the next student yet.' : 'Nobody waiting.'}
        </p>
      ) : (
        <ul className="called-list">
          {called.map((t) => (
            <li key={t.id}>
              <span><b>#{t.position}</b> {t.name} <span className="tiny muted">{t.student_id}</span></span>
              <span className="row gap-s">
                <button className="btn primary small" disabled={busy === t.id} onClick={() => mark(t.id, 'boarded')}>
                  Boarded
                </button>
                <button className="btn ghost small" disabled={busy === t.id} onClick={() => mark(t.id, 'no_show')}>
                  No-show
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function BigButton({ en, hi, tone, active, onClick }) {
  const { hindi } = usePrefs()
  return (
    <button className={`big-btn ${tone} ${active ? 'active' : ''}`} onClick={onClick} aria-pressed={active}>
      <span className="big-btn-en">{en}</span>
      {hindi && <span className="big-btn-hi" lang="hi">{hi}</span>}
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
