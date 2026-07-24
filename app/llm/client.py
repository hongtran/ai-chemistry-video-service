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


class NormalizerUnavailableError(Exception):
    """Script normalization could not get an answer from the LLM (after retries).
    Transient — the caller may reasonably retry, or fall back to the raw script."""


class NormalizerMisconfiguredError(Exception):
    """Script normalization is misconfigured (bad/revoked API key, no permission).
    Permanent until a human fixes it — retrying will not help."""


class ScriptNormalization(BaseModel):
    title: str
    narration: str


class ScriptNormalizer(Protocol):
    async def normalize(
        self, script: str, subject: str, language: str
    ) -> ScriptNormalization: ...


def _first_line_title(script: str, limit: int = 80) -> str:
    """A short single-line title from the first non-empty line of a script.
    Mirrors the API router's `_title_from_script` fallback."""
    first_line = next((ln.strip() for ln in script.splitlines() if ln.strip()), "")
    title = " ".join(first_line.split())
    return f"{title[: limit - 1]}…" if len(title) > limit else title


class LLMScriptNormalizer:
    """Cleans a user-pasted script into plain spoken-prose narration (stripping
    headings/markdown/bullets/emoji) following the subject's narration style, and
    derives a short title — in one structured LLM call. Reformats only; it must not
    paraphrase, summarize, add, or drop content."""

    def __init__(self, client: AsyncOpenAI, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _language_clause(self, language: str) -> str:
        from app.languages import DEFAULT_LANGUAGE, language_name

        if language == DEFAULT_LANGUAGE:
            return ""
        name = language_name(language)
        return (
            f"\n\nLanguage: the narration is in {name}. Keep it in {name} — do not "
            "translate it. Preserve the author's original wording."
        )

    async def normalize(
        self, script: str, subject: str, language: str
    ) -> ScriptNormalization:
        from app.subjects import get_subject_config

        subject_config = get_subject_config(subject, self._settings)
        system_prompt = (
            "You clean up a user-pasted narration script so it can be read aloud by "
            "a text-to-speech voice and shown as verbatim captions. The user may have "
            "included headings, markdown, bullet points, numbered lists, emoji, links, "
            "or stage directions.\n\n"
            "Your job is to REFORMAT, not rewrite. Strictly preserve the author's "
            "wording, meaning, order, and every point they make. Do NOT paraphrase, "
            "summarize, expand, add, or drop content. Only:\n"
            "- remove headings, markdown syntax, bullet/list markers, emoji, links, "
            "and stage directions;\n"
            "- join the remaining text into continuous natural spoken prose;\n"
            "- reword only genuinely un-speakable NON-NUMERIC tokens so a narrator "
            "could read them (code identifiers like top_k -> 'top k', URLs, file "
            "paths, raw snake_case/camelCase).\n\n"
            "CRITICAL — numbers stay as digits. KEEP every number, decimal, range, "
            "unit, percentage, and standard math/scientific symbol EXACTLY as written "
            "(e.g. 99.995, ±0.005 g, 6.4, 25%, NaCl). Do NOT spell numbers out as "
            "words and do NOT strip these symbols. This text is shown verbatim as the "
            "on-screen caption, where digits read far more clearly than spelled-out "
            "words; the downstream pipeline handles speaking them aloud. This rule "
            "overrides any advice in the style guide below about saying numbers "
            "naturally or avoiding symbols.\n\n"
            "Also produce a short single-line title (plain text, no markdown, no "
            "trailing punctuation) that names what the narration is about.\n\n"
            "--- TARGET NARRATION STYLE (for tone/register only; do not use it to "
            "change the content or to spell out numbers) ---\n"
            f"{subject_config.narration_style}"
            + self._language_clause(language)
        )

        async def _call() -> ScriptNormalization:
            completion = await self._client.chat.completions.parse(
                model=self._settings.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": script},
                ],
                temperature=0.0,
                response_format=ScriptNormalization,
            )
            parsed = completion.choices[0].message.parsed
            if parsed is None:
                raise NormalizerUnavailableError("normalizer returned no parsable result")
            return parsed

        try:
            return await with_retries(_call, max_attempts=self._settings.max_retries)
        except NormalizerUnavailableError:
            raise
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            logger.critical("script normalizer misconfigured: %s", exc)
            raise NormalizerMisconfiguredError(
                "OpenAI credentials are invalid or lack permission"
            ) from exc
        except openai.APIError as exc:
            raise NormalizerUnavailableError(str(exc)) from exc


class StubScriptNormalizer:
    """Returns the script unchanged with a first-line title. Used in
    USE_STUB_PIPELINE mode so the demo needs no OpenAI credentials."""

    async def normalize(
        self, script: str, subject: str, language: str
    ) -> ScriptNormalization:
        return ScriptNormalization(title=_first_line_title(script), narration=script)


def build_openai_client(settings: Settings) -> AsyncOpenAI:
    # With Langfuse configured, use its drop-in wrapper — interface-identical to
    # AsyncOpenAI, so every downstream call site is unchanged, but chat
    # completions automatically record tokens + USD cost. Falls back to the
    # plain client when tracking is off.
    if settings.langfuse_enabled:
        from langfuse.openai import AsyncOpenAI as LangfuseAsyncOpenAI

        return LangfuseAsyncOpenAI(api_key=settings.openai_api_key)
    return AsyncOpenAI(api_key=settings.openai_api_key)
