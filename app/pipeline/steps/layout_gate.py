"""Layout gate: render the candidate frames and reject unrenderable layouts.

Populates the project from the composed data.json and runs `hyperframes
inspect` over it, then maps each finding back to the scene on screen at that
timestamp so a re-split can name the offending scene and field.

Scope (deliberate): only ERROR-severity findings gate. Those are layouts
hyperframes cannot render. Warnings (text nudging past a container, cosmetic
overlap) still render fine, and some of them come from the templates
themselves rather than from anything the LLM wrote — gating on those would
fail jobs whose content is perfectly good.

Sampling: each scene is probed once its entrance animation has settled. At
`start + 0.1` the frame's elements are still faded out mid-entrance and the
inspector reports nothing at all (measured), so entrance-time samples would
make this gate blind.
"""
import asyncio
import json
import logging
from pathlib import Path

from app.config import Settings
from app.pipeline.steps import subproc
from app.subjects import SubjectConfig

logger = logging.getLogger(__name__)

# Kept in step with the pin in render_kit/scripts/build-video.sh — the gate and
# the real render must agree on the renderer, or the gate proves nothing.
_HYPERFRAMES = "hyperframes@0.7.18"

_GATING_SEVERITIES = {"error"}

# inspect occasionally emits truncated/garbled stdout (measured: ~8KB of a
# ~49KB report, cut off mid-string) — a subprocess-level flake, not a content
# problem, and a re-run parses fine. Retry a few times before treating an
# unparseable report as a hard gate failure, so a transient flake can't fail
# the whole video job.
_INSPECT_ATTEMPTS = 3


class LayoutGateError(Exception):
    """The gate could not run (populate/inspect blew up, or inspect emitted no
    JSON). Distinct from 'the layout has problems' — that's the issues list."""


def sample_times(scenes: list[dict]) -> list[float]:
    """Two probes per scene: one just after its entrance settles, one late.
    Never samples a scene boundary, where two clips briefly coexist and the
    inspector reports transition artifacts rather than real layout faults."""
    times: set[float] = set()
    for scene in scenes:
        start = float(scene.get("start", 0.0))
        duration = float(scene.get("duration", 0.0))
        if duration <= 0:
            continue
        times.add(round(start + min(1.5, duration * 0.5), 3))
        if duration > 2:
            times.add(round(start + duration * 0.85, 3))
    return sorted(times)


def scene_at(scenes: list[dict], time: float) -> dict | None:
    for scene in scenes:
        start = float(scene.get("start", 0.0))
        end = start + float(scene.get("duration", 0.0))
        if start <= time < end:
            return scene
    return None


def layout_feedback(issues: list[dict]) -> str:
    lines = "\n".join(
        f'- scene "{i.get("sceneId", "?")}" ({i.get("sceneType", "?")}): '
        f'{i.get("code")} on {i.get("selector")} — '
        f'"{str(i.get("text") or "")[:80]}" ({i.get("message")})'
        for i in issues
    )
    return (
        "Rendering that split produced layout errors — some scene content does "
        "not fit its frame:\n" + lines + "\n"
        "Fix your JSON above: shorten the offending scene's fields to the "
        "character/item limits in the schema, move detail into that scene's "
        "captions, or switch the scene to a roomier type. Do NOT change the "
        "captions' words (the verbatim rule still applies). Return the "
        "corrected full JSON object."
    )


async def _run(program: str, args: list[str], cwd: Path, timeout: int) -> tuple[int, str, str]:
    try:
        return await subproc.run(program, args, cwd=str(cwd), timeout=timeout)
    except asyncio.TimeoutError:
        raise LayoutGateError(
            f"{program} {' '.join(args[:2])} timed out after {timeout}s"
        ) from None


async def run_layout_gate(
    settings: Settings,
    subject_config: SubjectConfig,
    data: dict,
    data_path: Path,
) -> list[dict]:
    """Returns the gating (error-severity) findings, each tagged with the
    scene that owns it. Empty list = the layout is renderable."""
    root = settings.hyperframes_dir.resolve()
    populate = root / "templates" / subject_config.renderer_template / "populate.js"
    if not populate.is_file():
        raise LayoutGateError(f"populate.js not found at {populate}")

    code, _, stderr = await _run(
        "node", [str(populate), str(data_path.resolve())],
        cwd=root, timeout=settings.layout_gate_timeout_seconds,
    )
    if code != 0:
        raise LayoutGateError(f"populate.js exited {code}: ...{stderr.strip()[-500:]}")

    scenes = data.get("scenes") or []
    times = sample_times(scenes)
    if not times:
        return []

    project_dir = root / "videos" / data["config"]["slug"]
    report = None
    last_error = ""
    for attempt in range(1, _INSPECT_ATTEMPTS + 1):
        code, stdout, stderr = await _run(
            "npx", ["--yes", _HYPERFRAMES, "inspect", ".", "--json",
                    "--at", ",".join(str(t) for t in times)],
            cwd=project_dir, timeout=settings.layout_gate_timeout_seconds,
        )

        # inspect exits non-zero when error-severity findings exist, but still
        # prints its JSON, so the exit code alone can't tell a flake from real
        # findings — a parseable report is the only success signal.
        start = stdout.find("{")
        if start == -1:
            last_error = (
                f"hyperframes inspect exited {code} without JSON output: "
                f"...{(stderr or stdout).strip()[-500:]}"
            )
        else:
            try:
                report = json.loads(stdout[start:])
                break
            except json.JSONDecodeError as exc:
                last_error = f"could not parse inspect JSON: {exc}"

        logger.warning(
            "layout gate: inspect attempt %d/%d unusable (%s)%s",
            attempt, _INSPECT_ATTEMPTS, last_error,
            "; retrying" if attempt < _INSPECT_ATTEMPTS else "",
        )

    if report is None:
        raise LayoutGateError(last_error)

    gating: list[dict] = []
    ignored = 0
    for issue in report.get("issues") or []:
        if issue.get("severity") not in _GATING_SEVERITIES:
            ignored += 1
            continue
        scene = scene_at(scenes, float(issue.get("time", -1)))
        gating.append({
            **issue,
            "sceneId": scene.get("id", "?") if scene else "?",
            "sceneType": scene.get("type", "?") if scene else "?",
        })

    logger.info(
        "layout gate: %d sample(s), %d error finding(s), %d non-gating finding(s) ignored",
        len(times), len(gating), ignored,
    )
    return gating
