import re
from datetime import datetime, timezone

import jsonschema

from app.subjects import SubjectConfig


class ComposeError(Exception):
    pass


def _slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def build_data(
    query: str,
    job_id: str,
    subject_config: SubjectConfig,
    scenes: list[dict],
    total_duration: float,
    orientation: str = "vertical",
    metadata: dict | None = None,
) -> dict:
    slug = _slugify(query) or f"{subject_config.name}-video"
    suffix = "-h" if orientation == "horizontal" else ""
    width = 1920 if orientation == "horizontal" else 1080
    height = 1080 if orientation == "horizontal" else 1920
    metadata = metadata or {}

    config = {
        "topic": query,
        "slug": f"{slug}-{job_id[:8]}{suffix}",
        "totalDuration": round(total_duration, 2),
        "orientation": orientation,
        "width": width,
        "height": height,
        "capHighlight": subject_config.cap_highlight,
        "audio": "assets/tts/narration.mp3",
    }
    if metadata.get("description"):
        config["description"] = metadata["description"]
    if metadata.get("hashtags"):
        config["hashtags"] = metadata["hashtags"]
    if metadata.get("tags"):
        config["tags"] = metadata["tags"]

    return {"config": config, "scenes": scenes}


def build_meta(data: dict, query: str) -> dict:
    """The YouTube-facing sidecar, same shape templates/*/populate.js writes to
    videos/<slug>/meta.json. Keys absent from config stay absent here."""
    config = data.get("config", {})
    meta = {
        "id": config.get("slug"),
        "name": query,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    for key in ("description", "hashtags", "tags"):
        if config.get(key):
            meta[key] = config[key]
    return meta


def validate_data(data: dict, full_schema: dict) -> None:
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in jsonschema.Draft7Validator(full_schema).iter_errors(data)
    ]
    if errors:
        raise ComposeError(
            "final data.json failed schema validation: " + "; ".join(errors[:5])
        )
