import {
  PIPELINE_STEPS,
  type InputMode,
  type JobStatus,
  type PipelineStep,
} from '../api/types'

const STEP_LABELS: Record<PipelineStep, string> = {
  narration: 'Narration',
  tts: 'Text-to-speech',
  transcription: 'Transcription',
  scene_split: 'Scene split',
  alignment: 'Alignment',
  compose: 'Compose',
  layout_gate: 'Layout gate',
  render: 'Render',
}

type StepState = 'done' | 'current' | 'failed' | 'pending'

function stateFor(
  steps: readonly PipelineStep[],
  step: PipelineStep,
  current: PipelineStep | null,
  status: JobStatus,
): StepState {
  if (status === 'COMPLETED') return 'done'
  const index = steps.indexOf(step)
  const currentIndex = current ? steps.indexOf(current) : -1
  if (index < currentIndex) return 'done'
  if (index === currentIndex) return status === 'FAILED' ? 'failed' : 'current'
  return 'pending'
}

const ICONS: Record<StepState, string> = {
  done: '✓',
  current: '●',
  failed: '✗',
  pending: '○',
}

export default function StepProgress({
  currentStep,
  status,
  inputMode,
}: {
  currentStep: PipelineStep | null
  status: JobStatus
  inputMode: InputMode
}) {
  // Script-mode jobs supply the narration, so the NARRATION step never runs.
  const steps =
    inputMode === 'script'
      ? PIPELINE_STEPS.filter((s) => s !== 'narration')
      : PIPELINE_STEPS
  return (
    <ol className="step-progress">
      {steps.map((step) => {
        const state = stateFor(steps, step, currentStep, status)
        return (
          <li key={step} className={`step ${state}`}>
            <span className="step-icon">{ICONS[state]}</span>
            <span className="step-label">{STEP_LABELS[step]}</span>
          </li>
        )
      })}
    </ol>
  )
}
