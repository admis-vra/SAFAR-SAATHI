import { useCallback, useEffect, useRef, useState } from 'react'
import { api, logout } from '../api'
import { supabase } from '../supabase'
import { useLiveBuses } from '../live'
import BusMap from '../components/BusMap'
import Announcements from '../components/Announcements'
import FallbackModal from '../components/FallbackModal'
import { notify } from '../prefs'
import QueueCard from '../components/QueueCard'
import { BottomNav, Eta, SeatMeter, StatusPill, TopBar } from '../components/ui'

const EXAM_SLOTS = ['09:00', '11:00', '14:00']

function minutesUntil(hhmm) {
  const [h, m] = hhmm.split(':').map(Number)
  const t = new Date()
  t.setHours(h, m, 0, 0)
  return Math.round((t - Date.now()) / 60000)
}

async function saveProfile(profile) {
  const result = await api('/me/profile', { method: 'PUT', body: profile })
  // Keep a copy in Supabase so the profile survives backend restarts.
  await supabase.auth.updateUser({ data: profile })
  return result
}

export default function Passenger() {
  const [profile, setProfile] = useState(null)
  const [needsProfile, setNeedsProfile] = useState(false)
  const [mine, setMine] = useState(null)
  const [routes, setRoutes] = useState([])
  const [examTrip, setExamTrip] = useState(null)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState('')
  const [queueTick, setQueueTick] = useState(0)
  const [fallbackCheck, setFallbackCheck] = useState(0)  // >0: (re)check whether the last bus has left
  const [annTick, setAnnTick] = useState(0)
  const [forecast, setForecast] = useState(null)
  const refreshTimer = useRef(null)
  const nearAlerted = useRef(null)

  const handleError = useCallback((err) => {
    if (err.status === 401) logout()
    else if (err.status === 409) setNeedsProfile(true)
    else setError(err.message)
  }, [])

  const loadBus = useCallback(() => {
    api('/me/bus').then((d) => { setMine(d); setError('') }).catch(handleError)
  }, [handleError])

  const { buses, connected } = useLiveBuses((msg) => {
    if (needsProfile) return
    if (msg.type === 'queue' || msg.type === 'token_called') setQueueTick((t) => t + 1)
    if (msg.type === 'last_bus_left' && msg.route_id === profile?.route_id) setFallbackCheck((n) => n + 1)
    if (msg.type === 'announcement') {
      setAnnTick((n) => n + 1)
      const a = msg.data
      if (a && (a.route_id == null || a.route_id === profile?.route_id)) notify('SafarSaathi notice', a.message)
    }
    clearTimeout(refreshTimer.current)
    refreshTimer.current = setTimeout(loadBus, 300)
  })

  useEffect(() => {
    api('/routes').then(setRoutes).catch(() => {})
    api('/me')
      .then((d) => {
        if (d.profile) {
          setProfile(d.profile)
          loadBus()
          setFallbackCheck(1)  // opening the app after the last bus left should show options too
        } else {
          setNeedsProfile(true)
        }
      })
      .catch(handleError)
    return () => clearTimeout(refreshTimer.current)
  }, [loadBus, handleError])

  useEffect(() => {
    if (!profile?.exam_slot) return
    api(`/planner?slot=${profile.exam_slot}`)
      .then((p) => setExamTrip(p.routes.find((r) => r.route_id === profile.route_id)?.suggestion ?? null))
      .catch(() => {})
  }, [profile])

  const onSaved = (result, saved) => {
    setMine(result)
    setProfile(saved)
    setNeedsProfile(false)
    setEditing(false)
  }

  const bus = mine?.bus
  const route = routes.find((r) => r.id === profile?.route_id)
  const mins = bus ? minutesUntil(bus.eta.predicted) : null
  const stopEta = mine?.my_stop_eta
  const busId = bus?.id

  useEffect(() => {
    if (!busId) return
    api(`/buses/${busId}/forecast`).then(setForecast).catch(() => {})
  }, [busId])

  // One "5 minutes from your stop" alert per trip, only from live GPS.
  useEffect(() => {
    if (!stopEta || stopEta.basis !== 'live' || stopEta.passed || stopEta.minutes > 5) return
    const key = `${busId}-${new Date().toDateString()}`
    if (nearAlerted.current === key) return
    nearAlerted.current = key
    notify(`Bus ${busId} is ${stopEta.minutes || 'about 1'} min from stop ${profile?.stop}`,
      mine?.aboard ? 'Get ready to get off.' : 'Head to your stop now.')
  }, [stopEta, busId, mine?.aboard, profile?.stop])

  if (needsProfile) {
    return (
      <div className="app">
        <TopBar title="Welcome" connected={connected} home="/passenger" />
        <main className="content">
          <section className="card">
            <h2>Finish your profile</h2>
            <p className="muted">Tell us your stop so we can assign your bus.</p>
            <ProfileForm routes={routes} onSaved={onSaved} onError={handleError} />
          </section>
        </main>
      </div>
    )
  }

  return (
    <div className="app has-bottom-nav">
      <TopBar title={profile ? `Hi, ${profile.name.split(' ')[0]}` : 'Passenger'}
        subtitle={profile && `${route?.name ?? ''} · Stop ${profile.stop}`} connected={connected} home="/passenger" />

      <main className="content">
        {error && <div className="alert bad">{error}</div>}
        <Announcements routeId={profile?.route_id} tick={annTick} />
        {mine?.alert && <div className="alert warn big-alert" role="alert">{mine.alert}</div>}

        {!mine && !error && <div className="card skeleton">Finding your bus…</div>}

        {mine && !bus && <div className="card"><p>No bus is assigned to your stop right now.</p></div>}

        {bus && (
          <section className="card bus-card">
            <div className="bus-card-head">
              <div>
                <div className="eyebrow">{mine?.aboard ? 'You are on' : 'Your bus today'}</div>
                <div className="bus-number">Bus {bus.id}</div>
                <div className="muted">{bus.gate} · Stops {bus.stops.join(', ')}</div>
              </div>
              <StatusPill status={bus.status} />
            </div>

            {mins !== null && mins >= 0 && mins <= 60 && bus.status !== 'departed' && (
              <div className={`countdown ${mins <= 10 ? 'soon' : ''}`}>
                {mins === 0 ? 'Leaving now' : `Leaves in ${mins} min`}
              </div>
            )}

            {stopEta && (
              <div className={`stop-eta ${stopEta.basis === 'live' ? 'live' : ''}`}>
                <div className="eyebrow">At your stop ({profile?.stop})</div>
                {stopEta.passed ? <div className="muted">Bus has passed your stop</div> : stopEta.basis === 'waiting' ? (
                  <div>
                    <b>Still at campus</b>
                    <span className="muted"> · {stopEta.overdue_min} min past its time</span>
                    <div className="tiny muted">You’ll get a time here once it leaves.</div>
                  </div>
                ) : (
                  <div>
                    <b className="stop-eta-time">~{stopEta.at}</b>
                    <span className="muted"> · {stopEta.minutes === 0 ? 'arriving now' : `in ${stopEta.minutes} min`}
                      {' · '}{(stopEta.distance_m / 1000).toFixed(1)} km</span>
                    <div className="tiny muted">{stopEta.basis === 'live' ? 'Live from the bus GPS' : 'Estimated from the timetable'}</div>
                  </div>
                )}
              </div>
            )}

            <div className="grid-2">
              <div className="stat">
                <div className="eyebrow">Departure</div>
                <Eta eta={bus.eta} />
              </div>
              <div className="stat">
                <div className="eyebrow">Seats</div>
                <SeatMeter bus={bus} />
                {forecast?.enough_data && (
                  <div className="tiny forecast-hint" title={`From ${forecast.trips} past trips`}>
                    {forecast.label} · {forecast.trips} past trips
                  </div>
                )}
              </div>
            </div>
          </section>
        )}

        {fallbackCheck > 0 && (
          <FallbackModal key={fallbackCheck} alert={fallbackCheck > 1} onClose={() => setFallbackCheck(0)} onError={handleError} />
        )}

        {bus && <QueueCard key={bus.id} tick={queueTick} onError={handleError} />}

        {examTrip && profile?.exam_slot && (
          <section className="card exam-card">
            <div className="eyebrow">Exam day</div>
            <p>
              Your <b>{profile.exam_slot}</b> shift has an extra trip leaving at <b>{examTrip.depart}</b>.
            </p>
          </section>
        )}

        <section className="card" id="live-map">
          <div className="eyebrow">Live map</div>
          <BusMap buses={Object.values(buses)} routes={routes} highlightRoute={profile?.route_id} focusId={bus?.id} />
        </section>

        <section className="card">
          {!editing ? (
            <div className="row-between">
              <div>
                <div className="eyebrow">My stop</div>
                <div>{route?.name} · Stop {profile?.stop}{profile?.exam_slot && ` · Exam ${profile.exam_slot}`}</div>
              </div>
              <button className="btn ghost small" onClick={() => setEditing(true)}>Change</button>
            </div>
          ) : (
            <>
              <div className="eyebrow">Change my stop</div>
              <ProfileForm profile={profile} routes={routes} onSaved={onSaved} onError={handleError}
                onCancel={() => setEditing(false)} stopOnly />
            </>
          )}
        </section>
      </main>
      <BottomNav home="/passenger" />
    </div>
  )
}

