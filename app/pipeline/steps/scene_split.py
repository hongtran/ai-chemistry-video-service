"""LLM scene split, validated against the user-provided scene schema.

The schema file describes the FINAL data.json (config + scenes with
start/duration/captionTiming). The LLM authors scenes BEFORE timing exists, so
we validate its output against a derived "authoring" schema: same scene items,
but with start/duration removed from `required` (they're computed by the
alignment step). The full schema is enforced later on the composed data.json.
"""
import copy
import json
from pathlib import Path

import jsonschema
from openai import AsyncOpenAI

from app.config import Settings
from app.llm.client import with_retries

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "scene_split.txt"
_EXAMPLES_PATH = Path(__file__).parent.parent / "prompts" / "scene_examples.json"

_TIMING_FIELDS = {"start", "duration", "captionTiming"}

# Per-type content fields that visibly break the frame when missing or empty.
# Mirrors REQUIRED_CONTENT_FIELDS in
# render_kit/templates/chemistry/frame-defaults.mjs (the renderer's
# source of truth) — keep in sync if that file changes. Fields NOT listed are
# either genuinely optional (reaction-equation's r2) or have a safe renderer
# default (tug-of-war's leftSymbol).
REQUIRED_CONTENT_FIELDS: dict[str, list[str]] = {
    "cover": [],
    "stats": ["stat", "statLabel"],
    "quote": ["quote", "attribution"],
    "diagram": ["items"],
    "cta": ["subheadline"],
    "atom": ["symbol", "shell1", "shell2", "shell3", "title"],
    "element-card": ["symbol", "atomicNumber", "atomicMass", "name"],
    "reaction-equation": ["r1", "p1"],
    "molecule": ["leftSymbol", "rightSymbol", "leftColor", "rightColor", "angle", "bondOrder", "title"],
    "ph-bar": ["targetPh", "title"],
    "orbital-overlap": ["atom1", "atom2"],
    "bond-comparison": ["ionicAtom1", "ionicAtom2", "covalentAtom1", "covalentAtom2"],
    "particle-count": ["containerLabel", "result", "targetCount"],
    "tug-of-war": ["verb"],
}


class SceneSplitError(Exception):
    pass


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


def content_field_errors(scenes: list) -> list[str]:
    errors = []
    for i, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue  # schema validation reports this
        stype = scene.get("type")
        missing = [
            f for f in REQUIRED_CONTENT_FIELDS.get(stype, [])
            if _is_missing(scene.get(f))
        ]
        if missing:
            errors.append(
                f"scene '{scene.get('id', i)}' (type '{stype}'): missing required "
                f"content field(s) {', '.join(missing)} — the frame renders "
                "broken without them"
            )
    return errors


def load_scene_schema(settings: Settings) -> dict:
    path = settings.scene_schema_path
    if not path.is_file():
        raise SceneSplitError(f"scene schema not provided (expected at {path}; set SCENE_SCHEMA_PATH)")
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


async def generate_scenes(
    client: AsyncOpenAI,
    settings: Settings,
    script: str,
    transcript_text: str,
    alignment_feedback: str | None = None,
) -> list[dict]:
    """One LLM call + up to one corrective re-prompt on schema-invalid output.

    `alignment_feedback` is set on the re-split retry after an alignment
    failure: it tells the LLM which captions diverged from the audio.
    """
    full_schema = load_scene_schema(settings)
    author_schema = authoring_schema(full_schema)

    required_lines = "\n".join(
        f"- {t}: {', '.join(fields) if fields else '(no extra required fields)'}"
        for t, fields in REQUIRED_CONTENT_FIELDS.items()
    )
    user_parts = [
        "TRANSCRIPT (what was actually spoken, with punctuation — "
        f"SOURCE OF TRUTH for captions):\n{transcript_text}",
        f"NARRATION SCRIPT (reference only; captions must follow the transcript):\n{script}",
        "SCENE JSON SCHEMA (one scene object; includes typeUsage guide):\n"
        + json.dumps(author_schema["items"], indent=2),
        "REQUIRED CONTENT FIELDS PER TYPE — every scene MUST include non-empty "
        "values for its type's fields below; a frame missing any of them "
        "renders broken. Only pick a type if you can fill ALL of its required "
        "fields from the narration:\n" + required_lines,
        "GOLDEN EXAMPLES — one well-formed scene per frame type with full "
        "params. Match this level of completeness for every scene you emit "
        "(captions in the examples are illustrative only; YOUR captions must "
        "copy the transcript verbatim):\n" + _EXAMPLES_PATH.read_text("utf-8"),
    ]
    if alignment_feedback:
        user_parts.append(
            "PREVIOUS ATTEMPT FAILED WORD ALIGNMENT against the audio:\n"
            f"{alignment_feedback}\n"
            "Re-split the narration, copying the TRANSCRIPT wording verbatim into "
            "captions so every caption word appears in the audio, in order."
        )

    messages: list[dict] = [
        {"role": "system", "content": _PROMPT_PATH.read_text("utf-8")},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]

    last_errors: list[str] = []
    for attempt in (1, 2):
        async def _call() -> str:
            completion = await client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                response_format={"type": "json_object"},
            )
            return completion.choices[0].message.content or ""

        raw = await with_retries(_call, max_attempts=settings.max_retries)

        try:
            scenes = json.loads(raw).get("scenes")
        except json.JSONDecodeError as exc:
            scenes, last_errors = None, [f"response was not valid JSON: {exc}"]
        else:
            if not isinstance(scenes, list) or not scenes:
                last_errors = ["response must be an object with a non-empty 'scenes' array"]
            else:
                last_errors = _validate(scenes, author_schema) + content_field_errors(scenes)
                if not last_errors:
                    return scenes

        if attempt == 1:
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    "Your scenes failed schema validation:\n- "
                    + "\n- ".join(last_errors[:10])
                    + "\n\nFix these problems and return the corrected "
                    '{"scenes": [...]} JSON object only.'
                ),
            })

    raise SceneSplitError(
        "LLM scene output failed schema validation after a corrective re-prompt: "
        + "; ".join(last_errors[:5])
    )
