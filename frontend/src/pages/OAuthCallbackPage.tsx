import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { popReturnJob, saveTokenFromFragment } from '../lib/auth'

/**
 * Landing page for the backend's web-mode OAuth redirect:
 * /oauth/callback#access_token=...&refresh_token=...&expires_in=...
 * (or #error=... on failure). Stores the token and returns to the job page.
 */
export default function OAuthCallbackPage() {
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const failure = saveTokenFromFragment(window.location.hash)
    // Strip tokens from the address bar/history either way.
    window.history.replaceState(null, '', window.location.pathname)
    if (failure) {
      setError(failure)
      return
    }
    const jobId = popReturnJob()
    navigate(jobId ? `/jobs/${jobId}` : '/', { replace: true })
  }, [navigate])

  if (error) {
    return (
      <div className="page">
        <div className="card">
          <p className="error-text">Google sign-in failed: {error}</p>
          <Link to="/">← Back to jobs</Link>
        </div>
      </div>
    )
  }
  return (
    <div className="page">
      <div className="card muted">Finishing Google sign-in…</div>
    </div>
  )
}
