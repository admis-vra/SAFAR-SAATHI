import { useCallback, useEffect, useState } from 'react'
import { api, logout } from '../api'
import { TopBar } from '../components/ui'

const RANGES = [[1, 'Today'], [7, '7 days'], [30, '30 days'], [90, '90 days']]
const pct = (v) => (v == null ? '–' : `${Math.round(v * 100)}%`)

export default function Analytics() {
  const [days, setDays] = useState(7)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  const fail = useCallback((err) => {
    if (err.status === 401) logout()
    else setError(err.message)
  }, [])

  useEffect(() => {
    api(`/analytics?days=${days}`).then(setData).catch(fail)
  }, [days, fail])

  const q = data?.queue
  const totalTrips = data?.routes.reduce((n, r) => n + r.trips, 0) ?? 0
  return (
    <div className="app">
      <TopBar title="Analytics" subtitle="Transport office" home="/office" />
      <main className="content wide">
        {error && <div className="alert bad" role="alert">{error}</div>}
        <div className="row-between">
          <h2 className="page-title">How the fleet did</h2>
          <div className="segmented">
            {RANGES.map(([v, l]) => (
              <button key={v} className={v === days ? 'active' : ''} onClick={() => setDays(v)}>{l}</button>
            ))}
          </div>
        </div>

        {!data ? <section className="card skeleton">Loading…</section> : (
          <>
            <section className="stat-grid">
              <Stat label="Trips run" value={totalTrips} />
              <Stat label="Queue tokens" value={q.joined} hint={`${q.boarded} boarded · ${q.no_show} no-show`} />
              <Stat label="SOS alerts" value={data.incidents.sos} hint={`${data.incidents.offline} device-offline`} />
              <Stat label="Open issues" value={data.open_issues} />
            </section>

            <section className="card">
              <div className="eyebrow">By route</div>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr><th>Route</th><th>Trips</th><th>On time</th><th>Avg late</th><th>Avg load</th><th>Near-empty</th></tr>
                  </thead>
                  <tbody>
                    {data.routes.map((r) => (
                      <tr key={r.route_id}>
                        <td>{r.name}</td>
                        <td>{r.trips}</td>
                        <td className={r.on_time_share != null && r.on_time_share < 0.8 ? 'bad-text' : ''}>{pct(r.on_time_share)}</td>
                        <td>{r.avg_late_min == null ? '–' : `${r.avg_late_min.toFixed(1)} min`}</td>
                        <td>
                          {r.avg_load_share == null ? '–' : (
                            <div className="mini-bar"><div style={{ width: pct(Math.min(r.avg_load_share, 1)) }} /><span>{pct(r.avg_load_share)}</span></div>
                          )}
                        </td>
                        <td>{r.trips_with_load ? `${r.near_empty_trips} / ${r.trips_with_load}` : '–'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="tiny muted">On time = the bus’s first trip of the day left campus no more than 5 min after its timetable slot. Load is the seat count when the
                bus left. Near-empty = under 30% full, a candidate for merging trips.</p>
            </section>
          </>
        )}
      </main>
    </div>
  )
}

function Stat({ label, value, hint }) {
  return (
    <div className="card stat">
      <div className="tiny muted">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="tiny muted">{hint}</div>}
    </div>
  )
}
