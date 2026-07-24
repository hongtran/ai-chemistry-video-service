from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PipelineStep(str, Enum):
    NARRATION = "narration"
    # Pass 1: LLM groups the script's sentences into semantic scenes; code
    # derives the caption chunks. Pass 2: LLM authors each scene's typed data.
    SEGMENT = "segment"
    TTS = "tts"
    TRANSCRIPTION = "transcription"
    AUTHORING = "authoring"
    # Generate a real picture for any image frame (photo / photo-split) from the
    # imagePrompt the authoring step wrote; embeds it as a data URI.
    IMAGE_GEN = "image_gen"
    ALIGNMENT = "alignment"
    COMPOSE = "compose"
    LAYOUT_GATE = "layout_gate"
    RENDER = "render"
    # Compose a designed 1280x720 YouTube thumbnail from the cover scene; runs
    # last and is best-effort (a failure never fails the job).
    THUMBNAIL = "thumbnail"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    # "topic" → LLM writes the narration from `query`; "script" → `script` holds
    # the user-supplied narration and the NARRATION step is skipped. In script
    # mode `query` carries a short title derived from the script (for display,
    # compose and meta).
    input_mode: str = "topic"
    query: str
    script: str | None = None
    subject: str = "lab-management"
    orientation: str = "vertical"
    language: str = "en"
    status: JobStatus = JobStatus.PENDING
    current_step: PipelineStep | None = None
    error_message: str | None = None
    video_path: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class UploadStatus(str, Enum):
    PENDING = "PENDING"
    UPLOADING = "UPLOADING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class YouTubeUpload(BaseModel):
    """One YouTube publish attempt for a completed job's video.mp4.

    The client's access token is deliberately not stored here — it is passed
    by value into the upload task so it can never leak through the status API.
    Metadata is snapshotted at request time (body overrides meta.json).
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str
    status: UploadStatus = UploadStatus.PENDING
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy_status: str = "unlisted"
    category_id: str = "28"
    playlist_id: str | None = None
    bytes_total: int = 0
    bytes_sent: int = 0
    video_id: str | None = None
    video_url: str | None = None
    playlist_added: bool | None = None
    # None = no thumbnail was attempted (none generated); True/False = set / failed.
    thumbnail_set: bool | None = None
    # invalid_token | quota_exceeded | upload_failed | network_error
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
