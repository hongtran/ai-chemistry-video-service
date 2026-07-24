import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  fetchThumbnailUrl,
  getGoogleAuthUrl,
  getJobMeta,
  getUpload,
  startYouTubeUpload,
} from '../api/client'
import { clearToken, getValidToken, stashReturnJob } from '../lib/auth'
import { usePolling } from '../hooks/usePolling'

export default function YouTubeUploadPanel({ jobId }: { jobId: string }) {
  const [token, setToken] = useState<string | null>(() => getValidToken())
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [tags, setTags] = useState('')
  const [privacy, setPrivacy] = useState<'public' | 'unlisted' | 'private'>('unlisted')
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Prefill the form from the video's meta.json (title/description/tags) so the
  // user sees the defaults and can edit them. Runs once; hashtags are left out
  // on purpose — the backend appends meta.hashtags to whatever description we
  // submit, so including them here would duplicate them.
  const prefilled = useRef(false)
  useEffect(() => {
    if (prefilled.current) return
    prefilled.current = true
    getJobMeta(jobId)
      .then((meta) => {
        if (meta.name) setTitle((t) => t || meta.name!.slice(0, 100))
        if (meta.description) setDescription((d) => d || meta.description!)
        if (meta.tags?.length) setTags((t) => t || meta.tags!.join(', '))
      })
      .catch(() => {
        // meta.json missing/unreadable — leave fields empty; the backend still
        // falls back to meta.json server-side at upload time.
      })
  }, [jobId])

  // The generated thumbnail that will be attached to the upload. Best-effort:
  // absent for jobs where thumbnail generation was skipped/failed, so we just
  // hide the preview then. Object URL is revoked on unmount / job change.
  const [thumbUrl, setThumbUrl] = useState<string | null>(null)
  useEffect(() => {
    let url: string | null = null
    let cancelled = false
    fetchThumbnailUrl(jobId).then((u) => {
      if (cancelled) {
        if (u) URL.revokeObjectURL(u)
        return
      }
      url = u
      setThumbUrl(u)
    })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [jobId])

  const connect = async () => {
    setBusy(true)
    setError(null)
    try {
      const authUrl = await getGoogleAuthUrl()
      stashReturnJob(jobId)
      window.location.assign(authUrl)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start Google login.')
      setBusy(false)
    }
  }

  const upload = async () => {
    const accessToken = getValidToken()
    if (!accessToken) {
      setToken(null)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const tagList = tags
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean)
      const res = await startYouTubeUpload(jobId, {
        access_token: accessToken,
        // Empty fields are omitted so the backend falls back to meta.json.
        title: title.trim() || undefined,
        description: description.trim() || undefined,
        tags: tagList.length ? tagList : undefined,
        privacy_status: privacy,
      })
      setUploadId(res.upload_id)
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearToken()
        setToken(null)
        setError('Google session expired — please reconnect.')
      } else {
        setError(err instanceof Error ? err.message : 'Upload failed to start.')
      }
    } finally {
      setBusy(false)
    }
  }

  const fetchUpload = useCallback(() => getUpload(uploadId!), [uploadId])
  const [pollActive, setPollActive] = useState(false)
  const { data: status } = usePolling(fetchUpload, 1000, pollActive && !!uploadId)

  useEffect(() => {
    if (uploadId) setPollActive(true)
  }, [uploadId])
  useEffect(() => {
    if (status && (status.status === 'COMPLETED' || status.status === 'FAILED')) {
      setPollActive(false)
    }
  }, [status])

  const uploading = status?.status === 'PENDING' || status?.status === 'UPLOADING'
  const progress =
    status && status.bytes_total > 0 ? Math.round((status.bytes_sent / status.bytes_total) * 100) : 0

  return (
    <section className="card">
      <h2>Upload to YouTube</h2>

      {thumbUrl && (
        <figure className="thumbnail-preview">
          <img src={thumbUrl} alt="Generated YouTube thumbnail" width={320} height={180} />
          <figcaption className="muted">Thumbnail — attached automatically on upload</figcaption>
        </figure>
      )}

      {!token && !uploadId && (
        <>
          <p className="muted">Connect your Google account to publish this video to YouTube.</p>
          <button type="button" className="primary" onClick={connect} disabled={busy}>
            {busy ? 'Redirecting…' : 'Connect Google'}
          </button>
        </>
      )}

      {token && !uploadId && (
        <div className="upload-form">
          <label className="field">
            <span className="field-label">Title (prefilled from the video metadata — edit if needed)</span>
            <input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Description (optional)</span>
            <textarea
              value={description}
              maxLength={5000}
              rows={3}
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <div className="form-row">
            <label className="field">
              <span className="field-label">Tags (comma-separated, optional)</span>
              <input value={tags} onChange={(e) => setTags(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">Privacy</span>
              <select
                value={privacy}
                onChange={(e) => setPrivacy(e.target.value as typeof privacy)}
              >
                <option value="unlisted">unlisted</option>
                <option value="private">private</option>
                <option value="public">public</option>
              </select>
            </label>
          </div>
          <button type="button" className="primary" onClick={upload} disabled={busy}>
            {busy ? 'Starting…' : 'Upload to YouTube'}
          </button>
        </div>
      )}

      {uploadId && (
        <div className="upload-status">
          {uploading && (
            <>
              <p className="muted">
                {status?.status === 'PENDING' ? 'Waiting to start…' : `Uploading… ${progress}%`}
              </p>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${progress}%` }} />
              </div>
            </>
          )}
          {status?.status === 'COMPLETED' && status.video_url && (
            <p>
              ✅ Uploaded —{' '}
              <a href={status.video_url} target="_blank" rel="noreferrer">
                watch on YouTube
              </a>
            </p>
          )}
          {status?.status === 'FAILED' && (
            <>
              <p className="error-text">
                Upload failed ({status.error_code ?? 'unknown'}): {status.error_message ?? ''}
              </p>
              <button
                type="button"
                onClick={() => {
                  setUploadId(null)
                  if (status.error_code === 'invalid_token') {
                    clearToken()
                    setToken(null)
                  }
                }}
              >
                Try again
              </button>
            </>
          )}
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
    </section>
  )
}
