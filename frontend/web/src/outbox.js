import { api } from './api'

// Conductor actions that couldn't reach the server (bus out of coverage). Kept on the phone and replayed later.
const KEY = 'safarsaathi.outbox'

export function pending() {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]')
  } catch {
    return []
  }
}

function save(items) {
  localStorage.setItem(KEY, JSON.stringify(items))
}

export function enqueue(path, body) {
  save([...pending(), { path, body: body ?? null, at: Date.now() }])
}

// Replay oldest first. A 4xx means the server already has a final answer (e.g. token already closed): drop it.
// A network error (status 0) or 5xx means try again later.
export async function flush() {
  let items = pending()
  while (items.length) {
    const [first, ...rest] = items
    try {
      await api(first.path, { method: 'POST', body: first.body ?? undefined })
    } catch (err) {
      if (err.status === 0 || err.status >= 500) break
    }
    items = rest
    save(items)
  }
  return items.length
}
