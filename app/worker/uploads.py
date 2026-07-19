"""Background YouTube upload runner.

Deliberately not AsyncioJobQueue: that queue is pipeline-coupled and
serializes on worker_concurrency (rendering is heavy); uploads are I/O-bound
and must not wait behind renders. Each submit spawns one tracked task.
"""
import asyncio
import logging
from pathlib import Path

import httpx

from app.domain.models import UploadStatus
from app.storage.uploads import UploadRepository
from app.youtube.client import (
    UploadMetadata,
    YouTubeAuthError,
    YouTubeQuotaError,
    YouTubeUploader,
)

logger = logging.getLogger(__name__)


class UploadRunner:
    def __init__(self, uploads: UploadRepository, uploader: YouTubeUploader) -> None:
        self._uploads = uploads
        self._uploader = uploader
        self._tasks: set[asyncio.Task[None]] = set()

    def submit(self, upload_id: str, access_token: str, video_path: Path) -> None:
        task = asyncio.create_task(
            self._run(upload_id, access_token, video_path),
            name=f"yt-upload-{upload_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def join(self) -> None:
        """Wait for all in-flight uploads (tests and graceful drains)."""
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run(self, upload_id: str, access_token: str, video_path: Path) -> None:
        upload = await self._uploads.get(upload_id)
        if upload is None:
            logger.error("upload %s vanished before start", upload_id)
            return

        total = video_path.stat().st_size
        await self._uploads.update(
            upload_id, status=UploadStatus.UPLOADING, bytes_total=total
        )
        meta = UploadMetadata(
            title=upload.title,
            description=upload.description,
            tags=upload.tags,
            category_id=upload.category_id,
            privacy_status=upload.privacy_status,
            notify_subscribers=upload.privacy_status == "public",
        )

        async def on_progress(bytes_sent: int) -> None:
            await self._uploads.update(upload_id, bytes_sent=bytes_sent)

        try:
            video_id = await self._uploader.upload(
                video_path, meta, access_token, on_progress
            )
        except asyncio.CancelledError:
            # Shutdown mid-upload: leave the record as-is; in-memory state
            # dies with the process anyway (accepted prototype behavior).
            raise
        except YouTubeAuthError as exc:
            await self._fail(upload_id, "invalid_token", exc)
            return
        except YouTubeQuotaError as exc:
            await self._fail(upload_id, "quota_exceeded", exc)
            return
        except httpx.TransportError as exc:
            await self._fail(upload_id, "network_error", exc)
            return
        except Exception as exc:  # noqa: BLE001 — task must never die unrecorded
            await self._fail(upload_id, "upload_failed", exc)
            return

        fields: dict = {
            "status": UploadStatus.COMPLETED,
            "video_id": video_id,
            "video_url": f"https://www.youtube.com/watch?v={video_id}",
        }
        if upload.playlist_id:
            # Best-effort, like the reference script: a playlist failure does
            # not undo a successful upload.
            try:
                await self._uploader.add_to_playlist(
                    video_id, upload.playlist_id, access_token
                )
                fields["playlist_added"] = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("upload %s: playlist add failed: %s", upload_id, exc)
                fields["playlist_added"] = False
                fields["error_message"] = f"uploaded, but playlist add failed: {exc}"
        await self._uploads.update(upload_id, **fields)
        logger.info("upload %s completed: %s", upload_id, fields["video_url"])

    async def _fail(self, upload_id: str, code: str, exc: Exception) -> None:
        logger.exception("upload %s failed (%s)", upload_id, code)
        await self._uploads.update(
            upload_id,
            status=UploadStatus.FAILED,
            error_code=code,
            error_message=str(exc),
        )
