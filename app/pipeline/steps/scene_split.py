"""LLM scene split, validated against the subject template's scene schema.

The schema file describes the FINAL data.json (config + scenes with
start/duration/captionTiming). The LLM authors scenes BEFORE timing exists, so
we validate its output against a derived "authoring" schema: same scene items,
but with start/duration removed from `required` (they're computed by the
alignment step). The full schema is enforced later on the composed data.json.

A long-form (horizontal) narration is too long to split in one call, so it's
divided into ~200-word sections that are split independently, each holding its
own conversation (SectionState.messages). Keeping the conversation lets a
later alignment/layout failure be fed back as another turn to the ONE section
that owns it, instead of regenerating the whole video blind.

The system prompt is deliberately identical for every section and every job of
a subject — it is the cache-stable prefix. Everything per-request (the
section's transcript slice, arc rule, canvas, metadata ask, feedback) lives in
user messages.
"""
import copy
import json
import logging
import re
from dataclasses import dataclass, field

import jsonschema
from openai import AsyncOpenAI

from app.config import Settings
from app.pipeline.steps.sections import (
    build_arc_rule,
    section_index_from_scene_id,
    split_into_sections,
)
from app.llm.client import with_retries
from app.subjects import SubjectConfig

logger = logging.getLogger(__name__)

_TIMING_FIELDS = {"start", "duration", "captionTiming"}
_ID_PREFIX_RE = re.compile(r"^s\d+-")

# Asked for in the USER message (not the cache-stable system prefix) because
# on a long-form run only the first section writes the video-level metadata.
_METADATA_BLOCK = (
    'ALSO include a top-level "config" object alongside "scenes", carrying the '
    "video's YouTube metadata written from the narration:\n"
    '- "description": 2-4 sentence YouTube description summarizing the topic for '
    'a general audience, plain prose (no hashtags in this field).\n'
    '- "hashtags": 3-6 short lowercase words/phrases, no "#" and no spaces '
    '(e.g. "ai", "llmagents"), most relevant first.\n'
    '- "tags": 5-10 short lowercase search-keyword phrases for YouTube\'s tags '
    'field (these may contain spaces, e.g. "ai agents").\n'
    'Response shape: {"config": {"description": "...", "hashtags": ["..."], '
    '"tags": ["..."]}, "scenes": [ ... ]}'
)


class SceneSplitError(Exception):
    pass


@dataclass
class SectionState:
    """One slice of the narration plus the live conversation that split it."""

    index: int
    total: int
    text: str  # transcript slice — the verbatim source for this part
    id_prefix: str
    messages: list[dict] = field(default_factory=list)
    scenes: list[dict] | None = None


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


def _validate(scenes: object, schema: dict) -> list[str]:
    validator = jsonschema.Draft7Validator(schema)
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in validator.iter_errors(scenes)
    ]


def build_system_prompt(subject_config: SubjectConfig, author_schema: dict) -> str:
    """Static, query-independent context: a stable prefix OpenAI can cache
    across every section and every job of this subject."""
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
        "fields from the narration:\n" + required_lines,
        "GOLDEN EXAMPLES — one well-formed scene per frame type with full "
        "params. Match this level of completeness for every scene you emit "
        "(captions in the examples are illustrative only; YOUR captions must "
        "copy the transcript verbatim):\n" + subject_config.scene_examples,
    ])


def _canvas_line(orientation: str) -> str:
    return (
        "This video is horizontal 16:9 (1920x1080, YouTube)."
        if orientation == "horizontal"
        else "This video is vertical 9:16 (1080x1920, Shorts/Reels)."
    )


def build_user_message(
    section: SectionState,
    orientation: str,
    script: str | None,
    full_transcript: str,
) -> str:
    """Everything per-request. Multi-section runs get position-aware framing
    and only their own transcript slice to split."""
    multi = section.total > 1
    parts = [_canvas_line(orientation), build_arc_rule(section.index, section.total)]

    if multi:
        parts.append(
            f"The transcript below is PART {section.index + 1} of {section.total} "
            "of one longer continuous video — you are splitting ONLY this part; "
            "the other parts are handled separately, so do not re-introduce the "
            "topic or wrap up unless your part's position calls for it (see the "
            "arc rule above)."
        )

    if section.index == 0:
        parts.append(_METADATA_BLOCK)

    if multi:
        parts.append(
            f"TRANSCRIPT — PART {section.index + 1} of {section.total} (what was "
            "actually spoken in this part; split exactly this, verbatim):\n"
            f"{section.text}"
        )
        if section.index == 0:
            parts.append(
                "FULL VIDEO TRANSCRIPT (ALL parts — for the \"config\" metadata "
                "ONLY; do NOT split this. Your captions must cover exactly the "
                f"part above, nothing more):\n{full_transcript}"
            )
    else:
        parts.append(
            "TRANSCRIPT (what was actually spoken, with punctuation — "
            f"SOURCE OF TRUTH for captions):\n{section.text}"
        )
        if script:
            parts.append(
                "NARRATION SCRIPT (reference only; captions must follow the "
                f"transcript):\n{script}"
            )

    return "\n\n".join(parts)


def _apply_id_prefix(scenes: list[dict], prefix: str) -> list[dict]:
    """Scene ids carry their owning section so a downstream failure traces back
    to it. Applied in code, never trusted to the LLM; any prefix the model
    invented on its own is stripped first so ids can't compound."""
    if not prefix:
        return scenes
    for scene in scenes:
        base = _ID_PREFIX_RE.sub("", str(scene.get("id", "")))
        scene["id"] = f"{prefix}{base}"
    return scenes


