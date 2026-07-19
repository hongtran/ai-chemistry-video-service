// The backend is a stateless OAuth broker: the browser owns the Google token.
// Stored in localStorage; expiry tracked client-side from expires_in.

const TOKEN_KEY = 'youtube_token'
const RETURN_JOB_KEY = 'oauth_return_job'
const EXPIRY_MARGIN_MS = 60_000

interface StoredToken {
  access_token: string
  refresh_token?: string
  expires_at: number
}

/** Parse the OAuth callback fragment. Returns an error message, or null on success. */
export function saveTokenFromFragment(hash: string): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const error = params.get('error')
  if (error) return error
  const accessToken = params.get('access_token')
  if (!accessToken) return 'No access token in callback URL.'
  const expiresIn = Number(params.get('expires_in') ?? 3600)
  const token: StoredToken = {
    access_token: accessToken,
    refresh_token: params.get('refresh_token') ?? undefined,
    expires_at: Date.now() + expiresIn * 1000,
  }
  localStorage.setItem(TOKEN_KEY, JSON.stringify(token))
  return null
}

export function getValidToken(): string | null {
  const raw = localStorage.getItem(TOKEN_KEY)
  if (!raw) return null
  try {
    const token: StoredToken = JSON.parse(raw)
    if (Date.now() > token.expires_at - EXPIRY_MARGIN_MS) return null
    return token.access_token
  } catch {
    return null
  }
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

// Which job page to return to after the full-page OAuth round trip.
export function stashReturnJob(jobId: string): void {
  sessionStorage.setItem(RETURN_JOB_KEY, jobId)
}

export function popReturnJob(): string | null {
  const jobId = sessionStorage.getItem(RETURN_JOB_KEY)
  sessionStorage.removeItem(RETURN_JOB_KEY)
  return jobId
}
