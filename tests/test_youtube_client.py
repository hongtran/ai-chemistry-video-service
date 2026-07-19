import tempfile
import unittest
from pathlib import Path

import httpx

from app.youtube.client import (
    UploadMetadata,
    YouTubeAuthError,
    YouTubeQuotaError,
    YouTubeUploader,
    YouTubeUploadError,
    append_hashtags,
)

SESSION_URL = "https://upload.example/session-1"

META = UploadMetadata(
    title="Title",
    description="Desc",
    tags=["a", "b"],
    category_id="28",
    privacy_status="unlisted",
    notify_subscribers=False,
)


def _video_file(tmp: str, size: int) -> Path:
    path = Path(tmp) / "video.mp4"
    path.write_bytes(bytes(range(256)) * (size // 256) + bytes(size % 256))
    return path


class AppendHashtagsTests(unittest.TestCase):
    def test_appended_as_trailing_line_with_hash_prefix(self) -> None:
        self.assertEqual(append_hashtags("Desc", ["ai", "#rag"]), "Desc\n\n#ai #rag")

    def test_empty_description_yields_bare_line(self) -> None:
        self.assertEqual(append_hashtags("", ["ai"]), "#ai")

    def test_no_hashtags_leaves_description_alone(self) -> None:
        self.assertEqual(append_hashtags("Desc", []), "Desc")


class UploaderTests(unittest.IsolatedAsyncioTestCase):
    def _uploader(self, handler, chunk_bytes: int = 1024) -> YouTubeUploader:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return YouTubeUploader(client, chunk_bytes)

    async def test_single_chunk_upload_returns_video_id(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.method == "POST":
                assert request.url.params["uploadType"] == "resumable"
                assert request.headers["X-Upload-Content-Length"] == "500"
                return httpx.Response(200, headers={"Location": SESSION_URL})
            assert request.headers["Content-Range"] == "bytes 0-499/500"
            return httpx.Response(200, json={"id": "abc123"})

        with tempfile.TemporaryDirectory() as tmp:
            video = _video_file(tmp, 500)
            video_id = await self._uploader(handler).upload(video, META, "tok")

        self.assertEqual(video_id, "abc123")
        self.assertEqual(seen[0].headers["Authorization"], "Bearer tok")

    async def test_chunked_upload_resumes_from_confirmed_offset(self) -> None:
        ranges: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(200, headers={"Location": SESSION_URL})
            ranges.append(request.headers["Content-Range"])
            if len(ranges) == 1:
                # Google only kept 512 of the first 1024 bytes.
                return httpx.Response(308, headers={"Range": "bytes=0-511"})
            if len(ranges) == 2:
                return httpx.Response(308, headers={"Range": "bytes=0-1535"})
            return httpx.Response(200, json={"id": "vid42"})

        progress: list[int] = []

        async def on_progress(sent: int) -> None:
            progress.append(sent)

        with tempfile.TemporaryDirectory() as tmp:
            video = _video_file(tmp, 2000)
            video_id = await self._uploader(handler).upload(
                video, META, "tok", on_progress
            )

        self.assertEqual(video_id, "vid42")
        self.assertEqual(
            ranges,
            ["bytes 0-1023/2000", "bytes 512-1535/2000", "bytes 1536-1999/2000"],
        )
        self.assertEqual(progress, [512, 1536, 2000])

    async def test_401_raises_auth_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401, json={"error": {"message": "Invalid Credentials"}}
            )

        with tempfile.TemporaryDirectory() as tmp:
            video = _video_file(tmp, 10)
            with self.assertRaises(YouTubeAuthError):
                await self._uploader(handler).upload(video, META, "bad")

    async def test_403_quota_raises_quota_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={
                    "error": {
                        "message": "quota exceeded",
                        "errors": [{"reason": "quotaExceeded"}],
                    }
                },
            )

        with tempfile.TemporaryDirectory() as tmp:
            video = _video_file(tmp, 10)
            with self.assertRaises(YouTubeQuotaError):
                await self._uploader(handler).upload(video, META, "tok")

    async def test_missing_location_header_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        with tempfile.TemporaryDirectory() as tmp:
            video = _video_file(tmp, 10)
            with self.assertRaises(YouTubeUploadError):
                await self._uploader(handler).upload(video, META, "tok")


if __name__ == "__main__":
    unittest.main()
