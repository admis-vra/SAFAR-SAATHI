import { useNavigate } from 'react-router-dom'
import { logout } from '../api'

export function TopBar({ title, subtitle, connected }) {
  const navigate = useNavigate()
  return (
    <header className="topbar">
      <div>
        <div className="brand">SafarSaathi</div>
        <div className="topbar-sub">
          {title}
          {subtitle && <span className="muted"> · {subtitle}</span>}
        </div>
      </div>
      <div className="topbar-right">
        <span className={`live-dot ${connected ? 'on' : ''}`} title={connected ? 'Live' : 'Reconnecting…'}>
          {connected ? 'Live' : 'Offline'}
        </span>
        <button
          className="btn ghost small"
          onClick={async () => {
            await logout()
            navigate('/', { replace: true })
          }}
        >
          Log out
        </button>
      </div>
    </header>
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
