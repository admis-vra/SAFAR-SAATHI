import { useEffect, useState } from 'react'
import { api } from '../api'
import { supabase } from '../supabase'
import Icon from '../components/Icon'

const EXAM_SLOTS = ['09:00', '11:00', '14:00']
// Matches bulk_import_passengers.py: a bulk-imported student's email is deterministic from their student ID.
const PASSENGER_EMAIL_DOMAIN = 'passenger.safarsaathi.local'
// Matches bulk_create_drivers.py: a bulk-created driver's email is deterministic from their login ID (driver1, ...).
const STAFF_EMAIL_DOMAIN = 'staff.safarsaathi.local'
const EMPTY = { email: '', password: '', name: '', student_id: '', route_id: '', stop: '', exam_slot: '' }

export default function Login() {
  const [tab, setTab] = useState('passenger')
  const [mode, setMode] = useState('login')
  const [routes, setRoutes] = useState([])
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)
  const [unconfirmedEmail, setUnconfirmedEmail] = useState('')
  const [resent, setResent] = useState(false)

  useEffect(() => {
    api('/routes').then(setRoutes).catch(() => {})
  }, [])

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value, ...(k === 'route_id' ? { stop: '' } : {}) }))
  const stops = routes.find((r) => r.id === Number(form.route_id))?.stops ?? []
  const registering = tab === 'passenger' && mode === 'register'

  const submit = async (e) => {
    e.preventDefault()
    setError('')
    setInfo('')
    setUnconfirmedEmail('')
    setResent(false)
    setBusy(true)
    try {
      if (registering) {
        const { data, error: err } = await supabase.auth.signUp({
          email: form.email.trim(),
          password: form.password,
          options: {
            data: {
              name: form.name.trim(),
              student_id: form.student_id.trim(),
              route_id: Number(form.route_id),
              stop: form.stop,
              exam_slot: form.exam_slot || null,
            },
          },
        })
        if (err) throw err
        if (!data.session) {
          setInfo('Account created. Check your email to confirm it, then log in.')
          setMode('login')
          setForm((f) => ({ ...EMPTY, email: f.email }))
        }
      } else if (tab === 'passenger') {
        // Bulk-imported students log in with their student ID; self-registered ones use their real email.
        const id = form.email.trim()
        const email = id.includes('@') ? id : `${id}@${PASSENGER_EMAIL_DOMAIN}`
        const { error: err } = await supabase.auth.signInWithPassword({ email, password: form.password })
        if (err) throw err
      } else {
        // Bulk-created staff log in with driverN/conductorN; staff set up by hand use their real email.
        const id = form.email.trim()
        const email = id.includes('@') ? id : `${id}@${STAFF_EMAIL_DOMAIN}`
        const { error: err } = await supabase.auth.signInWithPassword({ email, password: form.password })
        if (err) throw err
      }
    } catch (err) {
      if (/email not confirmed/i.test(err.message)) {
        setError('Your email is not confirmed yet. Check your inbox for the confirmation link, or resend it below.')
        setUnconfirmedEmail(form.email.trim())
      } else {
        setError(/fetch/i.test(err.message) ? 'Cannot reach the login server. Check your internet connection.' : err.message)
      }
    } finally {
      setBusy(false)
    }
  }

  const resendConfirmation = async () => {
    setBusy(true)
    setError('')
    try {
      const { error: err } = await supabase.auth.resend({ type: 'signup', email: unconfirmedEmail })
      if (err) throw err
      setResent(true)
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
    setInfo('')
    setUnconfirmedEmail('')
    setResent(false)
    setForm(EMPTY)
  }

  return (
    <main className="login-page">
      <div className="login-container">
        <section className="login-hero">
          <div className="campus-badge">Graphic Era Hill University · Dehradun</div>
          <div className="brand big">SafarSaathi</div>
          <p className="login-subtitle">Which bus, which seat, what time — for every GEHU day-scholar.</p>
          
          <div className="login-features">
            <div className="login-feature-item">
              <span className="login-feature-icon"><Icon name="seat" size={18} /></span>
              <div>
                <b>Live Seat Vacancy</b>
                <span>Edge vision &amp; IR counting update free seats before you head to the gate</span>
              </div>
            </div>
            <div className="login-feature-item">
              <span className="login-feature-icon"><Icon name="clock" size={18} /></span>
              <div>
                <b>Stop-Accurate Live ETA</b>
                <span>Bus GPS tracking gives arrival times for your exact pickup stop</span>
              </div>
            </div>
            <div className="login-feature-item">
              <span className="login-feature-icon"><Icon name="users" size={18} /></span>
              <div>
                <b>Smart Boarding Queue</b>
                <span>Digital queue tokens for fair, calm boarding with zero pushing</span>
              </div>
            </div>
            <div className="login-feature-item">
              <span className="login-feature-icon"><Icon name="bus" size={18} /></span>
              <div>
                <b>Exam Shift Surge Dispatch</b>
                <span>Extra campus buses scheduled automatically for 9 AM, 11 AM, and 2 PM shifts</span>
              </div>
            </div>
          </div>
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
            {tab === 'passenger' && !registering ? 'Student ID' : tab === 'staff' ? 'Login ID' : 'Email'}
            <input type={tab === 'staff' || (tab === 'passenger' && !registering) ? 'text' : 'email'} value={form.email}
              onChange={set('email')} required autoComplete={tab === 'staff' || (tab === 'passenger' && !registering) ? 'username' : 'email'}
              placeholder={tab === 'passenger' && !registering ? 'Your student ID, or your email if you registered yourself' :
                tab === 'staff' ? 'Your login ID (e.g. driver1), or your email' : 'Your college email'} />
          </label>

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
                  Full name
                  <input value={form.name} onChange={set('name')} required autoComplete="name" />
                </label>
                <label>
                  Student ID
                  <input value={form.student_id} onChange={set('student_id')} required minLength={3} />
                </label>
              </div>
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
          {info && <div className="alert good" role="status">{info}</div>}
          {unconfirmedEmail && !resent && (
            <button type="button" className="link" onClick={resendConfirmation} disabled={busy}>
              Resend confirmation email
            </button>
          )}
          {resent && <div className="alert good" role="status">Confirmation email sent again. Check your inbox.</div>}

          <button className="btn primary block" disabled={busy}>
            {busy ? 'Please wait…' : registering ? 'Create account' : 'Log in'}
          </button>

          {tab === 'passenger' && (
            <p className="center muted small-text">
              {registering ? 'Already registered? ' : 'New here? '}
              <button type="button" className="link"
                onClick={() => { setMode(registering ? 'login' : 'register'); setError(''); setInfo(''); setUnconfirmedEmail(''); setResent(false) }}>
                {registering ? 'Log in' : 'Create an account'}
              </button>
            </p>
          )}
          {tab === 'staff' && (
            <p className="center muted small-text">Staff accounts are issued by the transport office.</p>
          )}
        </form>
        </section>
      </div>
    </main>
  )
}
