from pathlib import Path

from openai import AsyncOpenAI

from app.config import Settings
from app.languages import DEFAULT_LANGUAGE
from app.llm.client import with_retries


class TranscriptionError(Exception):
    pass


async def transcribe_words(
    client: AsyncOpenAI,
    settings: Settings,
    audio_path: Path,
    language: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict], float, str]:
    """Returns ([{text, start, end}, ...] in seconds, total audio duration,
    and the full PUNCTUATED transcript text).

    Whisper's word entries carry no punctuation; the punctuated `text` field
    is what caption authoring should copy from so subtitles keep punctuation.

    `language` (ISO 639-1) is passed to Whisper so it doesn't misdetect the
    spoken language — the transcript is the verbatim source the aligner and
    captions depend on, so a wrong detection corrupts the whole downstream.
    """

    async def _call():
        with audio_path.open("rb") as f:
            return await client.audio.transcriptions.create(
                model=settings.transcribe_model,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                language=language,
                temperature=settings.llm_temperature,
            )

    result = await with_retries(_call, max_attempts=settings.max_retries)
    raw_words = getattr(result, "words", None) or []
    if not raw_words:
        raise TranscriptionError("transcription returned no word timestamps")

    words = [
        {"text": w.word, "start": float(w.start), "end": float(w.end)} for w in raw_words
    ]
    duration = float(getattr(result, "duration", 0.0) or words[-1]["end"])
    text = (getattr(result, "text", "") or "").strip() or " ".join(
        w["text"] for w in words
    )
    return words, duration, text
