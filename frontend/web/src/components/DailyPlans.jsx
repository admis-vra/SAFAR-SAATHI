import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { slotLabel } from '../plans'


const km = (m) => `${(m / 1000).toFixed(1)} km`

// ---------- stop search (one stop can be on several routes) ----------

export function StopPicker({ stops, routes, value, routeId, onChange }) {
  const [q, setQ] = useState(value ?? '')
  const needle = q.trim().toLowerCase()
  const matches = useMemo(() => (needle ? stops.filter((s) => s.name.toLowerCase().includes(needle)) : stops), [stops, needle])
  const chosen = stops.find((s) => s.name === value)

  const pick = (s) => {
    setQ(s.name)
    onChange({ stop: s.name, route_id: s.routes[0] })
  }

  return (
    <div className="stop-picker">
      <label>
        Your stop
        <input value={q} placeholder="Search, e.g. Ghanta Ghar, Rispana, ISBT…" autoComplete="off"
          onChange={(e) => { setQ(e.target.value); if (value) onChange({ stop: '', route_id: null }) }} />
      </label>
      {!chosen && (
        <ul className="stop-options" role="listbox">
          {matches.slice(0, 8).map((s) => (
            <li key={s.name}>
              <button type="button" onClick={() => pick(s)}>
                <span>{s.name}</span>
                <span className="tiny muted">Bus {s.buses.slice(0, 6).join(', ')}{s.buses.length > 6 ? '…' : ''}</span>
              </button>
            </li>
          ))}
          {matches.length === 0 && <li className="tiny muted">No stop matches “{q}”.</li>}
        </ul>
      )}
      {chosen && (
        <div className="tiny muted">
          Buses from this stop: <b>{chosen.buses.join(', ') || 'none yet'}</b>
          {chosen.routes.length > 1 && (
            <label className="inline-select">
              {' · '}Usual route{' '}
              <select value={routeId ?? chosen.routes[0]} onChange={(e) => onChange({ stop: chosen.name, route_id: Number(e.target.value) })}>
                {chosen.routes.map((id) => <option key={id} value={id}>{routes.find((r) => r.id === id)?.name ?? id}</option>)}
              </select>
            </label>
          )}
        </div>
      )}
    </div>
  )
}

// ---------- passenger: morning buses to college ----------

const MORNING_STATE = {
  arriving: ['At your stop now', 'good'],
  coming: ['Coming', 'info'],
  scheduled: ['Not started', 'neutral'],
  passed: ['Passed your stop', 'bad'],
  probably_passed: ['Likely passed', 'warn-pill'],
  at_campus: ['Reached college', 'neutral'],
  done: ['Done for today', 'neutral'],
  no_timetable: ['No timetable', 'neutral'],
}

function MorningRow({ b }) {
  const [label, tone] = MORNING_STATE[b.state]
  const e = b.eta
  return (
    <li className={`morning-row ${b.state}`}>
      <span className={`bus-badge ${tone}`}>{b.bus_id}</span>
      <div className="morning-info">
        <div className="row-between">
          <b>{label}</b>
          {b.state === 'coming' && <span className="good-text">{e.minutes} min · ~{e.at}</span>}
          {b.state === 'scheduled' && e && <span className="muted">due ~{e.at}</span>}
        </div>
        <div className="tiny muted">
          {b.state === 'coming' && `${km(e.distance_m)} away · live GPS`}
          {b.state === 'arriving' && 'Live GPS · board now'}
          {b.state === 'scheduled' && `Run starts ${b.run} from ${b.first_stop}${e ? ' · timetable estimate' : ''}`}
          {b.state === 'passed' && `Live GPS shows it is past your stop`}
          {b.state === 'probably_passed' && `Was due ~${b.scheduled_at}; driver isn’t sharing location`}
          {b.state === 'at_campus' && 'Already at college'}
          {b.state === 'done' && `Runs: ${b.runs.join(', ')}`}
          {' · '}{b.route}
        </div>
      </div>
    </li>
  )
}

