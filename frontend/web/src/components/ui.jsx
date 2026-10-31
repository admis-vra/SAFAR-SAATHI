import { Link, NavLink, useLocation } from 'react-router-dom'
import { logout } from '../api'
import Icon from './Icon'

export function Logo({ size = 30 }) {
  return <img src="/favicon.svg" width={size} height={size} alt="" className="logo" />
}

export function TopBar({ title, subtitle, connected, home = '/' }) {
  const onSettings = useLocation().pathname === '/settings'
  return (
    <header className="topbar">
      <Link to={home} className="topbar-left">
        <Logo />
        <div>
          <div className="brand">SafarSaathi</div>
          <div className="topbar-sub">
            {title}
            {subtitle && <span className="muted"> · {subtitle}</span>}
          </div>
        </div>
      </Link>
      <div className="topbar-right">
        {connected !== undefined && (
          <span className={`live-dot ${connected ? 'on' : ''}`} title={connected ? 'Live' : 'Reconnecting…'}>
            {connected ? 'Live' : 'Offline'}
          </span>
        )}
        {!onSettings && (
          <Link to="/settings" className="icon-btn topbar-settings" aria-label="Settings" title="Settings">
            <Icon name="settings" />
          </Link>
        )}
        <button className="icon-btn" onClick={logout} aria-label="Log out" title="Log out">
          <Icon name="logout" />
        </button>
      </div>
    </header>
  )
}

// Phone-style bottom navigation; hidden on wide screens where the top bar is enough.
export function BottomNav({ home, mapTarget = 'live-map' }) {
  const scrollToMap = () => document.getElementById(mapTarget)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  return (
    <nav className="bottom-nav" aria-label="Main">
      <NavLink to={home} end><Icon name="home" /><span>Home</span></NavLink>
      <button type="button" onClick={scrollToMap}><Icon name="map" /><span>Map</span></button>
      <NavLink to="/settings"><Icon name="settings" /><span>Settings</span></NavLink>
    </nav>
  )
}

export function Toggle({ checked, onChange, label, hint, disabled }) {
  return (
    <label className={`toggle-row ${disabled ? 'disabled' : ''}`}>
      <span>
        <span className="toggle-label">{label}</span>
        {hint && <span className="tiny muted toggle-hint">{hint}</span>}
      </span>
      <input type="checkbox" role="switch" checked={checked} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle-track" aria-hidden="true"><span className="toggle-thumb" /></span>
    </label>
  )
}

const STATUS = {
  scheduled: { label: 'Scheduled', cls: 'neutral' },
  boarding: { label: 'Boarding', cls: 'good' },
  departed: { label: 'Departed', cls: 'info' },
  full: { label: 'Full', cls: 'bad' },
}

export function StatusPill({ status }) {
  const s = STATUS[status] ?? { label: status, cls: 'neutral' }
  return <span className={`pill ${s.cls}`}>{s.label}</span>
}

export function SeatMeter({ bus }) {
  const free = bus.seats_free
  const estimate = bus.seat_source === 'camera_partial'
  const pctFree = Math.round((free / bus.capacity) * 100)
  const tone = free === 0 ? 'bad' : pctFree < 25 ? 'warn' : 'good'
  const source = {
    fused: 'Door counter + camera',
    ir_count: 'Door counter',
    camera: 'Seat camera',
    camera_partial: `Seat camera (sees ${bus.seats_seen} of ${bus.capacity} seats)`,
    people_count: 'Head count',
    allocations: 'Bookings',
  }[bus.seat_source]

  return (
    <div className="seat-meter">
      <div className="seat-numbers">
        <span className={`seat-free ${tone}`}>
          {estimate && '~'}
          {free}
        </span>
        <span className="muted"> / {bus.capacity} seats free</span>
      </div>
      <div className="bar">
        <div className={`bar-fill ${tone}`} style={{ width: `${100 - pctFree}%` }} />
      </div>
      <div className="tiny muted">Source: {source}</div>
    </div>
  )
}

export function Eta({ eta }) {
  const late = eta.delay_min >= 1
  return (
    <div>
      <div className="eta-time">~{eta.predicted}</div>
      <div className="tiny muted">
        Timetable {eta.scheduled}
        {late && ` · about ${Math.round(eta.delay_min)} min late`}
        {eta.source === 'model' && ' · AI prediction'}
      </div>
    </div>
  )
}
