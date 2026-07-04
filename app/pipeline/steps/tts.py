from openai import AsyncOpenAI

from app.config import Settings
from app.llm.client import with_retries


async def synthesize(client: AsyncOpenAI, settings: Settings, script: str) -> bytes:
    async def _call() -> bytes:
        response = await client.audio.speech.create(
            model=settings.tts_model,
            voice=settings.tts_voice,
            input=script,
            response_format="mp3",
        )
        return response.content

    return await with_retries(_call, max_attempts=settings.max_retries)
