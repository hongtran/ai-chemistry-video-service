// Admin session token (backend /auth/login). Separate from lib/auth.ts,
// which owns the Google/YouTube token.

const TOKEN_KEY = 'admin_token'
const EXPIRY_MARGIN_MS = 60_000

interface StoredAdminToken {
  token: string
  expires_at: number
}

export function saveAdminToken(token: string, expiresIn: number): void {
  const stored: StoredAdminToken = {
    token,
    expires_at: Date.now() + expiresIn * 1000,
  }
  localStorage.setItem(TOKEN_KEY, JSON.stringify(stored))
}

export function getAdminToken(): string | null {
  const raw = localStorage.getItem(TOKEN_KEY)
  if (!raw) return null
  try {
    const stored: StoredAdminToken = JSON.parse(raw)
    if (Date.now() > stored.expires_at - EXPIRY_MARGIN_MS) return null
    return stored.token
  } catch {
    return null
  }
}

export function clearAdminToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}