export function MorningBuses({ stop, tick, onData, onError }) {
  const [data, setData] = useState(null)
  const load = useCallback(() => {
    if (!stop) return
    api(`/me/morning?stop=${encodeURIComponent(stop)}`).then((d) => { setData(d); onData?.(d) }).catch(onError)
  }, [stop, onData, onError])

  useEffect(load, [load, tick])
  useEffect(() => {
    const id = setInterval(load, 30000)   // timetable states change with the clock, not just GPS
    return () => clearInterval(id)
  }, [load])

  if (!data) return <section className="card skeleton">Checking morning buses…</section>
  const next = data.next
  return (
    <section className="card morning-card">
      <div className="eyebrow">Morning buses to college · from {data.stop}</div>
      {data.buses.length === 0 ? <p className="muted">No bus serves this stop.</p> : (
        <>
          {next?.state === 'arriving' && <div className="alert good big-alert">Bus {next.bus_id} is at {data.stop} now. Board it!</div>}
          {next?.state === 'coming' && (
            <div className="alert info">
              <b>Bus {next.bus_id}</b> reaches {data.stop} in <b>{next.eta.minutes} min</b> (~{next.eta.at}).
              {next.eta.minutes <= 5 ? ' Head to the stop now.' : ''}
            </div>
          )}
          {next?.state === 'scheduled' && next.eta && (
            <div className="alert neutral">
              Next: <b>Bus {next.bus_id}</b>, due ~{next.eta.at} ({next.eta.minutes} min).
              <span className="tiny muted"> Timetable estimate until the driver shares live location.</span>
            </div>
          )}
          {data.all_gone && (
            <div className="alert bad">
              All morning buses from {data.stop} have passed.
              {data.later_runs.length > 0 && ` The next pickup run starts at ${data.later_runs[0]}.`}
            </div>
          )}
          <ul className="morning-list">
            {data.buses.map((b) => <MorningRow key={b.bus_id} b={b} />)}
          </ul>
        </>
      )}
    </section>
  )
}

// ---------- passenger: which departure home today ----------

