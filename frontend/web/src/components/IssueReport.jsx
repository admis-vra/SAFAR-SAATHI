import { useEffect, useState } from 'react'
import { api } from '../api'
import Icon from './Icon'

const CATEGORIES = [
  ['overcrowding', 'Overcrowding'], ['driving', 'Rash / unsafe driving'], ['cleanliness', 'Cleanliness'],
  ['breakdown', 'Breakdown / AC / fan'], ['safety', 'Safety concern'], ['staff', 'Staff behaviour'], ['other', 'Other'],
]
const STATUS = { open: ['neutral', 'Open'], in_progress: ['info', 'Being handled'], resolved: ['good', 'Resolved'] }

export default function IssueReport({ onError, onDone }) {
  const [mine, setMine] = useState([])
  const [category, setCategory] = useState('overcrowding')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api('/me/issues').then(setMine).catch(onError)
  }, [onError])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      const issue = await api('/issues', { method: 'POST', body: { category, description: text.trim() } })
      setMine((m) => [issue, ...m])
      setText('')
      onDone?.()
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card settings-card">
      <h3 className="settings-title"><Icon name="alert" /> Report an issue</h3>
      <p className="tiny muted">Goes to the transport office, linked to your bus. You’ll see their reply here.</p>
      <form className="form" onSubmit={submit}>
        <label>What happened?
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label>Details
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} minLength={5} maxLength={1000} required
            placeholder="e.g. Bus 14 was overcrowded at stop E, people standing on the steps" />
        </label>
        <button className="btn primary" disabled={busy || text.trim().length < 5}>{busy ? 'Sending…' : 'Send report'}</button>
      </form>
      {mine.length > 0 && (
        <ul className="issue-list">
          {mine.slice(0, 5).map((i) => {
            const [tone, label] = STATUS[i.status] ?? STATUS.open
            return (
              <li key={i.id}>
                <div className="row-between">
                  <b className="small-text">{CATEGORIES.find(([v]) => v === i.category)?.[1]}{i.bus_id ? ` · Bus ${i.bus_id}` : ''}</b>
                  <span className={`pill ${tone}`}>{label}</span>
                </div>
                <div className="tiny muted">{new Date(i.created_at).toLocaleString()}</div>
                {i.office_note && <div className="tiny office-note">Office: {i.office_note}</div>}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
