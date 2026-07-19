import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import HTTPException

from app.api.schemas import CreateYouTubeUploadRequest
from app.api.youtube_router import get_youtube_upload, upload_to_youtube
from app.config import Settings
from app.domain.models import Job, JobStatus, UploadStatus
from app.storage.artifacts import LocalArtifactStore
from app.storage.jobs import InMemoryJobRepository
from app.storage.uploads import InMemoryUploadRepository
from app.worker.uploads import UploadRunner
from app.youtube.client import YouTubeUploader
from app.youtube.oauth import GoogleOAuth

SESSION_URL = "https://upload.example/session-1"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"


def _google_handler(request: httpx.Request) -> httpx.Response:
    """One MockTransport playing tokeninfo + resumable upload + playlists."""
    if request.url.path == "/oauth2/v3/tokeninfo":
        if request.url.params["access_token"] == "good-token":
            return httpx.Response(200, json={"scope": f"openid {UPLOAD_SCOPE}"})
        return httpx.Response(400, json={"error_description": "Invalid Value"})
    if request.url.path == "/upload/youtube/v3/videos":
        return httpx.Response(200, headers={"Location": SESSION_URL})
    if str(request.url).startswith(SESSION_URL):
        return httpx.Response(200, json={"id": "abc123"})
    if request.url.path == "/youtube/v3/playlistItems":
        return httpx.Response(200, json={"id": "pl-item"})
    raise AssertionError(f"unexpected request: {request.method} {request.url}")


class UploadApiHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.artifacts = LocalArtifactStore(Path(self._tmp.name))
        self.jobs = InMemoryJobRepository()
        self.uploads = InMemoryUploadRepository()
        client = httpx.AsyncClient(transport=httpx.MockTransport(_google_handler))
        settings = Settings(google_client_id="cid", google_client_secret="csecret")
        self.oauth = GoogleOAuth(settings, client)
        self.runner = UploadRunner(self.uploads, YouTubeUploader(client, 1024 * 1024))

    async def asyncTearDown(self) -> None:
        await self.runner.stop()
        self._tmp.cleanup()

    def _request(self) -> SimpleNamespace:
        return SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    jobs=self.jobs,
                    artifacts=self.artifacts,
                    uploads=self.uploads,
                    upload_runner=self.runner,
                    oauth=self.oauth,
                )
            )
        )

    async def _seed_completed_job(self, meta: dict | None = None) -> Job:
        job = Job(query="What is RAG?", subject="tech", status=JobStatus.COMPLETED)
        video_path = self.artifacts.save_bytes(job.id, "video.mp4", b"\x00" * 4096)
        job = job.model_copy(update={"video_path": str(video_path)})
        await self.jobs.create(job)
        if meta is not None:
            self.artifacts.save_json(job.id, "meta.json", meta)
        return job


