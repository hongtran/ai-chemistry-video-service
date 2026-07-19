import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, createVideo } from '../api/client'
import { MAX_QUERY_LENGTH, SUBJECTS, type Orientation, type Subject } from '../api/types'

// The API has no short/long field — vertical is the short single-pass flow
// (45-90s) and horizontal the long-form sectioned one (5-10 min).
const VIDEO_TYPES: { label: string; hint: string; orientation: Orientation }[] = [
  { label: 'Short', hint: 'vertical · 45–90s', orientation: 'vertical' },
  { label: 'Long', hint: 'horizontal · 5–10 min', orientation: 'horizontal' },
]

export default function CreateVideoForm() {
  const navigate = useNavigate()
  const [subject, setSubject] = useState<Subject>('chemistry')
  const [orientation, setOrientation] = useState<Orientation>('vertical')
  const [query, setQuery] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!query.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const job = await createVideo({ query: query.trim(), subject, orientation })
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      // 400 = the subject guard rejected an off-topic query; show it inline.
      setError(err instanceof ApiError ? err.message : 'Request failed — is the backend running?')
      setSubmitting(false)
    }
  }

  return (
    <form className="card create-form" onSubmit={submit}>
      <h2>Create a video</h2>

      <div className="form-row">
        <label className="field">
          <span className="field-label">Subject</span>
          <select value={subject} onChange={(e) => setSubject(e.target.value as Subject)}>
            {SUBJECTS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <div className="field">
          <span className="field-label">Video type</span>
          <div className="toggle-group">
            {VIDEO_TYPES.map((t) => (
              <button
                key={t.orientation}
                type="button"
                className={`toggle ${orientation === t.orientation ? 'active' : ''}`}
                onClick={() => setOrientation(t.orientation)}
                title={t.hint}
              >
                {t.label}
                <small>{t.hint}</small>
              </button>
            ))}
          </div>
        </div>
      </div>

      <label className="field">
        <span className="field-label">Topic</span>
        <textarea
          value={query}
          maxLength={MAX_QUERY_LENGTH}
          rows={3}
          placeholder={`e.g. How to "FIX" your AI from hallucination`}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="char-counter">
          {query.length}/{MAX_QUERY_LENGTH}
        </span>
      </label>

      {error && <p className="error-text">{error}</p>}

      <button type="submit" className="primary" disabled={!query.trim() || submitting}>
        {submitting ? 'Creating…' : 'Generate video'}
      </button>
    </form>
  )
}
