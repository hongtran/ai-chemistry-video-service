// Mirrors app/api/schemas.py and app/domain/models.py.

export type JobStatus = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED'
export type UploadStatus = 'PENDING' | 'UPLOADING' | 'COMPLETED' | 'FAILED'

export const PIPELINE_STEPS = [
  'narration',
  'tts',
  'transcription',
  'scene_split',
  'alignment',
  'compose',
  'layout_gate',
  'render',
] as const
export type PipelineStep = (typeof PIPELINE_STEPS)[number]

export type Subject = 'chemistry' | 'tech'
export type Orientation = 'vertical' | 'horizontal'
export const SUBJECTS: Subject[] = ['chemistry', 'tech']

// Mirrors SUPPORTED_LANGUAGES in app/languages.py.
export type Language = 'en' | 'vi'
export const LANGUAGES: { value: Language; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'vi', label: 'Tiếng Việt' },
]

export interface LoginResponse {
  token: string
  token_type: string
  expires_in: number
}

export interface CreateVideoRequest {
  query: string
  subject: Subject
  orientation: Orientation
  language: Language
}

export interface CreateVideoResponse {
  id: string
  subject: string
  orientation: string
  language: string
  status: JobStatus
}

export interface JobSummary {
  id: string
  query: string
  subject: string
  orientation: string
  language: string
  status: JobStatus
  current_step: PipelineStep | null
  created_at: string
}

export interface JobDetail extends JobSummary {
  error_message: string | null
  video_path: string | null
  updated_at: string
  artifacts: string[]
}

export interface CreateYouTubeUploadRequest {
  access_token: string
  title?: string
  description?: string
  tags?: string[]
  hashtags?: string[]
  privacy_status?: 'public' | 'unlisted' | 'private'
  category_id?: string
  playlist_id?: string
}

export interface CreateYouTubeUploadResponse {
  upload_id: string
  job_id: string
  status: UploadStatus
}

export interface YouTubeUploadDetail {
  id: string
  job_id: string
  status: UploadStatus
  title: string
  description: string
  tags: string[]
  privacy_status: string
  category_id: string
  playlist_id: string | null
  bytes_total: number
  bytes_sent: number
  video_id: string | null
  video_url: string | null
  playlist_added: boolean | null
  error_code: string | null
  error_message: string | null
  created_at: string
  updated_at: string
}

// Router-enforced cap (settings.max_query_length), tighter than the schema's 1000.
export const MAX_QUERY_LENGTH = 300
