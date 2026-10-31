import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, clearSession } from '../api'
import { useLiveBuses } from '../live'
import BusMap from '../components/BusMap'
import { Eta, SeatMeter, StatusPill, TopBar } from '../components/ui'

function minutesUntil(hhmm) {
  const [h, m] = hhmm.split(':').map(Number)
  const t = new Date()
  t.setHours(h, m, 0, 0)
  return Math.round((t - Date.now()) / 60000)
}

export default function Passenger() {
  const navigate = useNavigate()
  const [profile, setProfile] = useState(null)
  const [mine, setMine] = useState(null)
  const [routes, setRoutes] = useState([])
  const [examTrip, setExamTrip] = useState(null)
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState('')
  const refreshTimer = useRef(null)

  const handleAuthError = useCallback((err) => {
    if (err.status === 401) {
      clearSession()
      navigate('/', { replace: true })
    } else {
      setError(err.message)
    }
  }, [navigate])

  const loadBus = useCallback(() => {
    api('/me/bus').then((d) => { setMine(d); setError('') }).catch(handleAuthError)
  }, [handleAuthError])

  const { buses, connected } = useLiveBuses(() => {
    clearTimeout(refreshTimer.current)
    refreshTimer.current = setTimeout(loadBus, 300)
  })

  useEffect(() => {
    api('/me').then((d) => setProfile(d.profile)).catch(handleAuthError)
    api('/routes').then(setRoutes).catch(() => {})
    loadBus()
    return () => clearTimeout(refreshTimer.current)
  }, [loadBus, handleAuthError])

  useEffect(() => {
    if (!profile?.exam_slot) return
    api(`/planner?slot=${profile.exam_slot}`)
      .then((p) => setExamTrip(p.routes.find((r) => r.route_id === profile.route_id)?.suggestion ?? null))
      .catch(() => {})
  }, [profile])

  const bus = mine?.bus
  const route = routes.find((r) => r.id === profile?.route_id)
  const mins = bus ? minutesUntil(bus.eta.predicted) : null

  return (
    <div className="app">
      <TopBar title={profile ? `Hi, ${profile.name.split(' ')[0]}` : 'Passenger'}
        subtitle={profile && `${route?.name ?? ''} · Stop ${profile.stop}`} connected={connected} />

      <main className="content">
        {error && <div className="alert bad">{error}</div>}
        {mine?.alert && <div className="alert warn big-alert" role="alert">{mine.alert}</div>}

        {!mine && !error && <div className="card skeleton">Finding your bus…</div>}

        {mine && !bus && <div className="card"><p>No bus is assigned to your stop right now.</p></div>}

        {bus && (
          <section className="card bus-card">
            <div className="bus-card-head">
              <div>
                <div className="eyebrow">Your bus today</div>
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

            <div className="grid-2">
              <div className="stat">
                <div className="eyebrow">Departure</div>
                <Eta eta={bus.eta} />
              </div>
              <div className="stat">
                <div className="eyebrow">Seats</div>
                <SeatMeter bus={bus} />
              </div>
            </div>
          </section>
        )}

        {examTrip && profile?.exam_slot && (
          <section className="card exam-card">
            <div className="eyebrow">Exam day</div>
            <p>
              Your <b>{profile.exam_slot}</b> shift has an extra trip leaving at <b>{examTrip.depart}</b>.
            </p>
          </section>
        )}

        <section className="card">
          <div className="eyebrow">Live map</div>
          <BusMap buses={Object.values(buses)} focusId={bus?.id} />
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
            <ChangeStop profile={profile} routes={routes} onCancel={() => setEditing(false)}
              onSaved={(result, updated) => { setMine(result); setProfile(updated); setEditing(false) }}
              onError={handleAuthError} />
          )}
        </section>
      </main>
    </div>
  )
}

function ChangeStop({ profile, routes, onCancel, onSaved, onError }) {
  const [form, setForm] = useState({
    route_id: String(profile.route_id), stop: profile.stop, exam_slot: profile.exam_slot ?? '',
  })
  const [busy, setBusy] = useState(false)
  const stops = routes.find((r) => r.id === Number(form.route_id))?.stops ?? []

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    const body = { route_id: Number(form.route_id), stop: form.stop, exam_slot: form.exam_slot || null }
    try {
      const result = await api('/me/stop', { method: 'PUT', body })
      onSaved(result, { ...profile, ...body })
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={save}>
      <div className="eyebrow">Change my stop</div>
      <div className="row">
        <label>
          Route
          <select value={form.route_id} onChange={(e) => setForm({ ...form, route_id: e.target.value, stop: '' })}>
            {routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </label>
        <label>
          Stop
          <select value={form.stop} onChange={(e) => setForm({ ...form, stop: e.target.value })} required>
            <option value="">Choose…</option>
            {stops.map((s) => <option key={s} value={s}>Stop {s}</option>)}
          </select>
        </label>
      </div>
      <label>
        Exam shift
        <select value={form.exam_slot} onChange={(e) => setForm({ ...form, exam_slot: e.target.value })}>
          <option value="">No exam</option>
          {['09:00', '11:00', '14:00'].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </label>
      <div className="row-end">
        <button type="button" className="btn ghost" onClick={onCancel}>Cancel</button>
        <button className="btn primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
      </div>
    </form>
  )
}
