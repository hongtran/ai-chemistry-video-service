import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, downloadVideo, getJob } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import StepProgress from '../components/StepProgress'
import YouTubeUploadPanel from '../components/YouTubeUploadPanel'

export default function JobDetailPage() {
  const { id } = useParams<{ id: string }>()
  const jobId = id!

  const fetchJob = useCallback(() => getJob(jobId), [jobId])
  const [stopped, setStopped] = useState(false)
  const { data: job, error } = usePolling(fetchJob, 2500, !stopped)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const download = async () => {
    if (!job || downloading) return
    setDownloading(true)
    setDownloadError(null)
    try {
      await downloadVideo(job.id, `${job.subject}-${job.id}.mp4`)
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : 'Download failed.')
    } finally {
      setDownloading(false)
    }
  }

  // Stop polling once the job is terminal or gone.
  useEffect(() => {
    if (job && (job.status === 'COMPLETED' || job.status === 'FAILED')) setStopped(true)
    if (error instanceof ApiError && error.status === 404) setStopped(true)
  }, [job, error])

  if (error instanceof ApiError && error.status === 404) {
    return (
      <div className="page">
        <div className="card">
          <p className="error-text">
            Job not found. The server keeps jobs in memory — it may have restarted.
          </p>
          <Link to="/">← Back to jobs</Link>
        </div>
      </div>
    )
  }

  if (!job) {
    return (
      <div className="page">
        <div className="card muted">
          {error ? `Could not load job: ${error.message}` : 'Loading…'}
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <Link to="/" className="back-link">
        ← All jobs
      </Link>
      <div className="card">
        <h2 className="job-query">{job.query}</h2>
        <p className="muted">
          {job.subject} · {job.orientation === 'vertical' ? 'Short (vertical)' : 'Long (horizontal)'}{' '}
          · created {new Date(job.created_at).toLocaleString()}
        </p>
        <StepProgress currentStep={job.current_step} status={job.status} />
        {job.status === 'FAILED' && (
          <p className="error-text">Pipeline failed — {job.error_message ?? 'unknown error'}</p>
        )}
        {job.status === 'COMPLETED' ? (
          <>
            <button type="button" className="primary" onClick={download} disabled={downloading}>
              {downloading ? 'Downloading…' : '⬇ Download video'}
            </button>
            {downloadError && <p className="error-text">{downloadError}</p>}
          </>
        ) : (
          job.status !== 'FAILED' && (
            <p className="muted">Video will be downloadable when the pipeline completes.</p>
          )
        )}
      </div>
      {job.status === 'COMPLETED' && <YouTubeUploadPanel jobId={job.id} />}
    </div>
  )
}
