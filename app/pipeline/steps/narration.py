from openai import AsyncOpenAI

from app.config import Settings
from app.llm.client import with_retries
from app.subjects import SubjectConfig


class NarrationError(Exception):
    pass


def _length_clause(subject_config: SubjectConfig, orientation: str) -> str:
    targets = subject_config.duration_targets
    min_s, max_s = targets.get(orientation, targets["vertical"])
    if orientation in subject_config.long_form_orientations:
        return (
            f"Length: this will be recorded as audio and must run approximately "
            f"{min_s // 60} to {max_s // 60} minutes at a natural, clear speaking "
            "pace — a LONG-FORM video, so develop the topic in real depth: "
            "motivate it, build up the core ideas step by step, work through "
            "concrete examples or comparisons, address common misconceptions, "
            "and land a substantial takeaway. Still one single continuous "
            "narration read aloud once — no headings, no section breaks."
        )
    return (
        f"Length: this will be recorded as audio and must run approximately "
        f"{min_s} to {max_s} seconds at a natural, clear speaking pace — "
        "roughly what a person would read aloud in that time, unhurried."
    )


async def generate_script(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    query: str,
    orientation: str = "vertical",
) -> str:
    system_prompt = subject_config.narration_style + "\n\n" + _length_clause(
        subject_config, orientation
    )
    is_long_form = orientation in subject_config.long_form_orientations

    async def _call() -> str:
        completion = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{subject_config.topic_label}: {query}"},
            ],
            temperature=settings.llm_temperature,
        )
        return (completion.choices[0].message.content or "").strip()

    script = await with_retries(_call, max_attempts=settings.max_retries)
    word_count = len(script.split())
    min_words = 300 if is_long_form else 30
    if word_count < min_words:
        raise NarrationError(
            f"LLM returned an implausibly short script ({word_count} words)"
        )
    return script
