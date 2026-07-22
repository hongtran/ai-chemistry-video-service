"""Narration synthesis.

Short scripts are one TTS call. Long-form scripts exceed the TTS API's input
limit, so they're split at sentence boundaries, synthesized per chunk, and
ffmpeg-concatenated into ONE narration.mp3 — downstream Whisper and alignment
only ever see a single continuous track.

Vietnamese (language="vi") is synthesized via ElevenLabs instead of OpenAI
TTS — noticeably better Vietnamese prosody than gpt-4o-mini-tts. Every other
language keeps using OpenAI TTS.
"""
import asyncio
import logging
import random
import shutil
import tempfile
from pathlib import Path

import httpx
from elevenlabs.client import AsyncElevenLabs
from elevenlabs.core.api_error import ApiError
from openai import AsyncOpenAI

from app.config import Settings
from app.languages import DEFAULT_LANGUAGE
from app.llm.client import with_retries
from app.observability import track_generation
from app.pipeline.steps.sections import split_for_tts

logger = logging.getLogger(__name__)

ELEVENLABS_LANGUAGE = "vi"


class TTSError(Exception):
    pass


async def _synthesize_chunk(
    client: AsyncOpenAI, settings: Settings, text: str, voice: str
) -> bytes:
    async def _call() -> bytes:
        # Manual generation: the OpenAI wrapper doesn't auto-trace audio calls.
        # usage_details lets Langfuse price it once a char-based tts model price
        # is configured in the UI.
        with track_generation(
            settings,
            name="tts",
            model=settings.tts_model,
            input={"chars": len(text), "voice": voice},
        ) as gen:
            response = await client.audio.speech.create(
                model=settings.tts_model,
                voice=voice,
                input=text,
                response_format="mp3"
            )
            if gen is not None:
                gen.update(usage_details={"input": len(text)})
            return response.content

    return await with_retries(_call, max_attempts=settings.max_retries)


async def _with_elevenlabs_retries(
    call, *, max_attempts: int, base_delay: float = 1.0
) -> bytes:
    """Same backoff policy as with_retries (app.llm.client), but for the
    elevenlabs SDK's exception type — ElevenLabs isn't an OpenAI call so it
    can't reuse that helper's OpenAI-specific exception tuple. Only retries
    rate limits (429) and server errors (5xx); 4xx errors like 402 Payment
    Required are permanent until a human fixes the account, so they raise
    immediately."""
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
            "ElevenLabs TTS call failed (attempt %d/%d): %s — retrying in %.1fs",
            attempt, max_attempts, last_exc, delay,
        )
        await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


async def _synthesize_chunk_elevenlabs(settings: Settings, text: str) -> bytes:
    if not settings.elevenlabs_api_key:
        raise TTSError("ELEVENLABS_API_KEY is required for language=vi TTS")
    if not settings.elevenlabs_voice_id:
        raise TTSError("ELEVENLABS_VOICE_ID is required for language=vi TTS")

    eleven = AsyncElevenLabs(api_key=settings.elevenlabs_api_key)

    async def _call() -> bytes:
        with track_generation(
            settings,
            name="tts",
            model=settings.elevenlabs_model_id,
            input={"chars": len(text), "voice": settings.elevenlabs_voice_id},
        ) as gen:
            response = eleven.text_to_speech.convert(
                voice_id=settings.elevenlabs_voice_id,
                model_id=settings.elevenlabs_model_id,
                text=text,
                output_format="mp3_44100_128",
            )
            audio = b"".join([chunk async for chunk in response])
            if gen is not None:
                gen.update(usage_details={"input": len(text)})
            return audio

    try:
        return await _with_elevenlabs_retries(_call, max_attempts=settings.max_retries)
    except ApiError as exc:
        if exc.status_code == 402:
            raise TTSError(
                "ElevenLabs returned 402 Payment Required — the account behind "
                "ELEVENLABS_API_KEY is out of credits or on a plan that doesn't "
                "cover this request. Check usage/billing at "
                "https://elevenlabs.io/app/usage."
            ) from exc
        raise


async def _concat_mp3(parts: list[bytes]) -> bytes:
    """Join mp3 parts into one track. Re-encodes (never `-c copy`): stream-
    copying concatenated mp3s leaves timestamp discontinuities at each seam,
    which skews every Whisper word timestamp after the first chunk — and those
    timestamps are what the whole alignment step depends on."""
    if shutil.which("ffmpeg") is None:
        raise TTSError(
            "ffmpeg is required to join multi-part narration audio (long-form "
            "videos) but was not found on PATH"
        )

    with tempfile.TemporaryDirectory(prefix="tts-parts-") as tmp:
        tmpdir = Path(tmp)
        part_paths = []
        for i, data in enumerate(parts):
            path = tmpdir / f"part-{i:03d}.mp3"
            path.write_bytes(data)
            part_paths.append(path)

        list_path = tmpdir / "concat.txt"
        list_path.write_text(
            "".join(f"file '{p.name}'\n" for p in part_paths), encoding="utf-8"
        )
        out_path = tmpdir / "joined.mp3"

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_path),
            "-c:a", "libmp3lame",
            "-q:a", "2",
            str(out_path),
            cwd=str(tmpdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            tail = stderr.decode("utf-8", "replace").strip()[-500:]
            raise TTSError(f"ffmpeg concat failed ({proc.returncode}): ...{tail}")
        if not out_path.is_file():
            raise TTSError("ffmpeg reported success but produced no output file")
        return out_path.read_bytes()


async def synthesize(
    client: AsyncOpenAI,
    settings: Settings,
    script: str,
    language: str = DEFAULT_LANGUAGE,
) -> bytes:
    chunks = split_for_tts(script, settings.tts_max_chars)
    if not chunks:
        raise TTSError("nothing to synthesize: script is empty")

    if language == ELEVENLABS_LANGUAGE:
        async def _synth(text: str) -> bytes:
            return await _synthesize_chunk_elevenlabs(settings, text)
    else:
        voice = settings.voice_for_language(language)

        async def _synth(text: str) -> bytes:
            return await _synthesize_chunk(client, settings, text, voice)

    if len(chunks) == 1:
        return await _synth(chunks[0])

    logger.info(
        "narration is %d chars — synthesizing in %d chunks, then joining",
        len(script), len(chunks),
    )
    parts = [await _synth(c) for c in chunks]
    return await _concat_mp3(parts)
