"""Designed YouTube thumbnail for a finished video.

Orientation-aware: a horizontal (long-form) video gets a 16:9 1280x720 card, a
vertical (short) one a 9:16 1080x1920 card — matching how each shows on YouTube.
The card is the cover scene's headline over a background image. That background
is, in order of preference: a scene's already-generated image (reused, no extra
cost); else a freshly generated image from the topic (one images.generate call);
else an accent gradient.

Render path (no new dependencies): a self-contained HTML file is screenshot to a
PNG by hyperframes' bundled `chrome-headless-shell`, then ffmpeg converts
PNG->JPG (both already used elsewhere in the pipeline). Same "shell out with
asyncio.create_subprocess_exec" pattern as steps/layout_gate.py.

Best-effort: any failure raises ThumbnailError, which the orchestrator swallows
(the job still completes) — exactly like the image/alignment steps.
"""
import asyncio
import html
import logging
import re
import shutil
from pathlib import Path

from openai import AsyncOpenAI

from app.config import Settings
from app.pipeline.steps import images
from app.pipeline.steps.images import PLACEHOLDER_IMAGE
from app.subjects import SubjectConfig

logger = logging.getLogger(__name__)

# Keep in step with the pin in steps/layout_gate.py and render_kit/scripts/
# build-video.sh — the browser that renders the thumbnail must match the one the
# real render uses.
_HYPERFRAMES = "hyperframes@0.7.18"

# Thumbnail pixel dimensions by orientation (matches config.width/height).
_DIMENSIONS = {"horizontal": (1280, 720), "vertical": (1080, 1920)}

# chrome-headless-shell path, resolved once via `hyperframes browser path` and
# cached for the process (the resolve shells out to npx, which is slow).
_chrome_path_cache: str | None = None


class ThumbnailError(Exception):
    """Thumbnail could not be produced (render/convert failed, or no browser).
    Best-effort — the orchestrator logs and continues."""


