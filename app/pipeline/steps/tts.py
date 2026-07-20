"""Narration synthesis.

Short scripts are one TTS call. Long-form scripts exceed the TTS API's input
limit, so they're split at sentence boundaries, synthesized per chunk, and
ffmpeg-concatenated into ONE narration.mp3 — downstream Whisper and alignment
only ever see a single continuous track.
"""
import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

from app.config import Settings
from app.languages import DEFAULT_LANGUAGE
from app.llm.client import with_retries
from app.pipeline.steps.sections import split_for_tts

logger = logging.getLogger(__name__)


class TTSError(Exception):
    pass


async def _synthesize_chunk(
    client: AsyncOpenAI, settings: Settings, text: str, voice: str
) -> bytes:
    async def _call() -> bytes:
        response = await client.audio.speech.create(
            model=settings.tts_model,
            voice=voice,
            input=text,
            response_format="mp3"
        )
        return response.content

    return await with_retries(_call, max_attempts=settings.max_retries)


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
    voice = settings.voice_for_language(language)
    chunks = split_for_tts(script, settings.tts_max_chars)
    if not chunks:
        raise TTSError("nothing to synthesize: script is empty")
    if len(chunks) == 1:
        return await _synthesize_chunk(client, settings, chunks[0], voice)

    logger.info(
        "narration is %d chars — synthesizing in %d chunks, then joining",
        len(script), len(chunks),
    )
    parts = [await _synthesize_chunk(client, settings, c, voice) for c in chunks]
    return await _concat_mp3(parts)
