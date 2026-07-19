import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, login } from '../api/client'
import { saveAdminToken } from '../lib/adminAuth'

export default function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const session = await login(username, password)
      saveAdminToken(session.token, session.expires_in)
      navigate('/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) {
        // Auth is disabled server-side — no login needed.
        navigate('/', { replace: true })
        return
      }
      if (err instanceof ApiError && err.status === 401) {
        setError('Invalid username or password.')
      } else {
        setError(err instanceof Error ? err.message : 'Login failed — is the backend running?')
      }
      setBusy(false)
    }
  }

  return (
    <div className="page login-page">
      <form className="card login-card" onSubmit={submit}>
        <h2>Sign in</h2>
        <p className="muted">Internal tool — admin credentials required.</p>
        <label className="field">
          <span className="field-label">Username</span>
          <input
            value={username}
            autoComplete="username"
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input
            type="password"
            value={password}
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="error-text">{error}</p>}
        <button type="submit" className="primary" disabled={!username || !password || busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}
