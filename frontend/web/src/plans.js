import { useEffect, useState } from 'react'
import { api } from './api'

export function slotLabel(slot) {
  if (!slot) return ''
  const h = Number(slot.slice(0, 2))
  return `${h > 12 ? h - 12 : h} ${h >= 12 ? 'PM' : 'AM'}`
}

export function useStops() {
  const [stops, setStops] = useState([])
  useEffect(() => { api('/stops').then(setStops).catch(() => {}) }, [])
  return stops
}

// Before noon the morning run to college matters; after it, the trip home.
export function useIsMorning() {
  const [morning, setMorning] = useState(() => new Date().getHours() < 12)
  useEffect(() => {
    const id = setInterval(() => setMorning(new Date().getHours() < 12), 60000)
    return () => clearInterval(id)
  }, [])
  return morning
}
