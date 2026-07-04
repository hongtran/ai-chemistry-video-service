from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.models import Job, JobStatus, PipelineStep


class CreateVideoRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class CreateVideoResponse(BaseModel):
    id: str
    status: JobStatus


class JobSummary(BaseModel):
    id: str
    query: str
    subject: str
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
