from pathlib import Path

from openai import AsyncOpenAI

from app.config import Settings
from app.llm.client import with_retries

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "narration.txt"


class NarrationError(Exception):
    pass


async def generate_script(client: AsyncOpenAI, settings: Settings, query: str) -> str:
    system_prompt = _PROMPT_PATH.read_text("utf-8")

    async def _call() -> str:
        completion = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Chemistry topic: {query}"},
            ],
        )
        return (completion.choices[0].message.content or "").strip()

    script = await with_retries(_call, max_attempts=settings.max_retries)
    if len(script.split()) < 30:
        raise NarrationError(f"LLM returned an implausibly short script ({len(script.split())} words)")
    return script
