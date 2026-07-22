"""Pass 2 — batched typed authoring (split-first pipeline).

Scenes are authored TOGETHER, one LLM call per batch, so the model sees every
scene's sentences and captions side by side and can deliberately vary frame
types (authoring scenes in isolation made it repeat the same type — e.g. three
neural-net frames in a row — because no scene could see its siblings' choice).

Given the whole script (context) plus, for each scene, its own sentences and its
finished caption chunks, the model chooses a frame `type` per scene and fills
that type's content fields. Captions are GIVEN — the model never writes or
rewords them; id and captions are re-applied in code after validation so they
can't drift. Most videos fit one batch; very long ones split into sequential
batches that each learn the types earlier batches already used, so variety holds
across the seam.
"""
import json
import logging

from openai import AsyncOpenAI

from app.config import Settings
from app.languages import DEFAULT_LANGUAGE
from app.llm.client import with_retries
from app.pipeline.steps import scene_split
from app.pipeline.steps.segment import SceneIndex
from app.subjects import SubjectConfig

logger = logging.getLogger(__name__)


class AuthorError(scene_split.SceneSplitError):
    pass


def _scene_texts(scene: SceneIndex, sentences_by_index: dict[int, str]) -> list[str]:
    return [sentences_by_index[i] for i in scene.idx_sentences if i in sentences_by_index]


def build_author_user_message(
    batch: list[SceneIndex],
    sentences_by_index: dict[int, str],
    full_script: str,
    orientation: str,
    language: str = DEFAULT_LANGUAGE,
    prior_types: list[str] | None = None,
    feedback: str | None = None,
) -> str:
    parts = [scene_split.canvas_line(orientation)]

    language_block = scene_split._language_block(language)
    if language_block:
        parts.append(language_block)

    parts.append(
        "FULL SCRIPT (context only — so you can choose frame types that fit each "
        "scene's place in the narrative; author each scene ONLY from its own "
        f"sentences below):\n{full_script}"
    )

    if prior_types:
        parts.append(
            "FRAME TYPES ALREADY USED earlier in this video, in order: "
            f"{json.dumps(prior_types)}. Keep varying types — do NOT repeat the "
            "immediately preceding type unless the content genuinely calls for it."
        )

    scene_blocks = []
    for scene in batch:
        texts = " ".join(_scene_texts(scene, sentences_by_index))
        scene_blocks.append(
            f'SCENE id="{scene.scene_id}"\n'
            f"  sentences (author this scene's visible content from these words): {texts}\n"
            f"  captions (finalized, returned unchanged — shown only so visuals "
            f"stay in sync): {json.dumps(scene.captions, ensure_ascii=False)}"
        )
    parts.append(
        f"AUTHOR THESE {len(batch)} SCENES, in order:\n\n" + "\n\n".join(scene_blocks)
    )

    parts.append(
        'Return ONLY a JSON object {"scenes": [ ... ]} with one object per scene '
        "above, in the SAME order, each carrying its given \"id\" plus the chosen "
        '"type" and that type\'s content fields. Do NOT include captions, start, '
        "duration, or captionTiming — those are added by the system."
    )
    if feedback:
        parts.append(feedback)
    return "\n\n".join(parts)


def _match_scenes(
    authored: list, batch: list[SceneIndex]
) -> tuple[list[dict], list[str]]:
    """Line each authored scene back up to its SceneIndex by id, code-applying
    the canonical id + captions (never trusting the model's) and stripping any
    timing the model included. Returns (scenes in batch order, errors)."""
    errors: list[str] = []
    by_id: dict[str, dict] = {}
    for item in authored:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            by_id[item["id"]] = item

    ordered: list[dict] = []
    reordered = False
    for pos, plan in enumerate(batch):
        item = by_id.get(plan.scene_id)
        if item is None:
            # Fall back to positional match if the model dropped/renamed the id.
            item = authored[pos] if pos < len(authored) and isinstance(authored[pos], dict) else None
            reordered = True
        if not isinstance(item, dict):
            errors.append(f"scene '{plan.scene_id}': missing from the authored scenes array")
            continue
        scene = dict(item)
        scene["id"] = plan.scene_id
        scene["captions"] = plan.captions
        for field in scene_split._TIMING_FIELDS:
            scene.pop(field, None)
        # `image` is system-owned: the IMAGE_GEN step fills it from imagePrompt.
        # Drop anything the model invented so a hallucinated/garbage data URI can
        # never reach the render (the placeholder default applies until then).
        scene.pop("image", None)
        ordered.append(scene)

    if reordered:
        logger.warning(
            "author batch: authored scenes did not match given ids exactly — "
            "fell back to positional matching"
        )
    return ordered, errors