class UploadToYouTubeTests(UploadApiHarness):
    async def test_unknown_job_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            await upload_to_youtube(
                "nope", CreateYouTubeUploadRequest(access_token="good-token"), self._request()
            )
        self.assertEqual(caught.exception.status_code, 404)

    async def test_incomplete_job_returns_409(self) -> None:
        job = Job(query="q", status=JobStatus.PROCESSING)
        await self.jobs.create(job)
        with self.assertRaises(HTTPException) as caught:
            await upload_to_youtube(
                job.id, CreateYouTubeUploadRequest(access_token="good-token"), self._request()
            )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail["status"], "PROCESSING")

    async def test_missing_video_file_returns_404(self) -> None:
        job = Job(query="q", status=JobStatus.COMPLETED, video_path="/gone/video.mp4")
        await self.jobs.create(job)
        with self.assertRaises(HTTPException) as caught:
            await upload_to_youtube(
                job.id, CreateYouTubeUploadRequest(access_token="good-token"), self._request()
            )
        self.assertEqual(caught.exception.status_code, 404)

    async def test_invalid_token_returns_401_before_any_upload(self) -> None:
        job = await self._seed_completed_job()
        with self.assertRaises(HTTPException) as caught:
            await upload_to_youtube(
                job.id, CreateYouTubeUploadRequest(access_token="expired"), self._request()
            )
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(await self.uploads.list(), [])

    async def test_accepted_upload_completes_with_video_url(self) -> None:
        job = await self._seed_completed_job(
            meta={"name": "What is RAG?", "description": "About RAG.", "hashtags": ["ai"]}
        )
        response = await upload_to_youtube(
            job.id, CreateYouTubeUploadRequest(access_token="good-token"), self._request()
        )
        self.assertEqual(response.status, UploadStatus.PENDING)

        await self.runner.join()
        detail = await get_youtube_upload(response.upload_id, self._request())
        self.assertEqual(detail.status, UploadStatus.COMPLETED)
        self.assertEqual(detail.video_url, "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(detail.bytes_sent, detail.bytes_total)
        self.assertEqual(detail.title, "What is RAG?")
        self.assertEqual(detail.description, "About RAG.\n\n#ai")

    async def test_body_overrides_beat_meta_json(self) -> None:
        job = await self._seed_completed_job(
            meta={
                "name": "meta title",
                "description": "meta desc",
                "hashtags": ["metatag"],
                "tags": ["meta"],
            }
        )
        response = await upload_to_youtube(
            job.id,
            CreateYouTubeUploadRequest(
                access_token="good-token",
                title="Body title",
                description="Body desc",
                hashtags=["bodytag"],
                tags=["body"],
                privacy_status="private",
            ),
            self._request(),
        )
        upload = await self.uploads.get(response.upload_id)
        self.assertEqual(upload.title, "Body title")
        self.assertEqual(upload.description, "Body desc\n\n#bodytag")
        self.assertEqual(upload.tags, ["body"])
        self.assertEqual(upload.privacy_status, "private")
        await self.runner.join()

    async def test_no_meta_json_falls_back_to_subject_and_id(self) -> None:
        job = await self._seed_completed_job()
        response = await upload_to_youtube(
            job.id, CreateYouTubeUploadRequest(access_token="good-token"), self._request()
        )
        upload = await self.uploads.get(response.upload_id)
        self.assertEqual(upload.title, f"tech-{job.id}")
        await self.runner.join()

    async def test_upload_failure_is_recorded_not_raised(self) -> None:
        def failing(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/v3/tokeninfo":
                return httpx.Response(200, json={"scope": UPLOAD_SCOPE})
            return httpx.Response(
                403,
                json={
                    "error": {
                        "message": "quota exceeded",
                        "errors": [{"reason": "quotaExceeded"}],
                    }
                },
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(failing))
        self.oauth = GoogleOAuth(
            Settings(google_client_id="cid", google_client_secret="csecret"), client
        )
        self.runner = UploadRunner(self.uploads, YouTubeUploader(client, 1024))

        job = await self._seed_completed_job()
        response = await upload_to_youtube(
            job.id, CreateYouTubeUploadRequest(access_token="good-token"), self._request()
        )
        await self.runner.join()
        detail = await get_youtube_upload(response.upload_id, self._request())
        self.assertEqual(detail.status, UploadStatus.FAILED)
        self.assertEqual(detail.error_code, "quota_exceeded")
        self.assertIsNone(detail.video_url)

    async def test_playlist_added_after_upload(self) -> None:
        job = await self._seed_completed_job()
        response = await upload_to_youtube(
            job.id,
            CreateYouTubeUploadRequest(access_token="good-token", playlist_id="PL123"),
            self._request(),
        )
        await self.runner.join()
        detail = await get_youtube_upload(response.upload_id, self._request())
        self.assertEqual(detail.status, UploadStatus.COMPLETED)
        self.assertTrue(detail.playlist_added)


if __name__ == "__main__":
    unittest.main()
