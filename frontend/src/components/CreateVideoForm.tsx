import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, createVideo } from '../api/client'
import {
  LANGUAGES,
  MAX_QUERY_LENGTH,
  MAX_SCRIPT_LENGTH,
  SUBJECTS,
  type InputMode,
  type Language,
  type Orientation,
  type Subject,
} from '../api/types'

// The API has no short/long field — vertical is the short single-pass flow
// (45-90s) and horizontal the long-form sectioned one (5-10 min).
const VIDEO_TYPES: { label: string; hint: string; orientation: Orientation }[] = [
  { label: 'Short', hint: 'vertical · 45–90s', orientation: 'vertical' },
  { label: 'Long', hint: 'horizontal · 5–10 min', orientation: 'horizontal' },
]

const INPUT_MODES: { label: string; hint: string; mode: InputMode }[] = [
  { label: 'Topic', hint: 'AI writes the script', mode: 'topic' },
  { label: 'Script', hint: 'use your own narration', mode: 'script' },
]

export default function CreateVideoForm() {
  const navigate = useNavigate()
  const [inputMode, setInputMode] = useState<InputMode>('topic')
  const [subject, setSubject] = useState<Subject>('chemistry')
  const [orientation, setOrientation] = useState<Orientation>('vertical')
  const [language, setLanguage] = useState<Language>('en')
  const [query, setQuery] = useState('')
  const [script, setScript] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const scriptCap = MAX_SCRIPT_LENGTH[orientation]
  const active = inputMode === 'topic' ? query : script
  const canSubmit = active.trim().length > 0 && !submitting

  // Long→Short shrinks the script cap; trim so the counter and submit stay valid.
  const changeOrientation = (next: Orientation) => {
    setOrientation(next)
    setScript((s) => s.slice(0, MAX_SCRIPT_LENGTH[next]))
  }

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const job = await createVideo(
        inputMode === 'topic'
          ? { input_mode: 'topic', query: query.trim(), subject, orientation, language }
          : { input_mode: 'script', script: script.trim(), subject, orientation, language },
      )
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      // 400 = the subject guard rejected an off-topic query (topic mode) or an
      // over-cap/empty input; show it inline.
      setError(err instanceof ApiError ? err.message : 'Request failed — is the backend running?')
      setSubmitting(false)
    }
  }

  return (
    <form className="card create-form" onSubmit={submit}>
      <h2>Create a video</h2>

      <div className="field">
        <span className="field-label">Input</span>
        <div className="toggle-group">
          {INPUT_MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              className={`toggle ${inputMode === m.mode ? 'active' : ''}`}
              onClick={() => setInputMode(m.mode)}
              title={m.hint}
            >
              {m.label}
              <small>{m.hint}</small>
            </button>
          ))}
        </div>
      </div>

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

        <label className="field">
          <span className="field-label">Language</span>
          <select value={language} onChange={(e) => setLanguage(e.target.value as Language)}>
            {LANGUAGES.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
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
                onClick={() => changeOrientation(t.orientation)}
                title={t.hint}
              >
                {t.label}
                <small>{t.hint}</small>
              </button>
            ))}
          </div>
        </div>
      </div>

      {inputMode === 'topic' ? (
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
      ) : (
        <label className="field">
          <span className="field-label">Script</span>
          <textarea
            value={script}
            maxLength={scriptCap}
            rows={10}
            placeholder="Paste your narration — this text is spoken verbatim (no AI rewrite)."
            onChange={(e) => setScript(e.target.value)}
          />
          <span className="char-counter">
            {script.length}/{scriptCap}
          </span>
        </label>
      )}

      {error && <p className="error-text">{error}</p>}

      <button type="submit" className="primary" disabled={!canSubmit}>
        {submitting ? 'Creating…' : 'Generate video'}
      </button>
    </form>
  )
}
