import re

import jsonschema


class ComposeError(Exception):
    pass


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "chemistry-video"


def build_data(
    query: str,
    job_id: str,
    scenes: list[dict],
    total_duration: float,
    width: int = 1080,
    height: int = 1920,
) -> dict:
    return {
        "config": {
            "topic": query,
            "slug": f"{_slugify(query)}-{job_id[:8]}",
            "totalDuration": round(total_duration, 2),
            "width": width,
            "height": height,
            "audio": "assets/tts/narration.mp3",
        },
        "scenes": scenes,
    }


def validate_data(data: dict, full_schema: dict) -> None:
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in jsonschema.Draft7Validator(full_schema).iter_errors(data)
    ]
    if errors:
        raise ComposeError(
            "final data.json failed schema validation: " + "; ".join(errors[:5])
        )
