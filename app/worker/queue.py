"""In-process job queue boundary.

AsyncioJobQueue runs N worker tasks inside the FastAPI event loop (default
concurrency 1 — rendering is heavy). Swap for a Celery/SQS-backed queue by
implementing the same protocol; the API and pipeline never touch asyncio.Queue
directly.
"""
import asyncio
import logging
from typing import Protocol

from app.domain.models import JobStatus
from app.pipeline.base import VideoPipeline
from app.storage.jobs import JobRepository

logger = logging.getLogger(__name__)


class JobQueue(Protocol):
    async def enqueue(self, job_id: str) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class AsyncioJobQueue:
    def __init__(
        self,
        pipeline: VideoPipeline,
        jobs: JobRepository,
        concurrency: int = 1,
    ) -> None:
        self._pipeline = pipeline
        self._jobs = jobs
        self._concurrency = concurrency
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def start(self) -> None:
        self._workers = [
            asyncio.create_task(self._worker_loop(i), name=f"job-worker-{i}")
            for i in range(self._concurrency)
        ]

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._pipeline.run(job_id)
            except asyncio.CancelledError:
                # Shutdown mid-job: leave the job as-is; in-memory state dies
                # with the process anyway (accepted prototype behavior).
                raise
            except Exception as exc:  # noqa: BLE001 — worker must never die
                logger.exception("worker %d: pipeline crashed for job %s", worker_id, job_id)
                await self._jobs.update(
                    job_id,
                    status=JobStatus.FAILED,
                    error_message=f"internal: unexpected pipeline error: {exc}",
                )
            finally:
                self._queue.task_done()
