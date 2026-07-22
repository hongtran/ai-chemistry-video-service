"""Shared scene-authoring primitives used by both LLM passes.

Pass 1 (segment.py) groups the script's sentences into scenes; Pass 2
(author.py) authors each scene's typed data. Both validate against the subject
template's scene schema. The schema file describes the FINAL data.json (config +
scenes with start/duration/captionTiming); the LLM authors scenes BEFORE timing
exists, so we validate against a derived "authoring" schema: same scene items,
but with start/duration removed from `required` (they're computed by the
alignment step). The full schema is enforced later on the composed data.json.

The system prompt is deliberately identical for every scene and every job of a
subject — it is the cache-stable prefix. Everything per-request (a scene's
sentences, canvas, language, feedback) lives in user messages.
"""
import copy
import json

import jsonschema

from app.languages import DEFAULT_LANGUAGE, language_name
from app.subjects import SubjectConfig

_TIMING_FIELDS = {"start", "duration", "captionTiming"}


def _language_block(language: str) -> str:
    """Force target-language DISPLAY text + YouTube metadata. Empty for the
    default language (captions come from the script, already in-language, so the
    English path is unchanged). Kept in user messages, never the cache-stable
    system prefix."""
    if language == DEFAULT_LANGUAGE:
        return ""
    name = language_name(language)
    return (
        f"LANGUAGE: Write every on-screen display field you author (headline, "
        f"eyebrow, and all type-specific labels/titles) AND any config "
        f"description/hashtags/tags in {name}, matching the script's language. "
        "Standard chemical notation (NaCl, H₂O) stays as-is."
    )


class SceneSplitError(Exception):
    pass


def _clean_list(value: object) -> list[str]:
    """Lowercase, drop a stray leading '#', discard blanks/non-scalars."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, (str, int, float)):
            continue
        text = str(item).strip().lstrip("#").strip().lower()
        if text:
            out.append(text)
    return out


def coerce_metadata(raw: object) -> dict:
    """Best-effort YouTube metadata. A weak description still yields a good
    video, so anything malformed is dropped rather than failing the job —
    compose omits absent keys and populate.js writes a minimal meta.json."""
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    description = raw.get("description")
    if isinstance(description, str) and description.strip():
        out["description"] = description.strip()
    for key in ("hashtags", "tags"):
        cleaned = _clean_list(raw.get(key))
        if cleaned:
            out[key] = cleaned
    return out


def _is_missing(value: object) -> bool:
    # 0 is a legitimate value (e.g. shell2: 0, targetPh: 0) — only absent,
    # blank-string, and empty-list count as missing.
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return not value
    return False


def content_field_errors(
    scenes: list,
    required_content_fields: dict[str, list[str]],
) -> list[str]:
    errors = []
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue  # schema validation reports this
        stype = scene.get("type")
        missing = [
            f for f in required_content_fields.get(stype, [])
            if _is_missing(scene.get(f))
        ]
        if missing:
            errors.append(
                f"scene '{scene.get('id', i)}' (type '{stype}'): missing required "
                f"content field(s) {', '.join(missing)} — the frame renders "
                "broken without them"
            )
    return errors


def load_scene_schema(subject_config: SubjectConfig) -> dict:
    path = subject_config.scene_schema_path
    if not path.is_file():
        raise SceneSplitError(
            f"scene schema not provided for subject '{subject_config.name}' "
            f"(expected at {path})"
        )
    try:
        return json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise SceneSplitError(f"scene schema at {path} is not valid JSON: {exc}") from exc


def authoring_schema(full_schema: dict) -> dict:
    """Scene-array schema for LLM output: timing fields not yet required."""
    try:
        scenes_schema = copy.deepcopy(full_schema["properties"]["scenes"])
    except KeyError as exc:
        raise SceneSplitError("scene schema has no properties.scenes section") from exc
    items = scenes_schema.get("items", {})
    items["required"] = [
        r for r in items.get("required", []) if r not in _TIMING_FIELDS
    ]
    return scenes_schema


def validate(instance: object, schema: dict) -> list[str]:
    validator = jsonschema.Draft7Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in validator.iter_errors(instance)
    ]


def build_system_prompt(subject_config: SubjectConfig, author_schema: dict) -> str:
    """Static, query-independent Pass 2 context: a stable prefix OpenAI can
    cache across every scene and every job of this subject."""
    required_lines = "\n".join(
        f"- {t}: {', '.join(fields) if fields else '(no extra required fields)'}"
        for t, fields in subject_config.required_content_fields.items()
    )
    return "\n\n".join([
        subject_config.scene_split_prompt,
        "SCENE JSON SCHEMA (one scene object; includes typeUsage guide):\n"
        + json.dumps(author_schema["items"], indent=2),
        "REQUIRED CONTENT FIELDS PER TYPE — every scene MUST include non-empty "
        "values for its type's fields below; a frame missing any of them "
        "renders broken. Only pick a type if you can fill ALL of its required "
        "fields from the scene's sentences:\n" + required_lines,
        "GOLDEN EXAMPLES — one well-formed scene per frame type with full "
        "params. Match this level of completeness for every scene you author "
        "(captions in the examples are illustrative only; the real captions are "
        "GIVEN to you and must be returned unchanged):\n"
        + subject_config.scene_examples,
    ])


def canvas_line(orientation: str) -> str:
    return (
        "This video is horizontal 16:9 (1920x1080, YouTube)."
        if orientation == "horizontal"
        else "This video is vertical 9:16 (1080x1920, Shorts/Reels)."
    )
