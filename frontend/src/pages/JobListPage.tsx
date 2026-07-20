import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, deleteJob, listVideos } from '../api/client'
import CreateVideoForm from '../components/CreateVideoForm'
import type { JobStatus, JobSummary } from '../api/types'

const STATUS_CLASS: Record<JobStatus, string> = {
  PENDING: 'badge pending',
  PROCESSING: 'badge processing',
  COMPLETED: 'badge completed',
  FAILED: 'badge failed',
}

export default function JobListPage() {
  const navigate = useNavigate()
  // Fetch the list once on mount; no polling. Revisit the page (or reload) to refresh.
  const [jobs, setJobs] = useState<JobSummary[] | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    listVideos()
      .then((result) => !cancelled && setJobs(result))
      .catch((err) => !cancelled && setError(err as Error))
    return () => {
      cancelled = true
    }
  }, [])

  const remove = async (event: React.MouseEvent, job: JobSummary) => {
    event.stopPropagation() // don't trigger the row's navigate
    if (deletingId) return
    if (!window.confirm(`Delete this job and its files?\n\n${job.query}`)) return
    setDeletingId(job.id)
    try {
      await deleteJob(job.id)
      setJobs((current) => (current ? current.filter((j) => j.id !== job.id) : current))
    } catch (err) {
      // A 404 means it's already gone — drop it from the list anyway.
      if (err instanceof ApiError && err.status === 404) {
        setJobs((current) => (current ? current.filter((j) => j.id !== job.id) : current))
      } else {
        window.alert(err instanceof Error ? err.message : 'Delete failed.')
      }
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="page">
      <CreateVideoForm />

      <section className="card">
        <h2>Jobs</h2>
        {error && <p className="error-text">Could not load jobs — is the backend running?</p>}
        {jobs && jobs.length === 0 && (
          <p className="muted">No jobs yet. Jobs are kept in memory and reset when the server restarts.</p>
        )}
        {jobs && jobs.length > 0 && (
          <table className="job-table">
            <thead>
              <tr>
                <th>Topic</th>
                <th>Subject</th>
                <th>Type</th>
                <th>Status</th>
                <th>Step</th>
                <th>Created</th>
                <th aria-label="Actions"></th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id} onClick={() => navigate(`/jobs/${job.id}`)}>
                  <td className="job-query">{job.query}</td>
                  <td>{job.subject}</td>
                  <td>{job.orientation === 'vertical' ? 'Short' : 'Long'}</td>
                  <td>
                    <span className={STATUS_CLASS[job.status]}>{job.status}</span>
                  </td>
                  <td>{job.current_step ?? '—'}</td>
                  <td>{new Date(job.created_at).toLocaleString()}</td>
                  <td>
                    <button
                      type="button"
                      className="danger-link"
                      onClick={(e) => remove(e, job)}
                      disabled={deletingId === job.id}
                      title="Delete job"
                    >
                      {deletingId === job.id ? 'Deleting…' : 'Delete'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
