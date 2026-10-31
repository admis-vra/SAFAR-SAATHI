import { useCallback, useEffect, useRef, useState } from 'react'
import { api, logout } from '../api'
import { useLiveBuses, useRoutes } from '../live'
import BusMap from '../components/BusMap'
import { DEMO_PATH } from '../geo'
import { BottomNav, Eta, SeatMeter, StatusPill, TopBar } from '../components/ui'
import { notify, usePrefs } from '../prefs'
import Announcements from '../components/Announcements'
import { enqueue, flush, pending } from '../outbox'

const GPS_SEND_MS = 5000
const GPS_HEARTBEAT_MS = 15000
const SIM_STEP_MS = 3000
const SOS_HOLD_MS = 2000

export default function Staff() {
  const [me, setMe] = useState(null)
  const [riders, setRiders] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [queueTick, setQueueTick] = useState(0)
  const [annTick, setAnnTick] = useState(0)
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
    if (msg.type === 'announcement') setAnnTick((n) => n + 1)
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
    return <div className="app"><main className="content staff-content">{error ? <div className="alert bad">{error}</div> : <div className="card skeleton">Loading…</div>}</main></div>
  }

  return (
    <div className="app has-bottom-nav">
      <TopBar title={`${me.name} · ${isDriver ? 'Driver' : 'Conductor'}`} subtitle={`Bus ${bus.id}`} connected={connected} home="/staff" />

      <main className="content">
        {error && <div className="alert bad" role="alert">{error}</div>}
        <Announcements routeId={bus.route_id} tick={annTick} />
        {notice && (
          <div className="alert warn row-between" role="alert">
            <span>{notice}</span>
            <button className="btn ghost small" onClick={() => setNotice('')}>OK</button>
          </div>
        )}

        <div className="staff-grid">
          <div className="staff-col-controls">

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

        {isDriver && <GpsShare busId={bus.id} onError={fail} />}

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

        {!isDriver && <ManualCount bus={bus} onError={fail} />}

          </div>

          <div className="staff-col-ops">
            <section className="card desktop-map-card" id="live-map">
              <div className="row-between map-head-row">
                <div>
                  <div className="eyebrow">Live Route Map</div>
                  <div className="map-route-title">{routes.find((r) => r.id === bus.route_id)?.name ?? `Route ${bus.route_id}`}</div>
                </div>
                <div className="map-bus-badge">
                  <span className="live-pulse" />
                  <span>Bus {bus.id}</span>
                </div>
              </div>
              <BusMap buses={Object.values(buses)} routes={routes} highlightRoute={bus.route_id} focusId={bus.id}
                height={280} />
            </section>

            <section className="card passenger-manifest-card">
              <div className="row-between">
                <div>
                  <div className="eyebrow">Passenger manifest</div>
                  <div className="manifest-count">{riders.length} student{riders.length === 1 ? '' : 's'} booked</div>
                </div>
              </div>
              {riders.length === 0 ? (
                <p className="muted">No students assigned to this bus yet.</p>
              ) : (
                <div className="manifest-wrap">
                  <table className="manifest-table">
                    <thead>
                      <tr>
                        <th>Stop</th>
                        <th>Student Name</th>
                        <th>Student ID</th>
                      </tr>
                    </thead>
                    <tbody>
                      {riders.map((r) => (
                        <tr key={r.id}>
                          <td><span className="stop-chip">{r.stop}</span></td>
                          <td className="manifest-name">{r.name}</td>
                          <td className="muted tiny">{r.id}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
        </div>
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
  const [unsynced, setUnsynced] = useState(() => pending().length)
  const [marked, setMarked] = useState({})  // optimistic marks while offline

  const reload = useCallback(() => {
    api(`/buses/${busId}/queue`).then(setData).catch((err) => { if (err.status !== 0) onError(err) })
  }, [busId, onError])

  useEffect(reload, [reload, tick])

  // Offline mode: replay saved taps when the connection is back (and every 15 s, as phones don't always say).
  useEffect(() => {
    const sync = async () => {
      if (!pending().length) return
      const left = await flush()
      setUnsynced(left)
      if (!left) { setMarked({}); reload() }
    }
    window.addEventListener('online', sync)
    const id = setInterval(sync, 15000)
    sync()
    return () => { window.removeEventListener('online', sync); clearInterval(id) }
  }, [reload])

  const mark = async (id, outcome) => {
    const path = `/buses/${busId}/queue/${id}/${outcome}`
    setBusy(id)
    try {
      await api(path, { method: 'POST' })
      reload()
    } catch (err) {
      if (err.status === 0) {  // no network: keep the tap, show it as done, sync later
        enqueue(path)
        setUnsynced(pending().length)
        setMarked((m) => ({ ...m, [id]: outcome }))
      } else {
        onError(err)
      }
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
      {unsynced > 0 && (
        <div className="alert warn small-text" role="status">
          Offline: {unsynced} tap{unsynced > 1 ? 's' : ''} saved on this phone, will sync automatically.
        </div>
      )}
      {called.length === 0 ? (
        <p className="muted small-text">
          {data.queue.state !== 'open' ?
            `Queue ${data.queue.state === 'not_open' ? `opens at ${data.queue.opens_at}` : 'closed'}.` :
            data.queue.bus_status !== 'boarding' ? 'Nobody called yet. Open boarding to call the queue.' :
              waiting.length ? 'No free seat to call the next student yet.' : 'Nobody waiting.'}
        </p>
      ) : (
        <ul className="called-list">
          {called.filter((t) => !marked[t.id]).map((t) => (
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

const GPS_KEY = (busId) => `safarsaathi.gps.${busId}`

// Keep the phone screen on while sharing: browsers pause GPS for pages in the background.
function useWakeLock(active) {
  useEffect(() => {
    if (!active || !('wakeLock' in navigator)) return
    let lock = null
    let gone = false
    const grab = async () => {
      try {
        lock = await navigator.wakeLock.request('screen')
      } catch {
        lock = null
      }
    }
    const onVisible = () => { if (document.visibilityState === 'visible' && !gone) grab() }
    grab()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      gone = true
      document.removeEventListener('visibilitychange', onVisible)
      lock?.release().catch(() => {})
    }
  }, [active])
}

function GpsShare({ busId, onError }) {
  // Once a driver turns sharing on, it resumes by itself every time the app opens.
  const [mode, setMode] = useState(() => (localStorage.getItem(GPS_KEY(busId)) === 'gps' ? 'gps' : 'off'))
  const [last, setLast] = useState(null)
  const [fix, setFix] = useState(null)
  const [note, setNote] = useState('')
  const [gpsError, setGpsError] = useState('')
  const lastSent = useRef(0)
  const lastFix = useRef(null)
  const gpsAvailable = window.isSecureContext && 'geolocation' in navigator
  useWakeLock(mode !== 'off')

  const choose = (m) => {
    setGpsError('')
    setNote('')
    setMode(m)
    if (m === 'gps') localStorage.setItem(GPS_KEY(busId), 'gps')
    else localStorage.removeItem(GPS_KEY(busId))
  }

  const send = useCallback(async (body) => {
    lastSent.current = Date.now()
    try {
      const res = await api(`/buses/${busId}/location`, { method: 'POST', body })
      setNote(res.accepted ? '' : res.reason)
      if (res.accepted) setLast(new Date())
    } catch (err) {
      if (err.status === 0) setNote('No network. Will keep trying.')
      else onError(err)
    }
  }, [busId, onError])

  useEffect(() => {
    if (mode === 'gps') {
      if (!gpsAvailable) return
      const id = navigator.geolocation.watchPosition(
        (pos) => {
          const c = pos.coords
          lastFix.current = {
            lat: c.latitude, lng: c.longitude, accuracy_m: c.accuracy,
            speed_kmh: c.speed != null && c.speed >= 0 ? +(c.speed * 3.6).toFixed(1) : null,
          }
          setFix(lastFix.current)
          if (Date.now() - lastSent.current >= GPS_SEND_MS) send(lastFix.current)
        },
        (err) => {
          setGpsError(err.code === err.PERMISSION_DENIED
            ? 'Location permission is blocked. Allow it for this site in the browser settings.'
            : err.message)
          if (err.code === err.PERMISSION_DENIED) {
            localStorage.removeItem(GPS_KEY(busId))
            setMode('off')
          }
        },
        { enableHighAccuracy: true, maximumAge: 3000, timeout: 20000 },
      )
      // A parked bus gives no new fixes: re-send the last one so riders see it is still live.
      const beat = setInterval(() => {
        if (lastFix.current && Date.now() - lastSent.current >= GPS_HEARTBEAT_MS) send(lastFix.current)
      }, 5000)
      return () => { navigator.geolocation.clearWatch(id); clearInterval(beat) }
    }
    if (mode === 'demo') {
      let i = 0
      const step = () => { send({ lat: DEMO_PATH[i][0], lng: DEMO_PATH[i][1] }); i = (i + 1) % DEMO_PATH.length }
      step()
      const id = setInterval(step, SIM_STEP_MS)
      return () => clearInterval(id)
    }
  }, [mode, send, gpsAvailable, busId])

  return (
    <section className={`card gps-card ${mode !== 'off' ? 'on' : ''}`}>
      <div className="row-between">
        <div>
          <div className="eyebrow">Live location · <span lang="hi">लोकेशन शेयर</span></div>
          <div className={mode === 'off' ? 'muted' : 'good-text'}>
            {mode === 'off' ? 'Not sharing. Students can’t see your bus.' : mode === 'gps' ? 'Students can see your bus live' : 'Sharing demo route'}
          </div>
          {mode !== 'off' && (
            <div className="tiny muted">
              {last ? `Last sent ${last.toLocaleTimeString()}` : 'Waiting for GPS…'}
              {fix && mode === 'gps' && ` · ±${Math.round(fix.accuracy_m)} m`}
              {fix?.speed_kmh != null && mode === 'gps' && ` · ${Math.round(fix.speed_kmh)} km/h`}
            </div>
          )}
        </div>
        {mode !== 'off' && <span className="live-dot on">Live</span>}
      </div>
      <div className="row gap">
        {mode === 'off' ? (
          <>
            <button className="btn primary" disabled={!gpsAvailable} onClick={() => choose('gps')}>
              Share live location
            </button>
            <button className="btn ghost" onClick={() => choose('demo')}>Simulate route (demo)</button>
          </>
        ) : (
          <button className="btn danger" onClick={() => choose('off')}>Stop sharing</button>
        )}
      </div>
      {mode === 'gps' && <p className="tiny muted">Keep this screen open while driving. Phones pause location for websites in the background.</p>}
      {!gpsAvailable && (
        <p className="tiny muted">Phone GPS needs the app on HTTPS (or localhost). Use “Simulate route” for the demo.</p>
      )}
      {note && <p className="tiny warn-text">{note}</p>}
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
