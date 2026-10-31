import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, homeFor, saveSession } from '../api'

const EXAM_SLOTS = ['09:00', '11:00', '14:00']

export default function Login() {
  const navigate = useNavigate()
  const [tab, setTab] = useState('passenger')
  const [mode, setMode] = useState('login')
  const [routes, setRoutes] = useState([])
  const [form, setForm] = useState({ id: '', name: '', password: '', route_id: '', stop: '', exam_slot: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/routes').then(setRoutes).catch(() => {})
  }, [])

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value, ...(k === 'route_id' ? { stop: '' } : {}) }))
  const stops = routes.find((r) => r.id === Number(form.route_id))?.stops ?? []
  const registering = tab === 'passenger' && mode === 'register'

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const data = registering
        ? await api('/auth/register', {
            method: 'POST',
            body: {
              id: form.id.trim(),
              name: form.name.trim(),
              password: form.password,
              route_id: Number(form.route_id),
              stop: form.stop,
              exam_slot: form.exam_slot || null,
            },
          })
        : await api('/auth/login', {
            method: 'POST',
            body: { kind: tab, id: form.id.trim(), password: form.password },
          })
      saveSession(data.token, data.user)
      navigate(homeFor(data.user), { replace: true })
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const switchTab = (t) => {
    setTab(t)
    setMode('login')
    setError('')
    setForm((f) => ({ ...f, id: '', password: '' }))
  }

  return (
    <main className="login-page">
      <section className="login-hero">
        <div className="brand big">SafarSaathi</div>
        <p>Which bus, which seat, what time — for every GEHU day-scholar.</p>
      </section>

      <section className="card login-card">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'passenger'} className={tab === 'passenger' ? 'active' : ''}
            onClick={() => switchTab('passenger')}>
            Passenger
          </button>
          <button role="tab" aria-selected={tab === 'staff'} className={tab === 'staff' ? 'active' : ''}
            onClick={() => switchTab('staff')}>
            Driver / Conductor
          </button>
        </div>

        <form onSubmit={submit} className="form">
          <h2>{tab === 'staff' ? 'Staff login' : registering ? 'Create your account' : 'Student login'}</h2>

          <label>
            {tab === 'staff' ? 'Staff ID' : 'Student ID'}
            <input value={form.id} onChange={set('id')} required autoComplete="username"
              placeholder={tab === 'staff' ? 'e.g. D14' : 'Your university roll / ID'} />
          </label>

          {registering && (
            <label>
              Full name
              <input value={form.name} onChange={set('name')} required autoComplete="name" />
            </label>
          )}

          <label>
            Password
            <input type="password" value={form.password} onChange={set('password')} required
              minLength={registering ? 6 : undefined}
              autoComplete={registering ? 'new-password' : 'current-password'} />
          </label>

          {registering && (
            <>
              <div className="row">
                <label>
                  Route
                  <select value={form.route_id} onChange={set('route_id')} required>
                    <option value="">Choose…</option>
                    {routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </select>
                </label>
                <label>
                  Your stop
                  <select value={form.stop} onChange={set('stop')} required disabled={!stops.length}>
                    <option value="">Choose…</option>
                    {stops.map((s) => <option key={s} value={s}>Stop {s}</option>)}
                  </select>
                </label>
              </div>
              <label>
                Exam shift (optional)
                <select value={form.exam_slot} onChange={set('exam_slot')}>
                  <option value="">No exam</option>
                  {EXAM_SLOTS.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
            </>
          )}

          {error && <div className="alert bad" role="alert">{error}</div>}

          <button className="btn primary block" disabled={busy}>
            {busy ? 'Please wait…' : registering ? 'Create account' : 'Log in'}
          </button>

          {tab === 'passenger' && (
            <p className="center muted small-text">
              {registering ? 'Already registered? ' : 'New here? '}
              <button type="button" className="link" onClick={() => { setMode(registering ? 'login' : 'register'); setError('') }}>
                {registering ? 'Log in' : 'Create an account'}
              </button>
            </p>
          )}
          {tab === 'staff' && (
            <p className="center muted small-text">Staff accounts are issued by the transport office.</p>
          )}
        </form>
      </section>
    </main>
  )
}
