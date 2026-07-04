import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import router
from app.config import Settings
from app.llm.client import LLMChemistryGuard, StubChemistryGuard, build_openai_client
from app.pipeline.base import VideoPipeline
from app.pipeline.orchestrator import RealVideoPipeline
from app.pipeline.stub import StubPipeline
from app.storage.artifacts import LocalArtifactStore
from app.storage.jobs import InMemoryJobRepository
from app.worker.queue import AsyncioJobQueue

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
        guard = StubChemistryGuard()
        logger.info("Chemistry guard: stub (accepts any non-empty query)")
    else:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is required unless USE_STUB_PIPELINE=true"
            )
        client = build_openai_client(settings)
        guard = LLMChemistryGuard(client, settings)

    pipeline = _build_pipeline(settings, jobs, artifacts, client)
    queue = AsyncioJobQueue(pipeline, jobs, concurrency=settings.worker_concurrency)

    app.state.settings = settings
    app.state.jobs = jobs
    app.state.artifacts = artifacts
    app.state.queue = queue
    app.state.guard = guard

    await queue.start()
    logger.info("Job queue started (concurrency=%d)", settings.worker_concurrency)
    yield
    await queue.stop()
    logger.info("Job queue stopped")


app = FastAPI(title="AI Chemistry Video Request Service", lifespan=lifespan)
app.include_router(router)
