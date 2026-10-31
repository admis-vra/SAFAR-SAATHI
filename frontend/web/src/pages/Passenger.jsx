import { useCallback, useEffect, useRef, useState } from 'react'
import { api, logout } from '../api'
import { supabase } from '../supabase'
import { useLiveBuses } from '../live'
import BusMap from '../components/BusMap'
import { Eta, SeatMeter, StatusPill, TopBar } from '../components/ui'

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
  const refreshTimer = useRef(null)

  const handleError = useCallback((err) => {
    if (err.status === 401) logout()
    else if (err.status === 409) setNeedsProfile(true)
    else setError(err.message)
  }, [])

  const loadBus = useCallback(() => {
    api('/me/bus').then((d) => { setMine(d); setError('') }).catch(handleError)
  }, [handleError])

  const { buses, connected } = useLiveBuses(() => {
    if (needsProfile) return
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

  if (needsProfile) {
    return (
      <div className="app">
        <TopBar title="Welcome" connected={connected} />
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
            <>
              <div className="eyebrow">Change my stop</div>
              <ProfileForm profile={profile} routes={routes} onSaved={onSaved} onError={handleError}
                onCancel={() => setEditing(false)} stopOnly />
            </>
          )}
        </section>
      </main>
    </div>
  )
}

function ProfileForm({ profile, routes, onSaved, onError, onCancel, stopOnly = false }) {
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
