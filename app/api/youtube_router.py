from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.api.schemas import (
    CreateYouTubeUploadRequest,
    CreateYouTubeUploadResponse,
    YouTubeUploadDetail,
)
from app.domain.models import JobStatus, YouTubeUpload
from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository
from app.storage.uploads import UploadRepository
from app.worker.uploads import UploadRunner
from app.youtube.client import append_hashtags
from app.youtube.oauth import GoogleOAuth, GoogleOAuthError

router = APIRouter(prefix="/api/v1", tags=["youtube"])


def _deps(
    request: Request,
) -> tuple[JobRepository, ArtifactStore, UploadRepository, UploadRunner, GoogleOAuth]:
    s = request.app.state
    return s.jobs, s.artifacts, s.uploads, s.upload_runner, s.oauth


def _oauth_or_500(request: Request) -> GoogleOAuth:
    oauth: GoogleOAuth = request.app.state.oauth
    if not oauth.configured:
        raise HTTPException(
            status_code=500,
            detail=(
                "Google OAuth is not configured. Set GOOGLE_CLIENT_ID and "
                "GOOGLE_CLIENT_SECRET (see .env.example)."
            ),
        )
    return oauth


@router.get("/auth/google/login")
async def google_login(
    request: Request,
    redirect: bool = Query(default=True),
    mode: Literal["json", "web"] = Query(default="json"),
) -> Response:
    """Start the consent flow. `?redirect=false` returns the URL as JSON
    instead of a 307, for SPA clients that navigate themselves. `?mode=web`
    makes the callback redirect the browser to the frontend with the tokens
    in the URL fragment instead of returning JSON."""
    oauth = _oauth_or_500(request)
    auth_url = oauth.build_auth_url(oauth.make_state(mode))
    if not redirect:
        return JSONResponse({"auth_url": auth_url})
    return RedirectResponse(auth_url)


@router.get("/auth/google/callback", response_model=None)
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response | dict:
    """Exchange the authorization code and hand the tokens to the client.
    The server stores nothing — the client owns the token from here. In json
    mode (default) Google's token JSON is returned verbatim; in web mode
    (login started with ?mode=web) the browser is redirected to the frontend
    with the tokens in the URL fragment, so they never reach server logs."""
    oauth = _oauth_or_500(request)
    # State comes first: the mode inside it decides how errors are reported.
    mode = oauth.verify_state(state) if state else None
    if mode is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    settings = getattr(request.app.state, "settings", None)
    frontend = settings.frontend_oauth_redirect if settings else ""
    web = mode == "web" and bool(frontend)

    def _web_error(message: str) -> RedirectResponse:
        return RedirectResponse(f"{frontend}#{urlencode({'error': message})}")

    if error:
        message = f"Google authorization failed: {error}"
        if web:
            return _web_error(message)
        raise HTTPException(status_code=400, detail=message)
    if not code:
        if web:
            return _web_error("Missing authorization code.")
        raise HTTPException(status_code=400, detail="Missing authorization code.")
    try:
        tokens = await oauth.exchange_code(code)
    except GoogleOAuthError as exc:
        message = f"Google token exchange failed: {exc}"
        if web:
            return _web_error(message)
        raise HTTPException(status_code=502, detail=message) from exc
    if web:
        keys = ("access_token", "refresh_token", "expires_in", "scope")
        fragment = urlencode({k: tokens[k] for k in keys if k in tokens})
        return RedirectResponse(f"{frontend}#{fragment}")
    return tokens


@router.post(
    "/videos/{job_id}/youtube",
    response_model=CreateYouTubeUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_to_youtube(
    job_id: str, body: CreateYouTubeUploadRequest, request: Request
) -> CreateYouTubeUploadResponse:
    jobs, artifacts, uploads, runner, oauth = _deps(request)

    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != JobStatus.COMPLETED or not job.video_path:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Video is not ready.",
                "status": job.status.value,
                "current_step": job.current_step.value if job.current_step else None,
                "error_message": job.error_message,
            },
        )
    if not artifacts.exists(job_id, "video.mp4"):
        raise HTTPException(
            status_code=404, detail="Video file missing from artifact store."
        )
    if not await oauth.verify_access_token(body.access_token):
        raise HTTPException(
            status_code=401,
            detail=(
                "Google access token invalid, expired, or missing the "
                "youtube.upload scope. Re-authorize via /api/v1/auth/google/login."
            ),
        )

    # Snapshot metadata now (body overrides meta.json) so the background task
    # never re-reads artifacts.
    meta = (
        artifacts.load_json(job_id, "meta.json")
        if artifacts.exists(job_id, "meta.json")
        else {}
    )
    title = (body.title or meta.get("name") or f"{job.subject}-{job_id}")[:100]
    description = (
        body.description if body.description is not None else meta.get("description", "")
    )
    hashtags = body.hashtags if body.hashtags is not None else meta.get("hashtags", [])
    tags = body.tags if body.tags is not None else meta.get("tags", [])

    upload = YouTubeUpload(
        job_id=job_id,
        title=title,
        description=append_hashtags(description, hashtags),
        tags=tags,
        privacy_status=body.privacy_status,
        category_id=body.category_id,
        playlist_id=body.playlist_id,
    )
    await uploads.create(upload)
    thumbnail_path = (
        artifacts.path_for(job_id, "thumbnail.jpg")
        if artifacts.exists(job_id, "thumbnail.jpg")
        else None
    )
    runner.submit(
        upload.id,
        body.access_token,
        artifacts.path_for(job_id, "video.mp4"),
        thumbnail_path,
    )
    return CreateYouTubeUploadResponse(
        upload_id=upload.id, job_id=job_id, status=upload.status
    )


@router.get("/youtube-uploads", response_model=list[YouTubeUploadDetail])
async def list_youtube_uploads(
    request: Request, job_id: str | None = Query(default=None)
) -> list[YouTubeUploadDetail]:
    _, _, uploads, _, _ = _deps(request)
    return [YouTubeUploadDetail.from_upload(u) for u in await uploads.list(job_id)]


@router.get("/youtube-uploads/{upload_id}", response_model=YouTubeUploadDetail)
async def get_youtube_upload(upload_id: str, request: Request) -> YouTubeUploadDetail:
    _, _, uploads, _, _ = _deps(request)
    upload = await uploads.get(upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return YouTubeUploadDetail.from_upload(upload)
