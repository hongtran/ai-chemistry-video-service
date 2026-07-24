"""Shared retry policy for ElevenLabs SDK calls.

ElevenLabs isn't an OpenAI call, so it can't reuse app.llm.client.with_retries
(that helper's retryable set is OpenAI-specific exception types). Same backoff
shape, different exception surface. Used by both TTS synthesis and Scribe
transcription.
"""
import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

import httpx
from elevenlabs.core.api_error import ApiError

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_elevenlabs_retries(
    call: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay: float = 1.0,
    label: str = "ElevenLabs",
) -> T:
    """Exponential backoff for an async ElevenLabs SDK call. Only retries rate
    limits (429) and server errors (5xx); 4xx errors like 402 Payment Required
    are permanent until a human fixes the account, so they raise immediately."""
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await call()
        except ApiError as exc:
            retryable = exc.status_code == 429 or (
                exc.status_code is not None and exc.status_code >= 500
            )
            if not retryable:
                raise
            last_exc = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc

        if attempt == max_attempts:
            break
        delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        logger.warning(
            "%s call failed (attempt %d/%d): %s — retrying in %.1fs",
            label, attempt, max_attempts, last_exc, delay,
        )
        await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]
