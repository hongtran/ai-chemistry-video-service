"""Langfuse LLM cost/token tracking — an optional, self-contained layer.

Everything here is a no-op when Langfuse is not configured (both keys empty), so
the service runs unchanged in dev / stub / test without any Langfuse account.

Why explicit init instead of Langfuse's env auto-pickup: secrets load via
pydantic-settings from `.env`, which does NOT export to `os.environ`; Langfuse's
auto-init reads `os.environ`, so we hand it the keys directly from Settings.

The OpenAI drop-in wrapper (see app/llm/client.build_openai_client) uses the
global client constructed here and automatically records tokens + USD cost for
chat completions. `job_trace` groups every generation of one video job under a
single trace.
"""
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from app.config import Settings
    from app.domain.models import Job

logger = logging.getLogger(__name__)


def init_langfuse(settings: "Settings") -> bool:
    """Construct the global Langfuse client so the OpenAI wrapper and traces can
    reach it. Returns whether tracking is enabled. Called once at startup."""
    if not settings.langfuse_enabled:
        logger.info("Langfuse DISABLED — no LANGFUSE keys set (LLM cost not tracked)")
        return False

    from langfuse import Langfuse

    # Constructing Langfuse registers the process-wide singleton that
    # `langfuse.openai` and `get_client()` resolve to.
    Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    logger.info("Langfuse ENABLED (host=%s)", settings.langfuse_host)
    return True


def flush_langfuse() -> None:
    """Flush buffered events before shutdown. Safe to call when disabled."""
    try:
        from langfuse import get_client
    except ImportError:
        return
    client = get_client()
    if client is not None:
        client.flush()


@contextmanager
def track_generation(
    settings: "Settings",
    *,
    name: str,
    model: str,
    input: object | None = None,
    metadata: dict | None = None,
) -> Iterator[object | None]:
    """Record a manual Langfuse generation for calls the OpenAI wrapper does not
    auto-trace (audio: TTS + Whisper). Yields the generation (so the caller can
    `.update(usage_details=...)`) or None when disabled.

    USD cost stays $0 for these until per-model prices are configured in the
    Langfuse UI (TTS is char-based, Whisper per-minute)."""
    if not settings.langfuse_enabled:
        yield None
        return

    from langfuse import get_client

    with get_client().start_as_current_observation(
        name=name, as_type="generation", model=model, input=input, metadata=metadata
    ) as gen:
        yield gen


@contextmanager
def job_trace(settings: "Settings", job: "Job") -> Iterator[None]:
    """Open one Langfuse trace for a video job so all its LLM generations nest
    under it. A no-op context when Langfuse is disabled."""
    if not settings.langfuse_enabled:
        yield
        return

    from langfuse import get_client, propagate_attributes

    langfuse = get_client()
    # propagate_attributes sets trace-level fields (name/session/tags/metadata)
    # that flow onto every observation created inside; the span roots the trace
    # so all LLM generations nest under one video-job trace.
    with propagate_attributes(
        trace_name=f"video-job:{job.subject}",
        session_id=job.id,
        metadata={
            "subject": job.subject,
            "language": job.language,
            "orientation": job.orientation,
            "model": settings.llm_model,
        },
        tags=[job.subject, job.language],
    ), langfuse.start_as_current_observation(name="video-job", as_type="span"):
        yield
