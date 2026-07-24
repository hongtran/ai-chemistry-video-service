import asyncio
from pathlib import Path

from app.config import Settings
from app.pipeline.steps import subproc
from app.subjects import SubjectConfig


class RenderError(Exception):
    pass


async def render_video(
    settings: Settings,
    subject_config: SubjectConfig,
    data_json: Path,
    audio_file: Path,
    out_path: Path,
) -> None:
    script = (settings.hyperframes_dir / "scripts" / "build-video.sh").resolve()
    if not script.is_file():
        raise RenderError(f"render script not found at {script} (check HYPERFRAMES_DIR)")

    try:
        returncode, stdout, stderr = await subproc.run(
            "bash",
            [
                str(script),
                str(data_json.resolve()),
                "--template",
                subject_config.renderer_template,
                "--audio_file",
                str(audio_file.resolve()),
                "--out_path",
                str(out_path.resolve()),
            ],
            cwd=str(settings.hyperframes_dir.resolve()),
            timeout=settings.render_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise RenderError(
            f"render timed out after {settings.render_timeout_seconds}s"
        ) from None

    if returncode != 0:
        tail = stderr.strip()[-800:]
        raise RenderError(f"build-video.sh exited {returncode}: ...{tail}")
    if not out_path.is_file():
        out = stdout.strip()[-300:]
        raise RenderError(
            f"render reported success but no file at {out_path} (stdout: {out})"
        )
