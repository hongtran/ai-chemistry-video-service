import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from app.api.router import delete_video_job
from app.cleanup import purge_job
from app.domain.models import Job, JobStatus
from app.storage.artifacts import LocalArtifactStore
from app.storage.jobs import InMemoryJobRepository


class DeleteHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.artifacts = LocalArtifactStore(Path(self._tmp.name))
        self.jobs = InMemoryJobRepository()

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    def _request(self) -> SimpleNamespace:
        return SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    jobs=self.jobs, artifacts=self.artifacts, queue=None, guard=None
                )
            )
        )

    async def _seed(self) -> Job:
        job = Job(query="What is RAG?", subject="tech", status=JobStatus.COMPLETED)
        self.artifacts.save_bytes(job.id, "video.mp4", b"\x00" * 32)
        self.artifacts.save_text(job.id, "script.txt", "hi")
        await self.jobs.create(job)
        return job


class RepoAndStoreTests(DeleteHarness):
    async def test_repo_delete_returns_true_then_false(self) -> None:
        job = await self._seed()
        self.assertTrue(await self.jobs.delete(job.id))
        self.assertIsNone(await self.jobs.get(job.id))
        self.assertFalse(await self.jobs.delete(job.id))

    def test_delete_all_removes_the_job_dir(self) -> None:
        self.artifacts.save_bytes("job-1", "video.mp4", b"x")
        self.assertTrue((Path(self._tmp.name) / "job-1").is_dir())
        self.artifacts.delete_all("job-1")
        self.assertFalse((Path(self._tmp.name) / "job-1").exists())

    def test_delete_all_is_a_noop_for_missing_dir(self) -> None:
        self.artifacts.delete_all("never-existed")  # must not raise

    def test_delete_all_refuses_traversing_job_id(self) -> None:
        outside = Path(self._tmp.name).parent / "keep.txt"
        outside.write_text("keep")
        try:
            self.artifacts.delete_all("..")  # would resolve to the parent dir
            self.assertTrue(outside.exists())
        finally:
            outside.unlink(missing_ok=True)

    async def test_purge_job_drops_record_and_artifacts(self) -> None:
        job = await self._seed()
        existed = await purge_job(job.id, self.jobs, self.artifacts)
        self.assertTrue(existed)
        self.assertIsNone(await self.jobs.get(job.id))
        self.assertEqual(self.artifacts.list_names(job.id), [])


class DeleteEndpointTests(DeleteHarness):
    async def test_delete_endpoint_removes_job_and_returns_204(self) -> None:
        job = await self._seed()
        response = await delete_video_job(job.id, self._request())
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(await self.jobs.get(job.id))
        self.assertEqual(self.artifacts.list_names(job.id), [])

    async def test_delete_unknown_job_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            await delete_video_job("nope", self._request())
        self.assertEqual(caught.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
