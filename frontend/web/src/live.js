import { useEffect, useRef, useState } from 'react'

export function useLiveBuses(onEvent) {
  const [buses, setBuses] = useState({})
  const [connected, setConnected] = useState(false)
  const handler = useRef(onEvent)

  useEffect(() => {
    handler.current = onEvent
  })

  useEffect(() => {
    let ws
    let retry
    let closed = false

    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws`)
      ws.onopen = () => setConnected(true)
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data)
        if (msg.type === 'buses') setBuses(Object.fromEntries(msg.data.map((b) => [b.id, b])))
        if (msg.type === 'bus') setBuses((prev) => ({ ...prev, [msg.data.id]: msg.data }))
        handler.current?.(msg)
      }
      ws.onclose = () => {
        setConnected(false)
        if (!closed) retry = setTimeout(connect, 2000)
      }
    }
    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      ws?.close()
    }
  }, [])

  return { buses, connected }
}
