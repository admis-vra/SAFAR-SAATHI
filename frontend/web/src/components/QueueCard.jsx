import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { notify } from '../prefs'

const CALL_WINDOW_S = 300

function useCountdown(calledAt) {
  const [left, setLeft] = useState(null)
  useEffect(() => {
    if (!calledAt) return
    const deadline = new Date(calledAt).getTime() + CALL_WINDOW_S * 1000
    const tick = () => setLeft(Math.max(0, Math.round((deadline - Date.now()) / 1000)))
    const first = setTimeout(tick, 0)
    const id = setInterval(tick, 1000)
    return () => { clearTimeout(first); clearInterval(id) }
  }, [calledAt])
  return calledAt ? left : null
}

const DONE = {
  boarded: ['good', 'You boarded. Have a safe trip!'],
  no_show: ['bad', 'You were called but didn’t board, so your place was given away.'],
  expired: ['bad', 'Your token expired (not boarded in time, or the bus left).'],
  cancelled: ['neutral', 'You left the queue.'],
}

export default function QueueCard({ tick, onError }) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const lastStatus = useRef(null)

  useEffect(() => {
    api('/me/queue').then((d) => {
      const status = d.token?.status ?? null
      if (status === 'called' && lastStatus.current === 'waiting') {
        notify('Your turn to board!', `Board Bus ${d.queue.bus_id} within 5 minutes and show your screen.`)
      }
      lastStatus.current = status
      setData(d)
    }).catch(onError)
  }, [tick, onError])

  const t = data?.token
  const left = useCountdown(t?.status === 'called' ? t.called_at : null)
  if (!data?.queue) return null
  const q = data.queue

  const act = async (method) => {
    setBusy(true)
    setMessage('')
    try {
      if (method === 'POST') {
        setData(await api('/me/queue', { method }))
      } else {
        await api('/me/queue', { method })
        setData(await api('/me/queue'))
      }
    } catch (err) {
      if (err.status === 409) setMessage(err.message)
      else onError(err)
    } finally {
      setBusy(false)
    }
  }

  // Already aboard: nothing more to queue for today on this bus.
  const active = t && (t.status === 'waiting' || t.status === 'called' || t.status === 'boarded')
  return (
    <section className={`card queue-card ${t?.status === 'called' ? 'called' : ''}`}>
      <div className="row-between">
        <div className="eyebrow">Boarding queue · Bus {q.bus_id}</div>
        <span className="tiny muted">opens {q.opens_at} · departs {q.departs_at}</span>
      </div>

      {t?.status === 'called' && (
        <div className="your-turn">
          <div className="your-turn-title">Your turn! Board Bus {q.bus_id} now</div>
          <div className="your-turn-timer">{left != null && `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`}</div>
          <div className="tiny">Show this screen to the conductor.</div>
        </div>
      )}

      {t?.status === 'waiting' && (
        <div className="queue-pos">
          <div><span className="queue-num">#{t.position}</span> in line · {t.ahead} ahead of you</div>
          <div className={t.likely_seat ? 'good-text' : 'muted'}>
            {t.likely_seat ? 'Likely to get a seat on this bus' : 'May not fit on this bus'}
          </div>
        </div>
      )}

      {t && DONE[t.status] && <div className={`alert ${DONE[t.status][0]}`}>{DONE[t.status][1]}</div>}

      {!active && q.state === 'open' && (
        <button className="btn primary block" disabled={busy} onClick={() => act('POST')}>
          Join queue
        </button>
      )}
      {!active && q.state === 'not_open' && <p className="muted">The queue opens at {q.opens_at}.</p>}
      {!active && q.state === 'closed' && <p className="muted">Bus {q.bus_id} has left; the queue is closed.</p>}
      {active && t.status !== 'boarded' && (
        <button className="btn ghost block" disabled={busy} onClick={() => act('DELETE')}>Leave queue</button>
      )}
      {message && <div className="alert warn small-text">{message}</div>}
    </section>
  )
}
