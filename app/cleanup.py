"""Job deletion — shared by the DELETE endpoint and the post-upload auto-clear.

Deleting a job means dropping its in-memory record AND its on-disk artifacts
(video.mp4, audio, transcript, ...). YouTube upload records are intentionally
NOT touched: after an auto-clear the upload record is what still holds the
published video's URL.
"""
import logging

from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository

logger = logging.getLogger(__name__)


async def purge_job(
    job_id: str, jobs: JobRepository, artifacts: ArtifactStore
) -> bool:
    """Delete a job's record and its artifact directory. Returns True if the
    record existed. Artifacts are removed either way, so a partially-created
    job can't leave an orphaned directory behind."""
    existed = await jobs.delete(job_id)
    artifacts.delete_all(job_id)
    return existed