async def split_section(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    section: SectionState,
    *,
    orientation: str = "vertical",
    script: str | None = None,
    full_transcript: str = "",
    feedback: str | None = None,
) -> tuple[list[dict], dict]:
    """Split one section, continuing its conversation if it already has one.

    `feedback` appends a corrective turn (alignment or layout-gate findings)
    before re-asking — that's how a targeted regeneration re-uses everything
    the model already worked out for this section.
    """
    full_schema = load_scene_schema(subject_config)
    author_schema = authoring_schema(full_schema)

    if not section.messages:
        section.messages = [
            {
                "role": "system",
                "content": build_system_prompt(subject_config, author_schema),
            },
            {
                "role": "user",
                "content": build_user_message(
                    section, orientation, script, full_transcript
                ),
            },
        ]
    elif feedback:
        section.messages.append({"role": "user", "content": feedback})

    last_errors: list[str] = []
    for attempt in range(1, settings.max_split_attempts + 1):

        async def _call() -> str:
            completion = await client.chat.completions.create(
                model=settings.llm_model,
                messages=section.messages,
                response_format={"type": "json_object"},
                temperature=settings.llm_temperature,
            )
            return completion.choices[0].message.content or ""

        raw = await with_retries(_call, max_attempts=settings.max_retries)

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            last_errors = [f"response was not valid JSON: {exc}"]
        else:
            scenes = payload.get("scenes") if isinstance(payload, dict) else None
            if not isinstance(scenes, list) or not scenes:
                last_errors = ["response must be an object with a non-empty 'scenes' array"]
            else:
                last_errors = _validate(scenes, author_schema) + content_field_errors(
                    scenes, subject_config.required_content_fields
                )
                if not last_errors:
                    scenes = _apply_id_prefix(scenes, section.id_prefix)
                    section.scenes = scenes
                    metadata: dict = {}
                    if section.index == 0:
                        metadata = coerce_metadata(payload.get("config"))
                        if not metadata:
                            logger.warning(
                                "scene split returned no usable YouTube metadata — "
                                "continuing without it"
                            )
                    return scenes, metadata

        if attempt < settings.max_split_attempts:
            logger.warning(
                "scene split (section %d/%d) attempt %d failed validation: %s",
                section.index + 1, section.total, attempt, "; ".join(last_errors[:10]),
            )
            section.messages.append({"role": "assistant", "content": raw})
            section.messages.append({
                "role": "user",
                "content": (
                    "Your scenes failed schema validation:\n- "
                    + "\n- ".join(last_errors[:10])
                    + "\n\nFix these problems and return the corrected "
                    '{"config": {...}, "scenes": [...]} JSON object only.'
                ),
            })

    where = (
        f"section {section.index + 1}/{section.total}: " if section.total > 1 else ""
    )
    raise SceneSplitError(
        f"{where}LLM scene output failed schema validation after "
        f"{settings.max_split_attempts} attempts: " + "; ".join(last_errors[:10])
    )


def build_sections(
    subject_config: SubjectConfig, orientation: str, transcript_text: str
) -> list[SectionState]:
    """One section for a short; ~N-word sections for a long-form orientation.

    Sections are cut from the TRANSCRIPT, not the script: the transcript is
    what captions must reproduce verbatim, so slicing it directly means each
    section's verbatim source is exactly its own slice, and the slices
    concatenate back to the full transcript the aligner matches against.
    """
    if orientation in subject_config.long_form_orientations:
        texts = split_into_sections(transcript_text, subject_config.section_word_target)
    else:
        texts = [transcript_text]
    total = len(texts)
    return [
        SectionState(
            index=i,
            total=total,
            text=text,
            id_prefix=f"s{i}-" if total > 1 else "",
        )
        for i, text in enumerate(texts)
    ]


def sections_for_scene_ids(
    sections: list[SectionState], scene_ids: list[str]
) -> list[SectionState]:
    """The sections owning the given scene ids — who to regenerate after an
    alignment/layout failure. Falls back to the first section when the ids
    carry no usable prefix."""
    indices = {section_index_from_scene_id(sid) for sid in scene_ids}
    owning = [s for s in sections if s.index in indices]
    return owning or sections[:1]


async def generate_scenes(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    script: str,
    transcript_text: str,
    alignment_feedback: str | None = None,
) -> tuple[list[dict], dict]:
    """Single-section convenience wrapper (the short-form path).

    Returns (scenes, metadata) — metadata is the video's YouTube
    description/hashtags/tags, best-effort (see coerce_metadata).
    """
    section = SectionState(index=0, total=1, text=transcript_text, id_prefix="")
    if alignment_feedback:
        section.messages = [
            {
                "role": "system",
                "content": build_system_prompt(
                    subject_config, authoring_schema(load_scene_schema(subject_config))
                ),
            },
            {
                "role": "user",
                "content": build_user_message(section, "vertical", script, transcript_text)
                + "\n\nPREVIOUS ATTEMPT FAILED WORD ALIGNMENT against the audio:\n"
                + alignment_feedback
                + "\nRe-split the narration, copying the TRANSCRIPT wording verbatim "
                "into captions so every caption word appears in the audio, in order.",
            },
        ]
    return await split_section(
        client,
        settings,
        subject_config,
        section,
        orientation="vertical",
        script=script,
        full_transcript=transcript_text,
    )