async def author_batch(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    batch: list[SceneIndex],
    sentences_by_index: dict[int, str],
    full_script: str,
    *,
    orientation: str = "vertical",
    language: str = DEFAULT_LANGUAGE,
    prior_types: list[str] | None = None,
    feedback: str | None = None,
) -> list[dict]:
    """Author every scene in `batch` together in one call, retrying the whole
    batch on validation failure up to settings.max_split_attempts."""
    full_schema = scene_split.load_scene_schema(subject_config)
    author_schema = scene_split.authoring_schema(full_schema)

    messages = [
        {
            "role": "system",
            "content": scene_split.build_system_prompt(subject_config, author_schema),
        },
        {
            "role": "user",
            "content": build_author_user_message(
                batch, sentences_by_index, full_script, orientation, language,
                prior_types, feedback,
            ),
        },
    ]

    last_errors: list[str] = []
    for attempt in range(1, settings.max_split_attempts + 1):

        async def _call() -> str:
            completion = await client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
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
            authored = payload.get("scenes") if isinstance(payload, dict) else None
            if not isinstance(authored, list) or not authored:
                last_errors = ["response must be an object with a non-empty 'scenes' array"]
            else:
                scenes, match_errors = _match_scenes(authored, batch)
                last_errors = (
                    match_errors
                    + scene_split.validate(scenes, author_schema)
                    + scene_split.content_field_errors(
                        scenes, subject_config.required_content_fields
                    )
                )
                if not last_errors:
                    return scenes

        if attempt < settings.max_split_attempts:
            logger.warning(
                "author batch (%d scenes) attempt %d failed validation: %s",
                len(batch), attempt, "; ".join(last_errors[:6]),
            )
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": (
                    "Your scenes failed schema validation:\n- "
                    + "\n- ".join(last_errors[:8])
                    + '\n\nFix these problems and return the corrected {"scenes": '
                    "[...]} JSON object only, one object per scene in order."
                ),
            })

    raise AuthorError(
        f"batch of {len(batch)} scene(s) failed schema validation after "
        f"{settings.max_split_attempts} attempts: " + "; ".join(last_errors[:6])
    )


async def author_scenes(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    scenes_index: list[SceneIndex],
    sentences_by_index: dict[int, str],
    full_script: str,
    *,
    orientation: str = "vertical",
    language: str = DEFAULT_LANGUAGE,
) -> list[dict]:
    """Author all scenes, in batches of settings.author_batch_size. A short
    video is one batch; long ones split into sequential batches that each learn
    the types already chosen so frame-type variety holds across the seam."""
    size = max(1, settings.author_batch_size)
    scenes: list[dict] = []
    prior_types: list[str] = []
    for start in range(0, len(scenes_index), size):
        batch = scenes_index[start : start + size]
        authored = await author_batch(
            client, settings, subject_config, batch, sentences_by_index,
            full_script, orientation=orientation, language=language,
            prior_types=prior_types,
        )
        scenes.extend(authored)
        prior_types.extend(s.get("type", "") for s in authored)
    return scenes


async def reauthor_scenes(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    offending: list[SceneIndex],
    sentences_by_index: dict[int, str],
    full_script: str,
    all_types_so_far: list[str],
    feedback: str,
    *,
    orientation: str = "vertical",
    language: str = DEFAULT_LANGUAGE,
) -> list[dict]:
    """Re-author every layout-flagged scene together in one call, so the model
    can pick distinct, roomier types across siblings instead of fixing each one
    blind. `feedback` folds in the layout-gate complaints for the batch."""
    return await author_batch(
        client, settings, subject_config, offending, sentences_by_index,
        full_script, orientation=orientation, language=language,
        prior_types=all_types_so_far, feedback=feedback,
    )
