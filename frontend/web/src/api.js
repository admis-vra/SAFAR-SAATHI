const KEY = 'safarsaathi.session'

export function getSession() {
  try {
    return JSON.parse(localStorage.getItem(KEY))
  } catch {
    return null
  }
}

export function saveSession(token, user) {
  localStorage.setItem(KEY, JSON.stringify({ token, user }))
}

export function homeFor(user) {
  return user.role === 'passenger' ? '/passenger' : '/staff'
}

export function clearSession() {
  localStorage.removeItem(KEY)
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
  const session = getSession()
  if (session?.token) headers.Authorization = `Bearer ${session.token}`
  let res
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body && JSON.stringify(body) })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection.')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, errorMessage(data.detail))
  return data
}

export async function logout() {
  await api('/auth/logout', { method: 'POST' }).catch(() => {})
  clearSession()
}
