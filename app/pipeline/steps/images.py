"""Image generation for photo / photo-split frames.

The authoring step (Pass 2) writes an `imagePrompt` for each image scene but no
picture — chat models can't emit images. This step turns those prompts into real
pictures: it calls OpenAI's image model once per image scene and embeds the result
directly in the scene as a base64 **data URI** (`scene["image"]`), so the image
travels inside data.json and needs no separate asset staging at render time.

Design notes:
- **Idempotent** — only scenes still on the placeholder are generated, so it's
  safe to re-run after the layout gate re-authors some scenes.
- **Best-effort** — a single image failing keeps that scene's placeholder and logs
  a warning; only invalid/again-missing credentials (Authentication /
  PermissionDenied) propagate, matching how the orchestrator treats them.
- **Bounded** — at most `settings.max_images_per_video` images per video; extra
  image scenes keep the placeholder.
- **Disable-able** — `images_enabled=false` skips generation entirely.
"""
import logging

import openai
from openai import AsyncOpenAI

from app.config import Settings
from app.llm.client import with_retries
from app.observability import track_generation
from app.subjects import SubjectConfig

logger = logging.getLogger(__name__)

# The gray "IMAGE" placeholder (SVG data URI). MUST stay byte-identical to
# PLACEHOLDER_IMAGE in each render_kit/templates/<tpl>/frame-defaults.mjs so that
# "is this scene still un-generated?" is a simple equality check across both sides.
PLACEHOLDER_IMAGE = (
    "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmci"
    "IHdpZHRoPSI4MDAiIGhlaWdodD0iODAwIiB2aWV3Qm94PSIwIDAgODAwIDgwMCI+PHJlY3Qgd2lkdG"
    "g9IjgwMCIgaGVpZ2h0PSI4MDAiIGZpbGw9IiMxQjIxMzAiLz48ZyBmaWxsPSJub25lIiBzdHJva2U9"
    "IiM0QTU1NjgiIHN0cm9rZS13aWR0aD0iMTQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiIHN0cm9rZS"
    "1saW5lY2FwPSJyb3VuZCI+PHJlY3QgeD0iMjA1IiB5PSIyNTAiIHdpZHRoPSIzOTAiIGhlaWdodD0i"
    "MzAwIiByeD0iMTgiLz48Y2lyY2xlIGN4PSIzMjAiIGN5PSIzNTIiIHI9IjM0Ii8+PHBhdGggZD0iTT"
    "I1MCA1MjIgTDM3MiAzOTggTDQ1MiA0NzggTDUyMCA0MTYgTDU2MCA1MjIiLz48L2c+PHRleHQgeD0i"
    "NDAwIiB5PSI2MjIiIGZpbGw9IiM1QTY2NzgiIGZvbnQtZmFtaWx5PSJzYW5zLXNlcmlmIiBmb250LX"
    "NpemU9IjQyIiBmb250LXdlaWdodD0iNzAwIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBsZXR0ZXItc3Bh"
    "Y2luZz0iNCI+SU1BR0U8L3RleHQ+PC9zdmc+"
)


class ImageGenError(Exception):
    pass


def _needs_image(scene: dict, image_types: frozenset[str]) -> bool:
    if scene.get("type") not in image_types:
        return False
    if not str(scene.get("imagePrompt", "")).strip():
        return False
    current = scene.get("image")
    # Regenerate only when the scene is still on the placeholder (or has no image
    # yet). A real generated PNG data URI is left untouched → idempotent.
    return not current or current == PLACEHOLDER_IMAGE


def _size_for(settings: Settings, orientation: str) -> str:
    return (
        settings.image_size_horizontal
        if orientation == "horizontal"
        else settings.image_size_vertical
    )


async def _generate_one(
    client: AsyncOpenAI, settings: Settings, prompt: str, size: str
) -> str:
    """Generate one image, returning a `data:image/png;base64,…` URI."""

    async def _call() -> str:
        with track_generation(
            settings,
            name="image",
            model=settings.image_model,
            input={"prompt_chars": len(prompt), "size": size, "quality": settings.image_quality},
        ) as gen:
            result = await client.images.generate(
                model=settings.image_model,
                prompt=prompt,
                size=size,
                quality=settings.image_quality,
                n=1,
            )
            b64 = result.data[0].b64_json
            if not b64:
                raise ImageGenError("image API returned no b64_json payload")
            if gen is not None:
                gen.update(usage_details={"images": 1})
            return f"data:image/png;base64,{b64}"

    return await with_retries(_call, max_attempts=settings.max_retries)


async def resolve_images(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    scenes: list[dict],
    *,
    orientation: str = "vertical",
) -> list[dict]:
    """Fill `image` for every image scene still on the placeholder. Mutates and
    returns `scenes`. No-op when the subject has no image frame types or
    `images_enabled` is false."""
    image_types = subject_config.image_frame_types
    if not image_types or not settings.images_enabled:
        return scenes

    targets = [s for s in scenes if _needs_image(s, image_types)]
    if not targets:
        return scenes

    size = _size_for(settings, orientation)
    generated = 0
    for scene in targets:
        if generated >= settings.max_images_per_video:
            logger.warning(
                "image cap (%d) reached — scene %r keeps the placeholder",
                settings.max_images_per_video, scene.get("id"),
            )
            scene.setdefault("image", PLACEHOLDER_IMAGE)
            continue
        prompt = str(scene["imagePrompt"]).strip()
        try:
            scene["image"] = await _generate_one(client, settings, prompt, size)
            generated += 1
        except (openai.AuthenticationError, openai.PermissionDeniedError):
            raise  # permanent — let the orchestrator surface a clear message
        except Exception as exc:  # noqa: BLE001 — one bad image must not fail the job
            logger.warning(
                "image generation failed for scene %r (%s) — keeping placeholder: %s",
                scene.get("id"), scene.get("type"), exc,
            )
            scene.setdefault("image", PLACEHOLDER_IMAGE)

    if generated:
        logger.info("generated %d image(s) for %d image scene(s)", generated, len(targets))
    return scenes