async def _run(program: str, args: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        program, *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise ThumbnailError(
            f"{program} {' '.join(args[:2])} timed out after {timeout}s"
        ) from None
    return (
        proc.returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


def _cover(scenes: list[dict]) -> dict:
    """The scene whose text titles the thumbnail: the first with a headline,
    else the first scene (or an empty dict for an empty deck)."""
    for scene in scenes:
        if str(scene.get("headline", "")).strip():
            return scene
    return scenes[0] if scenes else {}


def _background_image(scenes: list[dict]) -> str | None:
    """The first real (non-placeholder) generated image data URI, reused as the
    thumbnail background. None -> generate one, else the gradient fallback."""
    for scene in scenes:
        image = scene.get("image")
        if image and image != PLACEHOLDER_IMAGE and str(image).startswith("data:image"):
            return image
    return None


# Strip inline markdown emphasis the authoring step may leave in a headline
# (e.g. "**token by token**") so it doesn't render literally on the thumbnail.
_MD_MARKERS = re.compile(r"(\*\*|\*|__|_|`)")


def _strip_markdown(text: str) -> str:
    return _MD_MARKERS.sub("", text)


async def _generate_background(
    client: AsyncOpenAI, settings: Settings, topic: str, accent: str, size: str
) -> str | None:
    """One images.generate call for a topic-derived background (best-effort).
    Returns a data URI, or None if generation is disabled or fails — the caller
    then falls back to the gradient. Reuses images._generate_one so cost tracking
    and retries match the pipeline's own image step."""
    if not (settings.images_enabled and settings.thumbnail_generate_background):
        return None
    prompt = (
        f'Clean modern abstract background illustration for a video thumbnail about '
        f'"{topic}". Cinematic soft lighting, subtle depth, professional muted palette '
        f'that suits the accent color {accent}. No text, no words, no letters, no logos. '
        f'Keep the lower-left region darker and uncluttered for a title overlay.'
    )
    try:
        return await images._generate_one(client, settings, prompt, size)
    except Exception as exc:  # noqa: BLE001 — best-effort; gradient is the fallback
        logger.warning("thumbnail background generation failed, using gradient: %s", exc)
        return None


def _build_html(
    title: str, eyebrow: str, accent: str, background: str | None, width: int, height: int
) -> str:
    """Self-contained card at width x height. Full-bleed background (image or an
    accent gradient) under a dark scrim, with eyebrow + title + an accent bar.
    System fonts only, so nothing to bundle."""
    safe_title = html.escape(title)
    safe_eyebrow = html.escape(eyebrow)
    if background:
        bg_layer = (
            f'background-image:url("{background}");'
            "background-size:cover;background-position:center;"
        )
    else:
        bg_layer = (
            f"background:radial-gradient(120% 120% at 15% 0%,{accent}33 0%,"
            "#0b0f1a 55%,#05070d 100%);"
        )
    eyebrow_html = (
        f'<div class="eyebrow">{safe_eyebrow}</div>' if safe_eyebrow else ""
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  html,body {{ width:{width}px; height:{height}px; overflow:hidden; }}
  .frame {{
    position:relative; width:{width}px; height:{height}px;
    {bg_layer}
    font-family:-apple-system,'Helvetica Neue',Helvetica,Arial,sans-serif;
    color:#fff;
  }}
  .scrim {{
    position:absolute; inset:0;
    background:linear-gradient(180deg,rgba(3,5,10,.15) 0%,rgba(3,5,10,.35) 45%,rgba(3,5,10,.9) 100%),
               linear-gradient(90deg,rgba(3,5,10,.75) 0%,rgba(3,5,10,.15) 60%);
  }}
  .content {{
    position:absolute; left:72px; right:72px; bottom:72px;
    display:flex; flex-direction:column; gap:22px;
  }}
  .eyebrow {{
    font-size:30px; font-weight:800; letter-spacing:6px; text-transform:uppercase;
    color:{accent};
  }}
  .title {{
    font-size:86px; font-weight:900; line-height:1.02; letter-spacing:-1px;
    text-shadow:0 4px 28px rgba(0,0,0,.55);
    display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden;
  }}
  .bar {{ width:120px; height:12px; border-radius:6px; background:{accent}; }}
</style></head>
<body><div class="frame">
  <div class="scrim"></div>
  <div class="content">
    <div class="bar"></div>
    {eyebrow_html}
    <div class="title">{safe_title}</div>
  </div>
</div></body></html>"""


async def _resolve_chrome(settings: Settings) -> str:
    global _chrome_path_cache
    if settings.chrome_headless_shell_path:
        return str(settings.chrome_headless_shell_path)
    if _chrome_path_cache:
        return _chrome_path_cache

    root = settings.hyperframes_dir.resolve()
    for args in (["--yes", _HYPERFRAMES, "browser", "path"],
                 ["--yes", _HYPERFRAMES, "browser", "ensure"]):
        code, stdout, stderr = await _run(
            "npx", args, cwd=root, timeout=settings.thumbnail_timeout_seconds
        )
        # `browser path` prints the binary path; `ensure` downloads then may print
        # it too. Take the last non-empty line that points at an existing file.
        for line in reversed(stdout.splitlines()):
            candidate = line.strip()
            if candidate and Path(candidate).exists():
                _chrome_path_cache = candidate
                return candidate
    raise ThumbnailError(
        "could not resolve chrome-headless-shell via `hyperframes browser path` "
        "(set CHROME_HEADLESS_SHELL_PATH to override)"
    )


async def generate_thumbnail(
    client: AsyncOpenAI,
    settings: Settings,
    subject_config: SubjectConfig,
    title: str,
    scenes: list[dict],
    out_path: Path,
    work_dir: Path,
    *,
    orientation: str = "vertical",
) -> Path:
    """Render the thumbnail for a job to `out_path` (a .jpg). `scenes` is the
    final composed scene list (carries the base64 cover image). `work_dir` is the
    job's artifact dir, where the intermediate .html/.png land. Size follows
    `orientation` (16:9 horizontal, 9:16 vertical). Raises ThumbnailError on any
    failure; the caller treats it as best-effort."""
    if shutil.which("ffmpeg") is None:
        raise ThumbnailError("ffmpeg is required to encode the thumbnail")

    # Resolve to absolute: artifacts_dir defaults to a relative "./artifacts", but
    # Path.as_uri() rejects relative paths and chrome's --screenshot resolves
    # against cwd — both need absolute paths to be correct.
    out_path = out_path.resolve()
    work_dir = work_dir.resolve()

    width, height = _DIMENSIONS.get(orientation, _DIMENSIONS["vertical"])

    cover = _cover(scenes)
    heading = _strip_markdown(str(cover.get("headline") or title).strip())
    eyebrow = _strip_markdown(str(cover.get("eyebrow") or "").strip())
    accent = subject_config.cap_highlight

    # Prefer a scene's own image; otherwise generate one from the topic (one call,
    # best-effort); the gradient is the last resort.
    background = _background_image(scenes)
    if background is None:
        image_size = (
            settings.image_size_horizontal
            if orientation == "horizontal"
            else settings.image_size_vertical
        )
        background = await _generate_background(client, settings, heading, accent, image_size)

    html_path = work_dir / "thumbnail.html"
    png_path = work_dir / "thumbnail.png"
    html_path.write_text(
        _build_html(heading, eyebrow, accent, background, width, height),
        encoding="utf-8",
    )

    chrome = await _resolve_chrome(settings)
    code, _, stderr = await _run(
        chrome,
        [
            "--headless", "--disable-gpu", "--hide-scrollbars",
            "--force-device-scale-factor=1", "--force-color-profile=srgb",
            f"--window-size={width},{height}",
            "--virtual-time-budget=2000",
            f"--screenshot={png_path}",
            html_path.as_uri(),
        ],
        cwd=work_dir,
        timeout=settings.thumbnail_timeout_seconds,
    )
    if code != 0 or not png_path.is_file():
        raise ThumbnailError(f"chrome screenshot failed ({code}): ...{stderr.strip()[-300:]}")

    code, _, stderr = await _run(
        "ffmpeg",
        ["-y", "-loglevel", "error", "-i", str(png_path), "-q:v", "3", str(out_path)],
        cwd=work_dir,
        timeout=settings.thumbnail_timeout_seconds,
    )
    if code != 0 or not out_path.is_file():
        raise ThumbnailError(f"ffmpeg PNG->JPG failed ({code}): ...{stderr.strip()[-300:]}")

    logger.info("thumbnail generated: %s (background=%s)", out_path, bool(background))
    return out_path
