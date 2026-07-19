"""Thin OpenAI wrapper + retry policy + the subject guard.

One retry helper covers every OpenAI call in the service: exponential backoff
with jitter, retrying only transient failures (rate limit / 5xx / timeouts /
connection errors) — never 4xx request errors.
"""
import asyncio
import logging
import random
from typing import Awaitable, Callable, Protocol, TypeVar

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import Settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


class GuardUnavailableError(Exception):
    """The subject guard could not get an answer from the LLM (after retries).
    Transient — the caller may reasonably retry later."""


class GuardMisconfiguredError(Exception):
    """The subject guard is misconfigured (bad/revoked API key, no permission).
    Permanent until a human fixes it — retrying will not help."""


class GuardResult(BaseModel):
    is_valid: bool
    reason: str


class SubjectGuard(Protocol):
    async def check(self, query: str, subject: str) -> GuardResult: ...


async def with_retries(
    call: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await call()
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            logger.warning(
                "OpenAI call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt, max_attempts, exc, delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


class LLMSubjectGuard:
    def __init__(self, client: AsyncOpenAI, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def check(self, query: str, subject: str) -> GuardResult:
        from app.subjects import get_subject_config

        subject_config = get_subject_config(subject, self._settings)
        system_prompt = (
            "You are a gatekeeper for an educational video service. "
            f"Decide whether the user's query is a {subject_config.display_name} "
            "concept or question that a short educational video could explain. "
            f"{subject_config.guard_description} Reply with is_valid and a "
            "one-sentence reason."
        )

        async def _call() -> GuardResult:
            completion = await self._client.chat.completions.parse(
                model=self._settings.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
                response_format=GuardResult,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise GuardUnavailableError("guard returned no parsable result")
            return parsed
        try:
            return await with_retries(_call, max_attempts=self._settings.max_retries)
        except GuardUnavailableError:
            raise
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            logger.critical("subject guard misconfigured: %s", exc)
            raise GuardMisconfiguredError(
                "OpenAI credentials are invalid or lack permission"
            ) from exc
        except openai.APIError as exc:
            raise GuardUnavailableError(str(exc)) from exc


class StubSubjectGuard:
    """Accepts any non-empty query. Used in USE_STUB_PIPELINE mode so the demo
    needs no OpenAI credentials."""

    async def check(self, query: str, subject: str) -> GuardResult:
        return GuardResult(is_valid=True, reason="stub mode: guard disabled")


def build_openai_client(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key)
