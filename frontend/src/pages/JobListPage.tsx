import { useNavigate } from 'react-router-dom'
import { listVideos } from '../api/client'
import { usePolling } from '../hooks/usePolling'
import CreateVideoForm from '../components/CreateVideoForm'
import type { JobStatus } from '../api/types'

const STATUS_CLASS: Record<JobStatus, string> = {
  PENDING: 'badge pending',
  PROCESSING: 'badge processing',
  COMPLETED: 'badge completed',
  FAILED: 'badge failed',
}

export default function JobListPage() {
  const navigate = useNavigate()
  const { data: jobs, error } = usePolling(listVideos, 5000, true)

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
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
