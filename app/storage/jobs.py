"""Job state boundary.

All job state lives behind JobRepository. The in-memory implementation is the
prototype default; swapping to Postgres/Redis means implementing this protocol
and changing one line of wiring in app.main.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Protocol

from app.domain.models import Job, JobStatus


class JobRepository(Protocol):
    async def create(self, job: Job) -> Job: ...

    async def get(self, job_id: str) -> Job | None: ...

    async def list(self, status: JobStatus | None = None) -> list[Job]: ...

    async def update(self, job_id: str, **fields: Any) -> Job | None: ...


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def create(self, job: Job) -> Job:
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Job | None:
        async with self._lock:
            job = self._jobs.get(job_id)
        return job.model_copy() if job else None

    async def list(self, status: JobStatus | None = None) -> list[Job]:
        async with self._lock:
            jobs = [j.model_copy() for j in self._jobs.values()]
        if status is not None:
            jobs = [j for j in jobs if j.status == status]
        return sorted(jobs, key=lambda j: j.created_at, reverse=True)

    async def update(self, job_id: str, **fields: Any) -> Job | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            updated = job.model_copy(
                update={**fields, "updated_at": datetime.now(timezone.utc)}
            )
            self._jobs[job_id] = updated
        return updated.model_copy()
