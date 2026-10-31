import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { getSession, homeFor } from './api'
import Login from './pages/Login'
import Passenger from './pages/Passenger'
import Staff from './pages/Staff'

function Guard({ roles, children }) {
  const session = getSession()
  if (!session) return <Navigate to="/" replace />
  if (!roles.includes(session.user.role)) return <Navigate to={homeFor(session.user)} replace />
  return children
}

function Home() {
  const session = getSession()
  return session ? <Navigate to={homeFor(session.user)} replace /> : <Login />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/passenger" element={<Guard roles={['passenger']}><Passenger /></Guard>} />
        <Route path="/staff" element={<Guard roles={['driver', 'conductor']}><Staff /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
