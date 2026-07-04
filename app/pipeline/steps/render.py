import asyncio
from pathlib import Path

from app.config import Settings


class RenderError(Exception):
    pass


async def render_video(
    settings: Settings, data_json: Path, audio_file: Path, out_path: Path
) -> None:
    script = (settings.hyperframes_dir / "scripts" / "build-video.sh").resolve()
    if not script.is_file():
        raise RenderError(f"render script not found at {script} (check HYPERFRAMES_DIR)")

    proc = await asyncio.create_subprocess_exec(
        "bash",
        str(script),
        str(data_json.resolve()),
        "--audio_file",
        str(audio_file.resolve()),
        "--out_path",
        str(out_path.resolve()),
        cwd=str(settings.hyperframes_dir.resolve()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=settings.render_timeout_seconds
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RenderError(
            f"render timed out after {settings.render_timeout_seconds}s"
        ) from None

    if proc.returncode != 0:
        tail = stderr.decode("utf-8", "replace").strip()[-800:]
        raise RenderError(f"build-video.sh exited {proc.returncode}: ...{tail}")
    if not out_path.is_file():
        out = stdout.decode("utf-8", "replace").strip()[-300:]
        raise RenderError(f"render reported success but no file at {out_path} (stdout: {out})")
