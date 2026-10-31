import { roleOf, supabase } from './supabase'

export function homeFor(session) {
  return { passenger: '/passenger', dispatcher: '/office' }[roleOf(session)] ?? '/staff'
}

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

function errorMessage(detail) {
  if (Array.isArray(detail)) {
    return detail.map((d) => `${d.loc?.at(-1) ?? 'field'}: ${d.msg}`).join(', ')
  }
  return detail || 'Something went wrong'
}

export async function api(path, { method = 'GET', body } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const { data } = await supabase.auth.getSession()
  if (data.session) headers.Authorization = `Bearer ${data.session.access_token}`
  let res
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body && JSON.stringify(body) })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection.')
  }
  const json = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, errorMessage(json.detail))
  return json
}

export async function logout() {
  await supabase.auth.signOut()
}
