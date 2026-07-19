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
    TTS = "tts"
    TRANSCRIPTION = "transcription"
    SCENE_SPLIT = "scene_split"
    ALIGNMENT = "alignment"
    COMPOSE = "compose"
    LAYOUT_GATE = "layout_gate"
    RENDER = "render"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    subject: str = "chemistry"
    orientation: str = "vertical"
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
    # invalid_token | quota_exceeded | upload_failed | network_error
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