export function GoingHome({ tick, onChanged, onError }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => { api('/me/departure').then(setData).catch(onError) }, [onError])
  useEffect(load, [load, tick])

  const choose = async (slot) => {
    setBusy(true)
    try {
      setData(await api('/me/departure', { method: 'PUT', body: { slot } }))
      onChanged?.()
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  if (!data) return null
  const options = [...data.slots.map((s) => [s, slotLabel(s)]), [null, 'Not today']]
  return (
    <section className="card going-home">
      <div className="row-between">
        <div className="eyebrow">Going home today</div>
        <span className={`tiny ${data.locked ? 'muted' : 'warn-text'}`}>
          {data.locked ? `Closed at ${data.deadline}` : `Choose by ${data.deadline} AM`}
        </span>
      </div>
      <div className="slot-buttons" role="group" aria-label="Departure from college">
        {options.map(([slot, label]) => {
          const active = data.answered && data.slot === slot
          return (
            <button key={label} className={`slot-btn ${active ? 'active' : ''}`} aria-pressed={active}
              disabled={busy || data.locked} onClick={() => choose(slot)}>
              <span className="slot-time">{label}</span>
              {slot && <span className="tiny">{data.stop_counts[slot]} from your stop</span>}
            </button>
          )
        })}
      </div>
      {!data.answered ? (
        <p className="small-text muted">
          {data.locked ? 'You didn’t choose a time today. Ask the conductor at the bus.'
            : 'Tell us when you’re leaving so staff send enough buses to your stop.'}
        </p>
      ) : data.slot === null ? (
        <p className="small-text muted">Noted: you’re not taking a bus home today.</p>
      ) : (
        <p className="small-text">
          You’re on the <b>{slotLabel(data.slot)}</b> list from <b>{data.stop}</b>.{' '}
          {data.buses.length ? <>Your bus: <b>{data.buses.map((b) => `Bus ${b.id} (${b.gate})`).join(' or ')}</b></>
            : <span className="muted">Bus not assigned yet. Staff assign buses after {data.deadline}.</span>}
        </p>
      )}
    </section>
  )
}

// ---------- staff: pick today's bus ----------

export function BusPicker({ current, onPicked, onCancel, onError }) {
  const [list, setList] = useState(null)
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(null)
  useEffect(() => { api('/staff/buses').then(setList).catch(onError) }, [onError])

  const needle = q.trim().toLowerCase()
  const shown = (list ?? []).filter((b) => !needle || String(b.id) === needle
    || `${b.route_name} ${b.registration ?? ''}`.toLowerCase().includes(needle))

  const pick = async (id) => {
    setBusy(id)
    try {
      await api('/me/bus-pick', { method: 'PUT', body: { bus_id: id } })
      onPicked(id)
    } catch (err) {
      onError(err)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card bus-picker">
      <div className="row-between">
        <div>
          <div className="eyebrow">Today’s bus</div>
          <b>Which bus are you on today?</b>
        </div>
        {onCancel && <button className="btn ghost small" onClick={onCancel}>Cancel</button>}
      </div>
      <input className="picker-search" value={q} onChange={(e) => setQ(e.target.value)} inputMode="search"
        placeholder="Bus number, stop or registration…" aria-label="Find your bus" />
      {!list ? <p className="muted">Loading buses…</p> : (
        <div className="picker-grid">
          {shown.slice(0, 40).map((b) => (
            <button key={b.id} className={`picker-item ${b.id === current ? 'on' : ''}`}
              disabled={busy !== null || !!b.driver_today} onClick={() => pick(b.id)}>
              <span className="bus-badge">{b.id}</span>
              <span className="picker-text">
                <span>{b.route_name}</span>
                <span className="tiny muted">
                  {b.registration ?? ''}{b.runs.length ? ` · ${b.runs.join(' & ')}` : ''}
                  {b.driver_today && ` · driver: ${b.driver_today}`}
                </span>
              </span>
            </button>
          ))}
          {shown.length === 0 && <p className="tiny muted">No bus matches.</p>}
        </div>
      )}
    </section>
  )
}

// ---------- staff: demand per slot and this bus's departure ----------

function useDemand(tick, onError) {
  const [data, setData] = useState(null)
  const load = useCallback(() => { api('/departures/demand').then(setData).catch(onError) }, [onError])
  useEffect(load, [load, tick])
  return [data, load]
}

export function SlotAssign({ bus, tick, onError }) {
  const [data, reload] = useDemand(tick, onError)
  const [busy, setBusy] = useState(false)

  const assign = async (slot) => {
    setBusy(true)
    try {
      await api(`/buses/${bus.id}/slot`, { method: 'PUT', body: { slot } })
      reload()
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  if (!data?.my_bus) return null
  const mine = data.my_bus
  return (
    <section className="card slot-assign">
      <div className="row-between">
        <div className="eyebrow">Trip home</div>
        <span className="tiny muted">{data.answered} of {data.registered} students answered</span>
      </div>
      <p className="small-text">Students going home from Bus {mine.id}’s stops ({mine.capacity} seats):</p>
      <div className="slot-buttons">
        {data.slots.map((s) => {
          const n = mine.demand[s.slot]
          const active = mine.slot === s.slot
          return (
            <button key={s.slot} className={`slot-btn ${active ? 'active' : ''} ${n > mine.capacity ? 'over' : ''}`}
              aria-pressed={active} disabled={busy} onClick={() => assign(active ? null : s.slot)}>
              <span className="slot-time">{s.label}</span>
              <span className="tiny"><b>{n}</b> students · {s.buses.length} bus{s.buses.length === 1 ? '' : 'es'} so far</span>
            </button>
          )
        })}
      </div>
      <p className="tiny muted">
        {mine.slot ? `Bus ${mine.id} leaves campus at ${slotLabel(mine.slot)} today. Tap again to unassign.`
          : 'Tap the time this bus will leave campus today.'}
        {!data.locked && ` Students can still change until ${data.deadline} AM.`}
        {' '}Counts include students at your stops who may also ride other buses on the same road.
      </p>
    </section>
  )
}

// ---------- office: whole-day demand board ----------

export function DemandBoard({ tick, onError }) {
  const [data, reload] = useDemand(tick, onError)
  const [form, setForm] = useState({ bus: '', slot: '16:00' })
  const [busy, setBusy] = useState(false)

  const assign = async (e) => {
    e.preventDefault()
    setBusy(true)
    try {
      await api(`/buses/${Number(form.bus)}/slot`, { method: 'PUT', body: { slot: form.slot || null } })
      setForm((f) => ({ ...f, bus: '' }))
      reload()
    } catch (err) {
      onError(err)
    } finally {
      setBusy(false)
    }
  }

  if (!data) return null
  return (
    <section className="card">
      <div className="row-between">
        <div className="eyebrow">Today’s trips home · demand vs buses</div>
        <span className="tiny muted">
          {data.answered} of {data.registered} students answered · {data.not_going} not going
          {data.locked ? ' · choices closed' : ` · open until ${data.deadline}`}
        </span>
      </div>
      <div className="demand-grid">
        {data.slots.map((s) => {
          const short = s.students > s.seats
          return (
            <div key={s.slot} className={`demand-slot ${short ? 'short' : ''}`}>
              <div className="row-between">
                <b>{s.label}</b>
                <span className={short ? 'bad-text' : 'good-text'}>{s.students} students / {s.seats} seats</span>
              </div>
              <div className="tiny muted">Buses: {s.buses.length ? s.buses.join(', ') : 'none assigned'}</div>
              <ul className="demand-stops">
                {s.stops.slice(0, 8).map((st) => (
                  <li key={st.stop} className={st.buses.length ? '' : 'uncovered'}>
                    <span>{st.stop}</span>
                    <span className="tiny">{st.students} · {st.buses.length ? `Bus ${st.buses.join(', ')}` : 'no bus'}</span>
                  </li>
                ))}
                {s.stops.length > 8 && <li className="tiny muted">+{s.stops.length - 8} more stops</li>}
              </ul>
            </div>
          )
        })}
      </div>
      <form className="row gap form compact" onSubmit={assign}>
        <label>Bus<input type="number" min={1} value={form.bus} required onChange={(e) => setForm({ ...form, bus: e.target.value })} /></label>
        <label>Leaves campus
          <select value={form.slot} onChange={(e) => setForm({ ...form, slot: e.target.value })}>
            {data.slots.map((s) => <option key={s.slot} value={s.slot}>{s.label}</option>)}
            <option value="">Not running home</option>
          </select>
        </label>
        <button className="btn primary small" disabled={busy}>Assign</button>
      </form>
    </section>
  )
}
