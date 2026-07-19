from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    use_stub_pipeline: bool = False

    llm_model: str = "gpt-4o"
    # Narration + scene split run warm enough to write well, cool enough to
    # obey the verbatim-captions rule. At OpenAI's default (1.0) the model
    # paraphrases and silently drops clauses, which alignment then rejects.
    llm_temperature: float = 0.5
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    transcribe_model: str = "whisper-1"

    # Per-call TTS budget, under OpenAI TTS's hard 4096-char input limit.
    # Longer scripts are chunked and ffmpeg-joined (see steps/tts.py).
    tts_max_chars: int = 4000
    # Content-validation attempts per scene-split section before giving up.
    max_split_attempts: int = 4
    # Rounds of (align -> compose -> layout gate); each failure re-splits only
    # the offending section(s) and tries again.
    outer_retry_limit: int = 3
    layout_gate_timeout_seconds: int = 300

    hyperframes_dir: Path = Path("./render_kit")
    artifacts_dir: Path = Path("./artifacts")

    worker_concurrency: int = 1
    max_retries: int = 1
    # Headless-Chrome rendering runs at roughly 2x realtime at 1080p, and
    # build-video.sh also lints/validates/inspects first — so a 10-minute
    # long-form needs ~20+ minutes of wall clock. Sized for that worst case;
    # a vertical short finishes in a small fraction of it.
    render_timeout_seconds: int = 1800

    max_query_length: int = 300

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
