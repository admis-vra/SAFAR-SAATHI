import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const key = import.meta.env.VITE_SUPABASE_ANON_KEY

export const supabaseConfigured = Boolean(url && key)
export const supabase = supabaseConfigured ? createClient(url, key) : null

const ROLES = ['driver', 'conductor', 'dispatcher']

export function roleOf(session) {
  const role = session?.user?.app_metadata?.role
  return ROLES.includes(role) ? role : 'passenger'
}
