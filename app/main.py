import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth_router import router as auth_router
from app.api.router import router
from app.api.youtube_router import router as youtube_router
from app.auth import AdminAuth, require_admin
from app.config import Settings
from app.llm.client import LLMSubjectGuard, StubSubjectGuard, build_openai_client
from app.pipeline.base import VideoPipeline
from app.pipeline.orchestrator import RealVideoPipeline
from app.pipeline.stub import StubPipeline
from app.storage.artifacts import LocalArtifactStore
from app.storage.jobs import InMemoryJobRepository
from app.storage.uploads import InMemoryUploadRepository
from app.worker.queue import AsyncioJobQueue
from app.worker.uploads import UploadRunner
from app.youtube.client import YouTubeUploader
from app.youtube.oauth import GoogleOAuth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _build_pipeline(settings: Settings, jobs, artifacts, client) -> VideoPipeline:
    if settings.use_stub_pipeline:
        logger.info("Pipeline: StubPipeline (USE_STUB_PIPELINE=true)")
        return StubPipeline(jobs, artifacts)
    logger.info("Pipeline: RealVideoPipeline (model=%s)", settings.llm_model)
    return RealVideoPipeline(jobs, artifacts, client, settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    jobs = InMemoryJobRepository()
    artifacts = LocalArtifactStore(settings.artifacts_dir)

    if settings.use_stub_pipeline:
        client = None
        guard = StubSubjectGuard()
        logger.info("Subject guard: stub (accepts any non-empty query)")
    else:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required unless USE_STUB_PIPELINE=true"
            )
        client = build_openai_client(settings)
        guard = LLMSubjectGuard(client, settings)

    pipeline = _build_pipeline(settings, jobs, artifacts, client)
    queue = AsyncioJobQueue(pipeline, jobs, concurrency=settings.worker_concurrency)

    # YouTube upload: shared Google HTTP client, stateless OAuth broker, and a
    # per-upload background runner. Credentials are optional — unset, the
    # /auth/google endpoints 500 with a pointer and everything else works.
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, read=settings.youtube_upload_timeout_seconds)
    )
    oauth = GoogleOAuth(settings, http_client)
    uploads = InMemoryUploadRepository()
    upload_runner = UploadRunner(
        uploads,
        YouTubeUploader(http_client, settings.youtube_upload_chunk_bytes),
        jobs=jobs,
        artifacts=artifacts,
        clear_job_on_success=settings.clear_job_after_youtube_upload,
    )

    auth = AdminAuth(settings)
    if auth.enabled:
        logger.info(
            "Admin auth ENABLED (user=%s, session ttl=%ds)",
            settings.admin_username,
            settings.admin_session_ttl_seconds,
        )
    elif settings.admin_username or settings.admin_password:
        logger.warning(
            "Admin auth DISABLED — only one of ADMIN_USERNAME/ADMIN_PASSWORD "
            "is set; set both to protect the API."
        )
    else:
        logger.warning(
            "Admin auth DISABLED — set ADMIN_USERNAME and ADMIN_PASSWORD "
            "to protect the API."
        )

    app.state.settings = settings
    app.state.auth = auth
    app.state.jobs = jobs
    app.state.artifacts = artifacts
    app.state.queue = queue
    app.state.guard = guard
    app.state.uploads = uploads
    app.state.upload_runner = upload_runner
    app.state.oauth = oauth

    await queue.start()
    logger.info("Job queue started (concurrency=%d)", settings.worker_concurrency)
    yield
    await queue.stop()
    await upload_runner.stop()
    await http_client.aclose()
    logger.info("Job queue stopped")


app = FastAPI(title="AI Subject Video Request Service", lifespan=lifespan)
# Middleware must be attached before startup, so CORS reads its own Settings
# here rather than the lifespan instance.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in Settings().cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    # Lets the SPA distinguish admin-session 401s (realm="admin") from the
    # Google-token 401 on the upload endpoint when calling cross-origin.
    expose_headers=["WWW-Authenticate"],
)
# Admin token guards the videos API only; the YouTube routes and login stay open.
app.include_router(router, dependencies=[Depends(require_admin)])
app.include_router(youtube_router)
app.include_router(auth_router)
