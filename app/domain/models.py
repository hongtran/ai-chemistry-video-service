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
    RENDER = "render"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Job(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    query: str
    subject: str = "chemistry"
    status: JobStatus = JobStatus.PENDING
    current_step: PipelineStep | None = None
    error_message: str | None = None
    video_path: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
