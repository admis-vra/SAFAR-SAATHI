import { Link } from 'react-router-dom'
import { useLiveBuses, useRoutes } from '../live'
import BusMap from '../components/BusMap'
import Icon from '../components/Icon'
import { Logo } from '../components/ui'

const FEATURES = [
  ['pin', 'Your bus, by stop', 'Set your stop once. SafarSaathi tells you the bus, the gate and when it really leaves.'],
  ['seat', 'Live free seats', 'Door counters and the bus camera are combined, so the seat count stays right at the door and on the road.'],
  ['route', 'Zero-touch tracking', 'Trips start and end from the bus’s own GPS. Drivers don’t need to press anything.'],
  ['users', 'Fair boarding queue', 'Join the queue from your phone and get called when a seat is free. No rushing the door.'],
  ['bus', 'Missed the last bus?', 'Get the nearest bus on another route that still has seats, with walking time.'],
  ['shield', 'SOS & control room', 'One long press sends an SOS with the bus location. The transport office sees and acknowledges it live.'],
]

const STEPS = [
  ['bus', 'On the bus', 'GPS, IR door counters and the existing CCTV send counts only, never video, every 2 s.'],
  ['wifi', 'In the cloud', 'Geofences start trips, sensor fusion fixes seat counts, and the queue calls the next student.'],
  ['bell', 'On your phone', 'Your bus, seats, ETA and “your turn” alerts, live.'],
  ['shield', 'In the office', 'The whole fleet on one map: SOS, device health, queues and exam-day demand.'],
]

const AUDIENCES = [
  ['user', 'Students', ['Which bus and gate, when it leaves', 'Free seats before you walk out', 'Queue from your phone', 'Fallback if the last bus left']],
  ['bus', 'Drivers & conductors', ['Trips run themselves from GPS', 'Big buttons with Hindi labels', 'Queue: call, boarded, no-show', 'Hold-to-send SOS']],
  ['shield', 'Transport office', ['Live fleet map and seat counts', 'SOS and device-offline alerts', 'Queue manager per bus', 'Extra trips for exam shifts']],
]

export default function Landing() {
  const { buses, connected } = useLiveBuses()
  const routes = useRoutes()
  const list = Object.values(buses)
  const onRoad = list.filter((b) => b.status === 'departed').length
  const seats = list.reduce((n, b) => n + (b.seats_free ?? 0), 0)
  const online = list.filter((b) => b.device?.online).length

  return (
    <div className="landing">
      <header className="landing-nav">
        <a href="#top" className="landing-brand"><Logo size={34} /><span className="brand">SafarSaathi</span></a>
        <nav className="landing-links">
          <a href="#features">Features</a>
          <a href="#how">How it works</a>
          <a href="#for-you">For you</a>
        </nav>
        <Link to="/login" className="btn primary">Log in</Link>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <div className="hero-tag">GEHU Dehradun · SAARTHI’26</div>
          <h1>Which bus. Which seat. <span>What time.</span></h1>
          <p>SafarSaathi is the smart campus bus system for Graphic Era Hill University. Live free seats from on-board
            sensors, departures that track themselves, and a fair queue instead of a crowd at the door.</p>
          <div className="hero-cta">
            <Link to="/login" className="btn primary big">Log in <Icon name="arrow" size={18} /></Link>
            <a href="#how" className="btn ghost big">See how it works</a>
          </div>
          <div className="hero-privacy"><Icon name="shield" size={16} /> Counts only. No video leaves the bus.</div>
        </div>

        <div className="hero-live card">
          <div className="row-between">
            <div className="eyebrow">Live fleet right now</div>
            <span className={`live-dot dark ${connected ? 'on' : ''}`}>{connected ? 'Live' : 'Connecting…'}</span>
          </div>
          <div className="stat-strip">
            <div><b>{list.length}</b><span>buses</span></div>
            <div><b>{onRoad}</b><span>on the road</span></div>
            <div><b>{seats}</b><span>seats free</span></div>
            <div><b>{online}</b><span>devices online</span></div>
          </div>
          <BusMap buses={list} routes={routes} height={250} zoom={12} />
        </div>
      </section>

      <section className="landing-section" id="features">
        <h2 className="section-title">Everything a day-scholar needs</h2>
        <div className="feature-grid">
          {FEATURES.map(([icon, title, text]) => (
            <div key={title} className="feature card">
              <div className="feature-icon"><Icon name={icon} size={22} /></div>
              <h3>{title}</h3>
              <p>{text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-section alt" id="how">
        <h2 className="section-title">How it works</h2>
        <ol className="steps">
          {STEPS.map(([icon, title, text], i) => (
            <li key={title} className="step">
              <div className="step-num">{i + 1}</div>
              <div className="feature-icon"><Icon name={icon} size={22} /></div>
              <h3>{title}</h3>
              <p>{text}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="landing-section" id="for-you">
        <h2 className="section-title">Built for everyone on the bus</h2>
        <div className="audience-grid">
          {AUDIENCES.map(([icon, title, points]) => (
            <div key={title} className="audience card">
              <div className="row gap-s audience-head"><Icon name={icon} /><h3>{title}</h3></div>
              <ul>{points.map((p) => <li key={p}><Icon name="check" size={16} />{p}</li>)}</ul>
            </div>
          ))}
        </div>
      </section>

      <section className="landing-cta">
        <h2>Ready for your next ride?</h2>
        <p>Students log in with their student ID. Drivers and conductors use the ID from the transport office.</p>
        <Link to="/login" className="btn primary big">Log in to SafarSaathi</Link>
      </section>

      <footer className="landing-footer">
        <div className="row gap-s"><Logo size={22} /><b>SafarSaathi</b></div>
        <div className="tiny">Team: Siddhant (lead), Vidarbh, Vansh, Rahul · Graphic Era Hill University, Dehradun</div>
        <div className="tiny muted">Hackathon prototype · stop positions and demo buses are sample data</div>
      </footer>
    </div>
  )
}
