"""YouTube Data API v3 uploader over plain httpx.

Uses the resumable upload protocol directly (init POST -> Location URL ->
chunked PUTs) instead of google-api-python-client: the official client is
sync-only and would block the event loop everything here runs on.
"""
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
PLAYLIST_ITEMS_ENDPOINT = "https://www.googleapis.com/youtube/v3/playlistItems"

_CHUNK_RETRIES = 3
_QUOTA_REASONS = ("quotaExceeded", "uploadLimitExceeded")


class YouTubeAuthError(Exception):
    """Token rejected by Google (401)."""


class YouTubeQuotaError(Exception):
    """Daily quota or upload limit exhausted (403 quotaExceeded)."""


class YouTubeUploadError(Exception):
    """Any other non-transient YouTube API failure."""


@dataclass
class UploadMetadata:
    title: str
    description: str
    tags: list[str]
    category_id: str
    privacy_status: str
    notify_subscribers: bool


def append_hashtags(description: str, hashtags: list[str]) -> str:
    """Trailing "#a #b" line — YouTube shows the first few above the title."""
    if not hashtags:
        return description
    line = " ".join(t if t.startswith("#") else f"#{t}" for t in hashtags)
    return f"{description}\n\n{line}" if description else line


def _api_error_detail(resp: httpx.Response) -> tuple[str, list[str]]:
    try:
        error = resp.json().get("error", {})
    except ValueError:
        return resp.text[:200] or f"HTTP {resp.status_code}", []
    message = error.get("message") or f"HTTP {resp.status_code}"
    reasons = [e.get("reason", "") for e in error.get("errors", [])]
    return message, reasons


def _raise_api_error(resp: httpx.Response) -> None:
    message, reasons = _api_error_detail(resp)
    if resp.status_code == 401:
        raise YouTubeAuthError(message)
    if resp.status_code == 403 and any(r in _QUOTA_REASONS for r in reasons):
        raise YouTubeQuotaError(message)
    raise YouTubeUploadError(f"HTTP {resp.status_code}: {message}")


class YouTubeUploader:
    def __init__(self, client: httpx.AsyncClient, chunk_bytes: int) -> None:
        self._client = client
        self._chunk_bytes = chunk_bytes

    async def upload(
        self,
        video_path: Path,
        meta: UploadMetadata,
        access_token: str,
        on_progress: Callable[[int], Awaitable[None]] | None = None,
    ) -> str:
        total = video_path.stat().st_size
        upload_url = await self._initiate(meta, access_token, total)
        return await self._send_chunks(
            upload_url, video_path, total, access_token, on_progress
        )

    async def add_to_playlist(
        self, video_id: str, playlist_id: str, access_token: str
    ) -> None:
        resp = await self._client.post(
            PLAYLIST_ITEMS_ENDPOINT,
            params={"part": "snippet"},
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        )
        if resp.status_code not in (200, 201):
            _raise_api_error(resp)

    async def _initiate(
        self, meta: UploadMetadata, access_token: str, total: int
    ) -> str:
        resp = await self._client.post(
            UPLOAD_ENDPOINT,
            params={
                "uploadType": "resumable",
                "part": "snippet,status",
                "notifySubscribers": "true" if meta.notify_subscribers else "false",
            },
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(total),
            },
            json={
                "snippet": {
                    "title": meta.title,
                    "description": meta.description,
                    "tags": meta.tags,
                    "categoryId": meta.category_id,
                },
                "status": {
                    "privacyStatus": meta.privacy_status,
                    "selfDeclaredMadeForKids": False,
                },
            },
        )
        if resp.status_code != 200:
            _raise_api_error(resp)
        location = resp.headers.get("Location")
        if not location:
            raise YouTubeUploadError("resumable init returned no Location header")
        return location

    async def _send_chunks(
        self,
        upload_url: str,
        video_path: Path,
        total: int,
        access_token: str,
        on_progress: Callable[[int], Awaitable[None]] | None,
    ) -> str:
        offset = 0
        with video_path.open("rb") as fh:
            while offset < total:
                fh.seek(offset)
                chunk = fh.read(self._chunk_bytes)
                end = offset + len(chunk) - 1
                resp = await self._put_chunk(
                    upload_url, chunk, offset, end, total, access_token
                )
                if resp.status_code == 308:
                    # Google confirms received bytes via "Range: bytes=0-N";
                    # resume from N+1 (covers partially-applied chunks).
                    offset = _next_offset(resp) or end + 1
                    if on_progress:
                        await on_progress(offset)
                    continue
                if resp.status_code in (200, 201):
                    if on_progress:
                        await on_progress(total)
                    return resp.json()["id"]
                _raise_api_error(resp)
        raise YouTubeUploadError("upload ended without a completion response")

    async def _put_chunk(
        self,
        upload_url: str,
        chunk: bytes,
        start: int,
        end: int,
        total: int,
        access_token: str,
    ) -> httpx.Response:
        delay = 1.0
        for attempt in range(_CHUNK_RETRIES):
            try:
                return await self._client.put(
                    upload_url,
                    content=chunk,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {start}-{end}/{total}",
                    },
                )
            except httpx.TransportError as exc:
                if attempt == _CHUNK_RETRIES - 1:
                    raise
                logger.warning(
                    "chunk PUT failed (%s), retrying in %.0fs", exc, delay
                )
                await asyncio.sleep(delay)
                delay *= 2
        raise AssertionError("unreachable")


def _next_offset(resp: httpx.Response) -> int | None:
    header = resp.headers.get("Range", "")
    if not header.startswith("bytes=0-"):
        return None
    try:
        return int(header.removeprefix("bytes=0-")) + 1
    except ValueError:
        return None
