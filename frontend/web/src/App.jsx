import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { homeFor } from './api'
import { roleOf, supabase, supabaseConfigured } from './supabase'
import OfflineBanner from './components/OfflineBanner'
import Admin from './pages/Admin'
import Analytics from './pages/Analytics'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Office from './pages/Office'
import Passenger from './pages/Passenger'
import Settings from './pages/Settings'
import Staff from './pages/Staff'

function useSupabaseSession() {
  const [state, setState] = useState({ loading: true, session: null })
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setState({ loading: false, session: data.session }))
    const { data } = supabase.auth.onAuthStateChange((_event, session) => setState({ loading: false, session }))
    return () => data.subscription.unsubscribe()
  }, [])
  return state
}

function Routed() {
  const { loading, session } = useSupabaseSession()
  if (loading) return <main className="content"><div className="card skeleton">Loading…</div></main>

  const guard = (roles, page) => {
    if (!session) return <Navigate to="/" replace />
    if (!roles.includes(roleOf(session))) return <Navigate to={homeFor(session)} replace />
    return page
  }

  return (
    <Routes>
      <Route path="/" element={session ? <Navigate to={homeFor(session)} replace /> : <Landing />} />
      <Route path="/login" element={session ? <Navigate to={homeFor(session)} replace /> : <Login />} />
      <Route path="/settings"
        element={guard(['passenger', 'driver', 'conductor', 'dispatcher'], <Settings session={session} />)} />
      <Route path="/passenger" element={guard(['passenger'], <Passenger />)} />
      <Route path="/staff" element={guard(['driver', 'conductor'], <Staff />)} />
      <Route path="/office" element={guard(['dispatcher'], <Office />)} />
      <Route path="/office/analytics" element={guard(['dispatcher'], <Analytics />)} />
      <Route path="/office/admin" element={guard(['dispatcher'], <Admin />)} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

function NotConfigured() {
  return (
    <main className="content">
      <section className="card">
        <h2>Supabase is not configured</h2>
        <p>
          Copy <code>frontend/web/.env.example</code> to <code>frontend/web/.env.local</code>, fill in your
          Supabase Project URL and anon key, then restart <code>npm run dev</code>.
        </p>
      </section>
    </main>
  )
}

export default function App() {
  if (!supabaseConfigured) return <NotConfigured />
  return (
    <BrowserRouter>
      <Routed />
      <OfflineBanner />
    </BrowserRouter>
  )
}