export function ProfileForm({ profile, routes, onSaved, onError, onCancel, stopOnly = false }) {
  const [form, setForm] = useState({
    name: profile?.name ?? '',
    student_id: profile?.student_id ?? '',
    route_id: profile ? String(profile.route_id) : '',
    stop: profile?.stop ?? '',
    exam_slot: profile?.exam_slot ?? '',
  })
  const [busy, setBusy] = useState(false)
  const stops = routes.find((r) => r.id === Number(form.route_id))?.stops ?? []
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value, ...(k === 'route_id' ? { stop: '' } : {}) }))

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    const body = {
      name: form.name.trim(),
      student_id: form.student_id.trim(),
      route_id: Number(form.route_id),
      stop: form.stop,
      exam_slot: form.exam_slot || null,
    }
    try {
      onSaved(await saveProfile(body), body)
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={save}>
      {!stopOnly && (
        <div className="row">
          <label>
            Full name
            <input value={form.name} onChange={set('name')} required autoComplete="name" />
          </label>
          <label>
            Student ID
            <input value={form.student_id} onChange={set('student_id')} required minLength={3} />
          </label>
        </div>
      )}
      <div className="row">
        <label>
          Route
          <select value={form.route_id} onChange={set('route_id')} required>
            <option value="">Choose…</option>
            {routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </label>
        <label>
          Stop
          <select value={form.stop} onChange={set('stop')} required disabled={!stops.length}>
            <option value="">Choose…</option>
            {stops.map((s) => <option key={s} value={s}>Stop {s}</option>)}
          </select>
        </label>
      </div>
      <label>
        Exam shift
        <select value={form.exam_slot} onChange={set('exam_slot')}>
          <option value="">No exam</option>
          {EXAM_SLOTS.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <div className="row-end">
        {onCancel && <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>}
        <button className="btn primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
    </form>
  )
}
