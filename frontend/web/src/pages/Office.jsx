import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, logout } from '../api'
import { useLiveBuses, useRoutes } from '../live'
import BusMap from '../components/BusMap'
import Icon from '../components/Icon'
import { AnnouncementPanel, IssuesPanel } from '../components/OfficePanels'
import { DemandBoard } from '../components/DailyPlans'
import { SeatMeter, StatusPill, TopBar } from '../components/ui'

const SLOTS = ['09:00', '11:00', '14:00']
const QUEUE_COLUMNS = [
  ['waiting', 'Waiting'], ['called', 'Called'], ['boarded', 'Boarded'], ['expired', 'Expired / no-show'],
]

export default function Office() {
  const routes = useRoutes()
  const [incidents, setIncidents] = useState([])
  const [selected, setSelected] = useState(null)
  const [queueTick, setQueueTick] = useState(0)
  const [annTick, setAnnTick] = useState(0)
  const [issueTick, setIssueTick] = useState(0)
  const [planTick, setPlanTick] = useState(0)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  const fail = useCallback((err) => {
    if (err.status === 401) logout()
    else setError(err.message)
  }, [])

  const loadIncidents = useCallback(() => {
    api('/incidents?open=true').then(setIncidents).catch(fail)
  }, [fail])

  const { buses, connected } = useLiveBuses((msg) => {
    if (msg.type === 'incident') loadIncidents()
    if (msg.type === 'queue' || msg.type === 'token_called') setQueueTick((t) => t + 1)
    if (msg.type === 'announcement') setAnnTick((t) => t + 1)
    if (msg.type === 'issue') setIssueTick((t) => t + 1)
    if (msg.type === 'demand') setPlanTick((t) => t + 1)
    if (msg.type === 'last_bus_left') {
      const route = routes.find((r) => r.id === msg.route_id)
      setNotice(`Last bus of ${route?.name ?? `route ${msg.route_id}`} (Bus ${msg.bus_id}) has left. ` +
        'Students still on that route are being shown fallback options.')
    }
  })

  useEffect(loadIncidents, [loadIncidents])

  const list = Object.values(buses).sort((a, b) => a.route_id - b.route_id || a.id - b.id)
  const sos = incidents.filter((i) => i.kind === 'sos')
  const selectedBus = selected != null ? buses[selected] : null

  const acknowledge = async (id) => {
    try {
      await api(`/incidents/${id}/ack`, { method: 'POST' })
      loadIncidents()
    } catch (err) {
      fail(err)
    }
  }

  return (
    <div className="app">
      <TopBar title="Control room" subtitle={`${list.length} buses`} connected={connected} home="/office" />
      <main className="content wide">
        {error && <div className="alert bad" role="alert">{error}</div>}
        <nav className="office-links">
          <Link to="/office/analytics" className="btn ghost small"><Icon name="chart" size={16} /> Analytics</Link>
          <Link to="/office/admin" className="btn ghost small"><Icon name="settings" size={16} /> Buses &amp; stops</Link>
        </nav>
        {sos.map((i) => (
          <div key={i.id} className="alert sos-banner" role="alert">
            <div>
              <b>SOS · Bus {i.bus_id}</b>{i.late && ' (reported late)'}
              <div className="small-text">{i.detail} · {new Date(i.opened_at).toLocaleTimeString()}
                {i.lat != null && ` · ${i.lat.toFixed(4)}, ${i.lng.toFixed(4)}`}</div>
            </div>
            <button className="btn danger" onClick={() => acknowledge(i.id)}>Acknowledge</button>
          </div>
        ))}
        {notice && (
          <div className="alert warn row-between" role="status">
            <span>{notice}</span>
            <button className="btn ghost small" onClick={() => setNotice('')}>OK</button>
          </div>
        )}

        <div className="office-grid">
          <section className="card office-map">
            <div className="row-between">
              <div className="eyebrow">Live fleet</div>
              <span className="tiny muted">Click a bus for its queue</span>
            </div>
            <BusMap buses={list} routes={routes} focusId={selected}
              highlightRoute={selectedBus?.route_id} height={420} zoom={12} onSelect={setSelected} />
          </section>

          <section className="card">
            <div className="eyebrow">Incidents ({incidents.length})</div>
            {incidents.length === 0 ? <p className="muted">All clear.</p> : (
              <ul className="incident-list">
                {incidents.map((i) => (
                  <li key={i.id}>
                    <span className={`pill ${i.kind === 'sos' ? 'bad' : 'neutral'}`}>
                      {i.kind === 'sos' ? 'SOS' : 'Device offline'}
                    </span>
                    <span>Bus {i.bus_id} · {new Date(i.opened_at).toLocaleTimeString()}</span>
                    <button className="btn ghost small" onClick={() => acknowledge(i.id)}>Ack</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>

        <DemandBoard tick={planTick} onError={fail} />

        <section className="bus-tiles">
          {list.map((b) => (
            <BusTile key={b.id} bus={b} route={routes.find((r) => r.id === b.route_id)}
              selected={b.id === selected} onClick={() => setSelected(b.id === selected ? null : b.id)} />
          ))}
        </section>

        {selectedBus && <QueueBoard bus={selectedBus} tick={queueTick} onError={fail} />}

        <div className="office-grid even">
          <AnnouncementPanel routes={routes} tick={annTick} onError={fail} />
          <IssuesPanel tick={issueTick} onError={fail} />
        </div>

        <Planner routes={routes} onError={fail} />
      </main>
    </div>
  )
}

function BusTile({ bus, route, selected, onClick }) {
  const trip = bus.trip ?? {}
  const where = trip.in_depot ? 'At campus' : trip.at_stop ? `At stop ${trip.at_stop}` : bus.location ? 'On the road' : 'No position'
  const f = bus.fusion ?? {}
  return (
    <button className={`card bus-tile ${selected ? 'selected' : ''} ${bus.sos ? 'sos' : ''}`} onClick={onClick}>
      <div className="row-between">
        <div className="bus-number small">Bus {bus.id}</div>
        <StatusPill status={bus.status} />
      </div>
      <div className="tiny muted">{route?.name ?? `Route ${bus.route_id}`} · {bus.slot ? `home ${bus.slot}` : `usual ${bus.eta.predicted}`}</div>
      <SeatMeter bus={bus} />
      <div className="tile-facts tiny">
        <span>{where}{bus.speed_kmh ? ` · ${Math.round(bus.speed_kmh)} km/h` : ''}</span>
        <span className={bus.device?.online ? 'good-text' : 'muted'}>
          Device {bus.device?.online ? 'online' : 'offline'}
        </span>
        {f.mode && f.mode !== 'none' && (
          <span title="IR door counter vs camera">
            Fusion {f.mode.toUpperCase()} · IR {f.ir_empty ?? '–'} / cam {f.camera_empty ?? '–'} empty
            {f.ir_offset ? ` · offset ${f.ir_offset}` : ''}
          </span>
        )}
      </div>
    </button>
  )
}

function QueueBoard({ bus, tick, onError }) {
  const [data, setData] = useState(null)
  useEffect(() => {
    api(`/buses/${bus.id}/queue`).then(setData).catch(onError)
  }, [bus.id, tick, onError])

  if (!data) return <section className="card skeleton">Loading queue…</section>
  const c = data.columns
  const expired = [...c.expired, ...c.no_show]
  return (
    <section className="card">
      <div className="row-between">
        <div className="eyebrow">Queue · Bus {bus.id} · {data.queue.state} (opens {data.queue.opens_at}, departs {data.queue.departs_at})</div>
        <span className="tiny muted">{data.seats_free} seats free · {c.cancelled.length} cancelled</span>
      </div>
      <div className="queue-columns">
        {QUEUE_COLUMNS.map(([key, label]) => {
          const items = key === 'expired' ? expired : c[key]
          return (
            <div key={key} className={`queue-col ${key}`}>
              <div className="queue-col-head">{label} <b>{items.length}</b></div>
              {items.length === 0 ? <div className="tiny muted">—</div> : items.map((t) => (
                <div key={t.id} className="queue-chip">
                  <b>#{t.position}</b> {t.name}
                  <span className="tiny muted"> {t.student_id}{t.status === 'no_show' ? ' · no-show' : ''}</span>
                </div>
              ))}
            </div>
          )
        })}
      </div>
    </section>
  )
}

function Planner({ routes, onError }) {
  const [slot, setSlot] = useState('11:00')
  const [plan, setPlan] = useState(null)
  useEffect(() => {
    api(`/planner?slot=${slot}`).then(setPlan).catch(onError)
  }, [slot, onError])

  const max = Math.max(1, ...(plan?.routes.map((r) => r.students) ?? [1]))
  return (
    <section className="card">
      <div className="row-between">
        <div className="eyebrow">Exam-shift planner</div>
        <div className="segmented">
          {SLOTS.map((s) => (
            <button key={s} className={s === slot ? 'active' : ''} onClick={() => setSlot(s)}>{s}</button>
          ))}
        </div>
      </div>
      {plan && (
        <div className="bars">
          {plan.routes.map((r) => (
            <div key={r.route_id} className="bar-row">
              <span className="bar-label">{routes.find((x) => x.id === r.route_id)?.name ?? r.route}</span>
              <div className="bar-track">
                <div className={`bar-value ${r.suggestion ? 'hot' : ''}`} style={{ width: `${(r.students / max) * 100}%` }} />
              </div>
              <span className="bar-num">{r.students}</span>
            </div>
          ))}
          {plan.routes.filter((r) => r.suggestion).map((r) => (
            <div key={r.route_id} className="alert warn small-text">{r.suggestion.text}</div>
          ))}
          <p className="tiny muted">Extra trip suggested at {plan.threshold}+ students. Demand includes sample data.</p>
        </div>
      )}
    </section>
  )
}
