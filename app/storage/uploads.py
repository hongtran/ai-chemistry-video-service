"""YouTube upload state boundary.

All upload state lives behind UploadRepository. The in-memory implementation
is the prototype default; swapping to Postgres/Redis means implementing this
protocol and changing one line of wiring in app.main.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Protocol

from app.domain.models import YouTubeUpload


class UploadRepository(Protocol):
    async def create(self, upload: YouTubeUpload) -> YouTubeUpload: ...

    async def get(self, upload_id: str) -> YouTubeUpload | None: ...

    async def list(self, job_id: str | None = None) -> list[YouTubeUpload]: ...

    async def update(self, upload_id: str, **fields: Any) -> YouTubeUpload | None: ...


class InMemoryUploadRepository:
    def __init__(self) -> None:
        self._uploads: dict[str, YouTubeUpload] = {}
        self._lock = asyncio.Lock()

    async def create(self, upload: YouTubeUpload) -> YouTubeUpload:
        async with self._lock:
            self._uploads[upload.id] = upload
        return upload

    async def get(self, upload_id: str) -> YouTubeUpload | None:
        async with self._lock:
            upload = self._uploads.get(upload_id)
        return upload.model_copy() if upload else None

    async def list(self, job_id: str | None = None) -> list[YouTubeUpload]:
        async with self._lock:
            uploads = [u.model_copy() for u in self._uploads.values()]
        if job_id is not None:
            uploads = [u for u in uploads if u.job_id == job_id]
        return sorted(uploads, key=lambda u: u.created_at, reverse=True)

    async def update(self, upload_id: str, **fields: Any) -> YouTubeUpload | None:
        async with self._lock:
            upload = self._uploads.get(upload_id)
            if upload is None:
                return None
            updated = upload.model_copy(
                update={**fields, "updated_at": datetime.now(timezone.utc)}
            )
            self._uploads[upload_id] = updated
        return updated.model_copy()
