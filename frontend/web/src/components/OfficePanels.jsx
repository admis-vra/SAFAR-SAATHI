import { useEffect, useState } from 'react'
import { api } from '../api'
import Icon from './Icon'

const DURATIONS = [[30, '30 min'], [60, '1 hour'], [120, '2 hours'], [240, '4 hours'], [720, 'Rest of day']]

export function AnnouncementPanel({ routes, tick, onError }) {
  const [active, setActive] = useState([])
  const [form, setForm] = useState({ kind: 'delay', route_id: '', message: '', minutes: 60 })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/announcements').then(setActive).catch(onError)
  }, [tick, onError])

  const send = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      await api('/announcements', {
        method: 'POST',
        body: { ...form, route_id: form.route_id ? Number(form.route_id) : null, minutes: Number(form.minutes) },
      })
      setForm((f) => ({ ...f, message: '' }))
      setActive(await api('/announcements'))
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  const end = async (id) => {
    try {
      await api(`/announcements/${id}`, { method: 'DELETE' })
      setActive((a) => a.filter((x) => x.id !== id))
    } catch (err) {
      onError(err)
    }
  }

  return (
    <section className="card">
      <div className="eyebrow">Announcements to riders</div>
      <form className="form compact" onSubmit={send}>
        <div className="row">
          <label>Type
            <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="delay">Delay</option><option value="cancellation">Cancellation</option>
              <option value="info">Notice</option>
            </select>
          </label>
          <label>To
            <select value={form.route_id} onChange={(e) => setForm({ ...form, route_id: e.target.value })}>
              <option value="">All routes</option>
              {routes.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
          </label>
          <label>For
            <select value={form.minutes} onChange={(e) => setForm({ ...form, minutes: e.target.value })}>
              {DURATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        </div>
        <label>Message
          <input value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} maxLength={280}
            placeholder="e.g. Route 3 buses running 15 min late due to traffic at ISBT" required minLength={3} />
        </label>
        <button className="btn primary" disabled={busy}><Icon name="bell" size={16} /> Send to riders</button>
      </form>
      {active.length > 0 && (
        <ul className="incident-list announce-list">
          {active.map((a) => (
            <li key={a.id}>
              <span className={`pill ${a.kind === 'cancellation' ? 'bad' : a.kind === 'delay' ? 'warn-pill' : 'neutral'}`}>{a.kind}</span>
              <span className="small-text grow">{a.route_id ? routes.find((r) => r.id === a.route_id)?.name : 'All routes'}: {a.message}</span>
              <button className="btn ghost small" onClick={() => end(a.id)}>End</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

const ISSUE_LABEL = { overcrowding: 'Overcrowding', driving: 'Driving', cleanliness: 'Cleanliness', breakdown: 'Breakdown',
  safety: 'Safety', staff: 'Staff', other: 'Other' }

export function IssuesPanel({ tick, onError }) {
  const [issues, setIssues] = useState([])
  const [showResolved, setShowResolved] = useState(false)

  useEffect(() => {
    api('/issues').then(setIssues).catch(onError)
  }, [tick, onError])

  const update = async (id, status) => {
    const note = status === 'resolved' ? window.prompt('Note for the reporter (optional)') : null
    try {
      const row = await api(`/issues/${id}`, { method: 'PATCH', body: { status, note: note || null } })
      setIssues((list) => list.map((i) => (i.id === id ? { ...i, ...row } : i)))
    } catch (err) {
      onError(err)
    }
  }

  const shown = issues.filter((i) => showResolved || i.status !== 'resolved')
  return (
    <section className="card">
      <div className="row-between">
        <div className="eyebrow">Issue reports ({issues.filter((i) => i.status !== 'resolved').length} open)</div>
        <label className="tiny inline-check">
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} /> show resolved
        </label>
      </div>
      {shown.length === 0 ? <p className="muted small-text">No open reports.</p> : (
        <ul className="issue-list">
          {shown.slice(0, 20).map((i) => (
            <li key={i.id}>
              <div className="row-between">
                <b className="small-text">{ISSUE_LABEL[i.category]}{i.bus_id ? ` · Bus ${i.bus_id}` : ''}
                  <span className="tiny muted"> · {i.reporter_role} · {new Date(i.created_at).toLocaleString()}</span></b>
                <span className={`pill ${i.status === 'resolved' ? 'good' : i.status === 'in_progress' ? 'info' : 'neutral'}`}>
                  {i.status.replace('_', ' ')}
                </span>
              </div>
              <div className="small-text">{i.description}</div>
              {i.office_note && <div className="tiny office-note">Note: {i.office_note}</div>}
              {i.status !== 'resolved' && (
                <div className="row gap-s issue-actions">
                  {i.status === 'open' && <button className="btn ghost small" onClick={() => update(i.id, 'in_progress')}>Take it</button>}
                  <button className="btn ghost small" onClick={() => update(i.id, 'resolved')}>Resolve</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
