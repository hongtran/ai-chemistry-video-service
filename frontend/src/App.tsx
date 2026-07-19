import { Link, Route, Routes, useNavigate } from 'react-router-dom'
import JobListPage from './pages/JobListPage'
import JobDetailPage from './pages/JobDetailPage'
import LoginPage from './pages/LoginPage'
import OAuthCallbackPage from './pages/OAuthCallbackPage'
import { clearAdminToken, getAdminToken } from './lib/adminAuth'

function App() {
  const navigate = useNavigate()
  const signedIn = getAdminToken() !== null

  const signOut = () => {
    clearAdminToken()
    navigate('/login')
  }

  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="app-title">
          🎬 AI Video Studio
        </Link>
        {signedIn && (
          <button type="button" className="signout" onClick={signOut}>
            Sign out
          </button>
        )}
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<JobListPage />} />
          <Route path="/jobs/:id" element={<JobDetailPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/oauth/callback" element={<OAuthCallbackPage />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
