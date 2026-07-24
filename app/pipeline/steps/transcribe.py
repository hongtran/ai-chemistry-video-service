from pathlib import Path

from elevenlabs.client import AsyncElevenLabs
from elevenlabs.core.api_error import ApiError
from openai import AsyncOpenAI

from app.config import Settings
from app.languages import DEFAULT_LANGUAGE
from app.llm.client import with_retries
from app.llm.elevenlabs import with_elevenlabs_retries
from app.observability import track_generation

# Vietnamese transcription uses ElevenLabs Scribe instead of Whisper in prod:
# the word timestamps here drive alignment, and Whisper's Vietnamese accuracy is
# poor enough that its mishears desync captions. Mirrors the vi+prod ElevenLabs
# switch in tts.py. Every other language / env keeps using Whisper.
ELEVENLABS_LANGUAGE = "vi"


class TranscriptionError(Exception):
    pass


async def _transcribe_whisper(
    client: AsyncOpenAI,
    settings: Settings,
    audio_path: Path,
    language: str,
) -> tuple[list[dict], float, str]:
    async def _call():
        # Manual generation: the OpenAI wrapper doesn't auto-trace audio calls.
        # usage_details (audio seconds) lets Langfuse price it once a per-minute
        # whisper model price is configured in the UI.
        with audio_path.open("rb") as f, track_generation(
            settings, name="transcribe", model=settings.transcribe_model
        ) as gen:
            result = await client.audio.transcriptions.create(
                model=settings.transcribe_model,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                language=language,
                temperature=settings.llm_temperature,
            )
            if gen is not None:
                seconds = float(getattr(result, "duration", 0.0) or 0.0)
                if seconds:
                    gen.update(usage_details={"input": seconds})
            return result

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


async def _transcribe_elevenlabs(
    settings: Settings,
    audio_path: Path,
    language: str,
) -> tuple[list[dict], float, str]:
    if not settings.elevenlabs_api_key:
        raise TranscriptionError(
            "ELEVENLABS_API_KEY is required for language=vi transcription"
        )
    eleven = AsyncElevenLabs(api_key=settings.elevenlabs_api_key)

    async def _call():
        with audio_path.open("rb") as f, track_generation(
            settings, name="transcribe", model=settings.elevenlabs_transcribe_model_id
        ) as gen:
            result = await eleven.speech_to_text.convert(
                model_id=settings.elevenlabs_transcribe_model_id,
                file=f,
                language_code=language,
                timestamps_granularity="word",
                # We only need verbatim words + timings for alignment: no speaker
                # diarization, and no "(laughter)" style event tokens polluting
                # the word stream.
                diarize=False,
                tag_audio_events=False,
            )
            if gen is not None:
                seconds = float(getattr(result, "audio_duration_secs", 0.0) or 0.0)
                if seconds:
                    gen.update(usage_details={"input": seconds})
            return result

    try:
        result = await with_elevenlabs_retries(
            _call, max_attempts=settings.max_retries, label="ElevenLabs Scribe"
        )
    except ApiError as exc:
        if exc.status_code == 402:
            raise TranscriptionError(
                "ElevenLabs returned 402 Payment Required — the account behind "
                "ELEVENLABS_API_KEY is out of credits or on a plan that doesn't "
                "cover Scribe. Check usage/billing at "
                "https://elevenlabs.io/app/usage."
            ) from exc
        raise

    # Scribe emits word, "spacing", and "audio_event" tokens. Only real words
    # carry the timing the aligner consumes; keep those and drop any (e.g. an
    # audio event) that lacks a start/end.
    raw_words = getattr(result, "words", None) or []
    words = [
        {"text": w.text, "start": float(w.start), "end": float(w.end)}
        for w in raw_words
        if getattr(w, "type", "word") == "word"
        and w.start is not None
        and w.end is not None
    ]
    if not words:
        raise TranscriptionError(
            "ElevenLabs transcription returned no word timestamps"
        )
    duration = (
        float(getattr(result, "audio_duration_secs", 0.0) or 0.0) or words[-1]["end"]
    )
    text = (getattr(result, "text", "") or "").strip() or " ".join(
        w["text"] for w in words
    )
    return words, duration, text


async def transcribe_words(
    client: AsyncOpenAI,
    settings: Settings,
    audio_path: Path,
    language: str = DEFAULT_LANGUAGE,
) -> tuple[list[dict], float, str]:
    """Returns ([{text, start, end}, ...] in seconds, total audio duration,
    and the full PUNCTUATED transcript text).

    The word entries carry no punctuation; the punctuated `text` field is what
    caption authoring should copy from so subtitles keep punctuation.

    `language` (ISO 639-1) is passed to the model so it doesn't misdetect the
    spoken language — the transcript is the verbatim source the aligner and
    captions depend on, so a wrong detection corrupts the whole downstream.

    Vietnamese in prod is transcribed by ElevenLabs Scribe (better vi word
    timings than Whisper); every other language / env uses Whisper.
    """
    if language == ELEVENLABS_LANGUAGE and settings.environment == "prod":
        return await _transcribe_elevenlabs(settings, audio_path, language)
    return await _transcribe_whisper(client, settings, audio_path, language)
