from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.models import Job, JobStatus, PipelineStep, UploadStatus, YouTubeUpload


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    expires_in: int


class CreateVideoRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    subject: Literal["chemistry", "tech"] = "chemistry"
    orientation: Literal["vertical", "horizontal"] = "vertical"


class CreateVideoResponse(BaseModel):
    id: str
    subject: str
    orientation: str
    status: JobStatus


class JobSummary(BaseModel):
    id: str
    query: str
    subject: str
    orientation: str
    status: JobStatus
    current_step: PipelineStep | None
    created_at: datetime

    @classmethod
    def from_job(cls, job: Job) -> "JobSummary":
        return cls(**job.model_dump(include=set(cls.model_fields)))


class JobDetail(BaseModel):
    id: str
    query: str
    subject: str
    orientation: str
    status: JobStatus
    current_step: PipelineStep | None
    error_message: str | None
    video_path: str | None
    created_at: datetime
    updated_at: datetime
    artifacts: list[str]

    @classmethod
    def from_job(cls, job: Job, artifacts: list[str]) -> "JobDetail":
        return cls(**job.model_dump(), artifacts=artifacts)


class CreateYouTubeUploadRequest(BaseModel):
    access_token: str = Field(min_length=1)
    # Omitted fields fall back to the job's meta.json (name/description/
    # hashtags/tags written by the compose step).
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None
    hashtags: list[str] | None = None
    privacy_status: Literal["public", "unlisted", "private"] = "unlisted"
    category_id: str = "28"
    playlist_id: str | None = None


class CreateYouTubeUploadResponse(BaseModel):
    upload_id: str
    job_id: str
    status: UploadStatus


class YouTubeUploadDetail(BaseModel):
    id: str
    job_id: str
    status: UploadStatus
    title: str
    description: str
    tags: list[str]
    privacy_status: str
    category_id: str
    playlist_id: str | None
    bytes_total: int
    bytes_sent: int
    video_id: str | None
    video_url: str | None
    playlist_added: bool | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_upload(cls, upload: YouTubeUpload) -> "YouTubeUploadDetail":
        return cls(**upload.model_dump())
