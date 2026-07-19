import { clearAdminToken, getAdminToken } from '../lib/adminAuth'
import type {
  CreateVideoRequest,
  CreateVideoResponse,
  CreateYouTubeUploadRequest,
  CreateYouTubeUploadResponse,
  JobDetail,
  JobSummary,
  LoginResponse,
  YouTubeUploadDetail,
} from './types'

// Empty in dev: the Vite proxy forwards /api to the backend. Set
// VITE_API_BASE to call a remote backend directly (CORS is enabled server-side).
const BASE = import.meta.env.VITE_API_BASE ?? ''

/** FastAPI error `detail` — a plain message, or the 409 not-ready object. */
export type ApiErrorDetail =
  | string
  | { message: string; status?: string; current_step?: string | null; error_message?: string | null }

export class ApiError extends Error {
  status: number
  detail: ApiErrorDetail

  constructor(status: number, detail: ApiErrorDetail) {
    super(typeof detail === 'string' ? detail : detail.message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

function authHeaders(hasBody: boolean): Record<string, string> {
  const headers: Record<string, string> = {}
  if (hasBody) headers['Content-Type'] = 'application/json'
  const token = getAdminToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

async function toApiError(res: Response): Promise<ApiError> {
  let detail: ApiErrorDetail = res.statusText
  try {
    const body = await res.json()
    if (body.detail !== undefined) detail = body.detail
  } catch {
    // non-JSON error body; keep statusText
  }
  return new ApiError(res.status, detail)
}

/** Admin-session 401s carry realm="admin"; the Google-token 401 does not. */
function handleAdminSessionExpiry(res: Response): void {
  if (res.status !== 401) return
  if (!res.headers.get('www-authenticate')?.includes('realm="admin"')) return
  clearAdminToken()
  if (window.location.pathname !== '/login') {
    window.location.assign('/login')
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: authHeaders(!!init?.body),
  })
  if (!res.ok) {
    handleAdminSessionExpiry(res)
    throw await toApiError(res)
  }
  return res.json() as Promise<T>
}

export function createVideo(body: CreateVideoRequest): Promise<CreateVideoResponse> {
  return request('/api/v1/videos', { method: 'POST', body: JSON.stringify(body) })
}

export function listVideos(): Promise<JobSummary[]> {
  return request('/api/v1/videos')
}

export function getJob(jobId: string): Promise<JobDetail> {
  return request(`/api/v1/videos/${jobId}`)
}

export function login(username: string, password: string): Promise<LoginResponse> {
  return request('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

/** Authenticated download: fetch as blob (an <a href> can't send the
 * Authorization header) and trigger a save via an object URL. */
export async function downloadVideo(jobId: string, filename: string): Promise<void> {
  const res = await fetch(`${BASE}/api/v1/videos/${jobId}/video`, {
    headers: authHeaders(false),
  })
  if (!res.ok) {
    handleAdminSessionExpiry(res)
    throw await toApiError(res)
  }
  const url = URL.createObjectURL(await res.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function startYouTubeUpload(
  jobId: string,
  body: CreateYouTubeUploadRequest,
): Promise<CreateYouTubeUploadResponse> {
  return request(`/api/v1/videos/${jobId}/youtube`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function getUpload(uploadId: string): Promise<YouTubeUploadDetail> {
  return request(`/api/v1/youtube-uploads/${uploadId}`)
}

export async function getGoogleAuthUrl(): Promise<string> {
  const { auth_url } = await request<{ auth_url: string }>(
    '/api/v1/auth/google/login?redirect=false&mode=web',
  )
  return auth_url
}
