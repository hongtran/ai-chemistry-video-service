from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    use_stub_pipeline: bool = False
    environment: str = "dev"  # dev | staging | prod

    # Langfuse LLM cost/token tracking. Both keys empty => tracking disabled and
    # the service behaves exactly as before (see app/observability.py). Keys come
    # from a Langfuse Cloud (or self-hosted) project.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    # Accept either LANGFUSE_HOST or Langfuse's own LANGFUSE_BASE_URL env name.
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("LANGFUSE_HOST", "LANGFUSE_BASE_URL"),
    )

    llm_model: str = "gpt-4o"
    # Narration + scene split run warm enough to write well, cool enough to
    # obey the verbatim-captions rule. At OpenAI's default (1.0) the model
    # paraphrases and silently drops clauses, which alignment then rejects.
    llm_temperature: float = 0.5
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"  # fallback voice for any language without a mapping
    transcribe_model: str = "whisper-1"

    # Image generation for photo / photo-split frames (see pipeline/steps/images.py).
    # The IMAGE_GEN step calls this model with each scene's imagePrompt and embeds
    # the result as a data URI. images_enabled=false skips generation entirely
    # (frames keep their placeholder), so the pipeline runs image-API-free.
    image_model: str = "gpt-image-2"
    images_enabled: bool = True
    image_quality: str = "high"  # gpt-image-1: low | medium | high
    image_size_vertical: str = "1024x1536"   # portrait, for 9:16 videos
    image_size_horizontal: str = "1536x1024"  # landscape, for 16:9 videos
    # Cost/latency guard: at most this many images generated per video; extra
    # image scenes keep their placeholder.
    max_images_per_video: int = 10
    # How many image API calls run concurrently within a single video. Image
    # scenes are generated in parallel (bounded by this) instead of one-by-one,
    # so a video's images finish in roughly total/concurrency time.
    image_concurrency: int = 4

    # Vietnamese narration uses ElevenLabs instead of OpenAI TTS — noticeably
    # better Vietnamese prosody than gpt-4o-mini-tts. Every other language
    # keeps using OpenAI TTS (see tts_model above). Required only when a job
    # requests language="vi".
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "A5w1fw5x0uXded1LDvZp"
    elevenlabs_model_id: str = "eleven_v3"
    # Vietnamese transcription also uses ElevenLabs (Scribe) instead of Whisper
    # in prod — Whisper's Vietnamese word timestamps drive alignment, and its
    # accuracy on vi is poor enough to desync captions. See transcribe.py.
    elevenlabs_transcribe_model_id: str = "scribe_v2"

    # Default narration language (ISO 639-1); requests may override it.
    default_language: str = "en"
    # Per-language TTS voice. OpenAI's gpt-4o-mini-tts voices are all
    # multilingual and follow the input text's language, so this is purely
    # about picking a voice that sounds good for each language. Unmapped
    # languages fall back to `tts_voice` (see voice_for_language). Override
    # from the environment as JSON, e.g.
    #   TTS_VOICE_BY_LANGUAGE='{"en":"alloy","vi":"nova"}'
    tts_voice_by_language: dict[str, str] = {"en": "alloy", "vi": "nova"}
    tts_instructions_by_language: dict[str, str] = {
        "en": "Read the text verbatim, with natural pacing and intonation. Do not add or remove any words, and do not paraphrase.",
        "vi": "Đọc nguyên văn, với nhịp điệu và ngữ điệu tự nhiên. Không thêm hoặc bớt từ nào, và không diễn giải lại.",
    }

    # Per-call TTS budget, under OpenAI TTS's hard 4096-char input limit.
    # Longer scripts are chunked and ffmpeg-joined (see steps/tts.py).
    tts_max_chars: int = 4000
    # Content-validation attempts per per-scene authoring call before giving up.
    max_split_attempts: int = 4
    # Long-form (horizontal) scripts are segmented one sentence-window at a time
    # in Pass 1 so no single LLM call has to group a whole 100+ sentence script.
    # A vertical short (well under this) is segmented in a single call.
    segment_sentence_window: int = 40
    # Scenes authored together per Pass 2 call. Authoring scenes as a group lets
    # the model vary frame types (isolated per-scene calls repeated types); most
    # videos fit one batch, so the seam only appears on very long runs.
    author_batch_size: int = 12
    # Caption chunking. When on, one LLM call chunks every scene's paragraph at
    # semantic boundaries and returns caption strings; code validates each scene
    # reproduces its own words (else falls back to the greedy chunker), so the
    # verbatim / three-way-equality guarantee is unaffected. When off (or in stub
    # mode), captions use the pure-code greedy chunker (derive_captions) — a clean
    # revert path with identical output.
    semantic_captions_enabled: bool = False
    # Rounds of (compose -> layout gate); each failure re-authors only the
    # offending scene(s) and tries again.
    outer_retry_limit: int = 3
    layout_gate_timeout_seconds: int = 600

    hyperframes_dir: Path = Path("./render_kit")
    artifacts_dir: Path = Path("./artifacts")

    worker_concurrency: int = 1
    max_retries: int = 1
    # Headless-Chrome rendering runs at roughly 2x realtime at 1080p, and
    # build-video.sh also lints/validates/inspects first — so a 10-minute
    # long-form needs ~20+ minutes of wall clock. Sized for that worst case;
    # a vertical short finishes in a small fraction of it.
    render_timeout_seconds: int = 3600

    # Designed 1280x720 YouTube thumbnail, composed from the cover scene after
    # render. Best-effort: a failure logs a warning and the job still completes.
    thumbnail_enabled: bool = True
    thumbnail_timeout_seconds: int = 120
    # When no scene has a real image, make one images.generate call for a
    # topic-derived thumbnail background (else a flat accent gradient). Only fires
    # for image-less videos and is gated by images_enabled.
    thumbnail_generate_background: bool = True
    # Override for hyperframes' bundled chrome-headless-shell binary. Unset ->
    # resolved once via `npx hyperframes browser path`.
    chrome_headless_shell_path: Path | None = None

    max_query_length: int = 300
    # Script/narration input caps (script input mode), by video type: vertical is
    # the short single-pass flow (45-90s), horizontal the long-form one (5-10 min).
    max_script_length_short: int = 1200
    max_script_length_long: int = 9000

    # Single admin account. Both unset => auth disabled (dev/stub mode) with a
    # startup warning. Setting both requires a Bearer token on the videos API
    # (/api/v1/videos*); /auth/login and the YouTube routes stay open.
    admin_username: str = ""
    admin_password: str = ""
    # HMAC key for admin session tokens; falls back to admin_password.
    auth_secret: str = ""
    admin_session_ttl_seconds: int = 86400

    # Browser client (frontend/). Comma-separated origins allowed via CORS.
    cors_origins: str = "http://localhost:5173"
    # Where the OAuth callback sends the browser (tokens in the URL fragment)
    # when the login was started with mode=web. Empty falls back to JSON.
    frontend_oauth_redirect: str = "http://localhost:5173/oauth/callback"

    # YouTube upload. OAuth client from Google Cloud Console (type "Web
    # application", YouTube Data API v3 enabled, redirect URI registered
    # verbatim). Left empty, the /auth/google endpoints return a clear 500 and
    # the rest of the service keeps working credential-free.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    google_oauth_scopes: str = (
        "https://www.googleapis.com/auth/youtube.upload"
        " https://www.googleapis.com/auth/youtube"
    )
    # HMAC key for the stateless OAuth state param (CSRF); falls back to
    # google_client_secret when unset.
    oauth_state_secret: str = ""
    oauth_state_max_age_seconds: int = 600
    # Resumable upload chunk size — Google requires a multiple of 256 KiB.
    youtube_upload_chunk_bytes: int = 8 * 1024 * 1024
    # Read timeout for Google calls; a chunk PUT must finish within this.
    youtube_upload_timeout_seconds: int = 600
    # After a video publishes to YouTube, delete its job record + on-disk
    # artifacts (the video now lives on YouTube). The upload record — and its
    # video URL — is kept. Set false to retain the job/artifacts.
    clear_job_after_youtube_upload: bool = True

    @property
    def langfuse_enabled(self) -> bool:
        """Langfuse tracking is on only when both keys are configured."""
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    def voice_for_language(self, language: str) -> str:
        """TTS voice for a language, falling back to the global `tts_voice`."""
        return self.tts_voice_by_language.get(language, self.tts_voice)
