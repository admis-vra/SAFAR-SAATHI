import { useCallback, useEffect, useState } from 'react'
import { api, homeFor, logout } from '../api'
import { roleOf, supabase } from '../supabase'
import { useRoutes } from '../live'
import Icon from '../components/Icon'
import IssueReport from '../components/IssueReport'
import MyRides from '../components/MyRides'
import { BottomNav, Toggle, TopBar } from '../components/ui'
import { ProfileForm } from './Passenger'
import { useStops } from '../plans'
import { enableNotifications, notificationsSupported, setPref, usePrefs } from '../prefs'

const ROLE_LABEL = { passenger: 'Student', driver: 'Driver', conductor: 'Conductor', dispatcher: 'Control room' }
const canLocate = window.isSecureContext && 'geolocation' in navigator

export default function Settings({ session }) {
  const role = roleOf(session)
  const home = homeFor(session)
  const prefs = usePrefs()
  const routes = useRoutes()
  const stops = useStops()
  const [me, setMe] = useState(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState('')

  const fail = useCallback((err) => {
    if (err.status === 401) logout()
    else setError(err.message)
  }, [])

  useEffect(() => {
    api('/me').then(setMe).catch(fail)
  }, [fail])

  const flash = (msg) => {
    setSaved(msg)
    setTimeout(() => setSaved(''), 2500)
  }

  return (
    <div className="app has-bottom-nav">
      <TopBar title="Settings" subtitle={ROLE_LABEL[role]} home={home} />
      <main className="content settings-content">
        {error && <div className="alert bad" role="alert">{error}</div>}
        {saved && <div className="alert good" role="status"><Icon name="check" size={16} /> {saved}</div>}

        <div className="settings-grid">
          <div className="settings-col">
            <section className="card settings-card">
          <h3 className="settings-title"><Icon name="user" /> Account</h3>
          <dl className="facts">
            <dt>Name</dt><dd>{me?.profile?.name ?? me?.name ?? '…'}</dd>
            <dt>Role</dt><dd>{ROLE_LABEL[role]}</dd>
            {me?.profile?.student_id && <><dt>Student ID</dt><dd>{me.profile.student_id}</dd></>}
            {me?.bus && <><dt>Bus today</dt><dd>Bus {me.bus.id} · {me.bus.route_name ?? `stops ${me.bus.stops.join(', ')}`}</dd></>}
            <dt>Sign-in</dt><dd className="break">{session.user.email}</dd>
          </dl>
        </section>

        {role === 'passenger' && me?.profile && (
          <>
            <section className="card settings-card">
              <h3 className="settings-title"><Icon name="pin" /> My trip</h3>
              <p className="tiny muted">Your stop decides which buses we show you, morning and evening.</p>
              <ProfileForm profile={me.profile} routes={routes} stops={stops} onError={fail} stopOnly
                onSaved={(_, p) => { setMe((m) => ({ ...m, profile: { ...m.profile, ...p } })); flash('Trip saved') }} />
            </section>
            <HomeLocation hasHome={me.profile.has_home} onError={fail}
              onChange={(has) => { setMe((m) => ({ ...m, profile: { ...m.profile, has_home: has } })); flash(has ? 'Home saved' : 'Home removed') }} />
          </>
        )}

          </div>

          <div className="settings-col">
            <section className="card settings-card">
              <h3 className="settings-title"><Icon name="sun" /> Appearance</h3>
          <div className="segmented wide" role="radiogroup" aria-label="Theme">
            {[['light', 'Light', 'sun'], ['dark', 'Dark', 'moon'], ['system', 'System', 'settings']].map(([v, l, i]) => (
              <button key={v} role="radio" aria-checked={prefs.theme === v} className={prefs.theme === v ? 'active' : ''}
                onClick={() => setPref('theme', v)}>
                <Icon name={i} size={16} /> {l}
              </button>
            ))}
          </div>
          <Toggle checked={prefs.largeText} onChange={(v) => setPref('largeText', v)}
            label="Larger text" hint="Easier to read on a moving bus or in sunlight" />
        </section>

        <section className="card settings-card">
          <h3 className="settings-title"><Icon name="bell" /> Alerts</h3>
          <Toggle checked={prefs.notifications} disabled={!notificationsSupported}
            onChange={async (v) => {
              if (!v) return setPref('notifications', false)
              if (!(await enableNotifications())) setError('Notifications are blocked. Allow them in your browser settings.')
            }}
            label="Phone notifications"
            hint={!notificationsSupported ? 'Needs HTTPS (or localhost). Not available on this connection.' :
              role === 'passenger' ? '“Your turn to board” and “last bus has left”' : 'Queue and SOS updates'} />
          <Toggle checked={prefs.sounds} onChange={(v) => setPref('sounds', v)}
            label="Alert sound" hint="A short beep with each alert" />
        </section>

        {role === 'passenger' && me?.profile && <MyRides onError={fail} />}

        {role !== 'dispatcher' && <IssueReport onError={fail} onDone={() => flash('Report sent to the transport office')} />}

        <ChangePassword onDone={() => flash('Password changed')} onError={fail} />

        <section className="card settings-card">
          <h3 className="settings-title"><Icon name="shield" /> Privacy</h3>
          <ul className="privacy-list tiny">
            <li>Buses send <b>counts only</b>. No camera video or photos ever leave the bus.</li>
            {role === 'passenger' && <li>Your current location is used only when you tap “use my location”, and is never stored.</li>}
            {role === 'passenger' && <li>Your home location is optional, rounded to about 100 m, and you can delete it any time.</li>}
            {(role === 'driver' || role === 'conductor') && <li>If you turn on “Share live location”, your phone’s position is shown to students as the bus’s position, only while sharing is on. Stop sharing any time from the driver screen.</li>}
          </ul>
        </section>

            <button className="btn ghost block danger-text" onClick={logout}>
              <Icon name="logout" size={18} /> Log out
            </button>
          </div>
        </div>
      </main>
      {role !== 'dispatcher' && <BottomNav home={home} />}
    </div>
  )
}

function HomeLocation({ hasHome, onChange, onError }) {
  const [busy, setBusy] = useState(false)
  const [locError, setLocError] = useState('')

  const saveHere = () => {
    setBusy(true)
    setLocError('')
    navigator.geolocation.getCurrentPosition(async (p) => {
      try {
        await api('/me/home', { method: 'PUT', body: { lat: p.coords.latitude, lng: p.coords.longitude } })
        onChange(true)
      } catch (err) {
        onError(err)
      } finally {
        setBusy(false)
      }
    }, (e) => { setLocError(e.message); setBusy(false) }, { enableHighAccuracy: true, timeout: 10000 })
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api('/me/home', { method: 'DELETE' })
      onChange(false)
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card settings-card">
      <h3 className="settings-title"><Icon name="home" /> Home location <span className="pill neutral">optional</span></h3>
      <p className="tiny muted">If your last bus leaves, SafarSaathi looks for other buses near your home. Saved roughly
        (about 100 m), never shown to anyone.</p>
      <div className="row-between">
        <span className={hasHome ? 'good-text' : 'muted'}>{hasHome ? 'Home saved' : 'Not set'}</span>
        <span className="row gap-s">
          {canLocate && (
            <button className="btn ghost small" disabled={busy} onClick={saveHere}>
              {hasHome ? 'Update to here' : 'Set to where I am'}
            </button>
          )}
          {hasHome && <button className="btn ghost small" disabled={busy} onClick={remove}>Remove</button>}
        </span>
      </div>
      {!canLocate && <p className="tiny muted">Location needs HTTPS (or localhost) on this device.</p>}
      {locError && <p className="tiny bad-text">Location unavailable: {locError}</p>}
    </section>
  )
}

function ChangePassword({ onDone, onError }) {
  const [pw, setPw] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const submit = async (e) => {
    e.preventDefault()
    setMsg('')
    if (pw.length < 8) return setMsg('Use at least 8 characters.')
    if (pw !== again) return setMsg('The two passwords don’t match.')
    setBusy(true)
    const { error } = await supabase.auth.updateUser({ password: pw })
    setBusy(false)
    if (error) return error.status === 401 ? onError(error) : setMsg(error.message)
    setPw('')
    setAgain('')
    onDone()
  }

  return (
    <section className="card settings-card">
      <h3 className="settings-title"><Icon name="lock" /> Change password</h3>
      <p className="tiny muted">If you were given a password by the college, change it to one only you know.</p>
      <form className="form" onSubmit={submit}>
        <label>New password
          <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" minLength={8} required />
        </label>
        <label>Repeat new password
          <input type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" required />
        </label>
        {msg && <div className="alert warn small-text">{msg}</div>}
        <button className="btn primary" disabled={busy}>{busy ? 'Saving…' : 'Change password'}</button>
      </form>
    </section>
  )
}
