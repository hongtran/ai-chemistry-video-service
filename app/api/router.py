import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from app.api.schemas import CreateVideoRequest, CreateVideoResponse, JobDetail, JobSummary
from app.cleanup import purge_job
from app.domain.models import Job, JobStatus
from app.llm.client import (
    GuardMisconfiguredError,
    GuardUnavailableError,
    NormalizerMisconfiguredError,
    NormalizerUnavailableError,
    ScriptNormalizer,
    SubjectGuard,
)
from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository
from app.subjects import get_subject_config
from app.worker.queue import JobQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["videos"])

# Only artifacts the pipeline produces are downloadable — no path traversal.
ALLOWED_ARTIFACTS = {
    "script.txt",
    "sentences.json",
    "scenes_index.json",
    "narration.mp3",
    "transcript.json",
    "scenes.json",
    "data.json",
    "meta.json",
    "video.mp4",
    "thumbnail.jpg",
}


def _deps(
    request: Request,
) -> tuple[JobRepository, ArtifactStore, JobQueue, SubjectGuard, ScriptNormalizer]:
    s = request.app.state
    return s.jobs, s.artifacts, s.queue, s.guard, s.normalizer


def _title_from_script(script: str, limit: int = 80) -> str:
    """A short single-line title for a script-mode job (list display + compose)."""
    first_line = next((ln.strip() for ln in script.splitlines() if ln.strip()), "")
    title = " ".join(first_line.split())
    return f"{title[: limit - 1]}…" if len(title) > limit else title


@router.post(
    "/videos", response_model=CreateVideoResponse, status_code=status.HTTP_202_ACCEPTED
)
async def request_video(body: CreateVideoRequest, request: Request) -> CreateVideoResponse:
    jobs, _, queue, guard, normalizer = _deps(request)
    settings = request.app.state.settings
    subject_config = get_subject_config(body.subject, settings)

    if body.input_mode == "script":
        # User supplies the narration: enforce the per-orientation cap (on the raw
        # input, before normalization) and skip the subject-relevance guard (trusted
        # content). A single LLM call then cleans formatting (headings, markdown,
        # bullets) into plain spoken prose and derives a title; on failure we fall
        # back to the raw script + a heuristic title so the user is never blocked.
        script = (body.script or "").strip()
        max_len = (
            settings.max_script_length_short
            if body.orientation == "vertical"
            else settings.max_script_length_long
        )
        if not script:
            raise HTTPException(status_code=400, detail="Script must not be empty.")
        if len(script) > max_len:
            raise HTTPException(
                status_code=400, detail=f"Script too long (max {max_len} characters)."
            )
        title = _title_from_script(script)
        try:
            result = await normalizer.normalize(script, body.subject, body.language)
            script = result.narration.strip() or script
            title = result.title.strip() or title
        except (NormalizerUnavailableError, NormalizerMisconfiguredError) as exc:
            logger.warning("script normalization failed, using raw script: %s", exc)
        job = Job(
            input_mode="script",
            query=title,
            script=script,
            subject=body.subject,
            orientation=body.orientation,
            language=body.language,
        )
    else:
        query = (body.query or "").strip()
        max_len = settings.max_query_length
        if not query:
            raise HTTPException(status_code=400, detail="Query must not be empty.")
        if len(query) > max_len:
            raise HTTPException(
                status_code=400, detail=f"Query too long (max {max_len} characters)."
            )

        try:
            verdict = await guard.check(query, body.subject)
        except GuardMisconfiguredError as exc:
            raise HTTPException(
                status_code=500,
                detail="Subject validation is misconfigured. Contact support.",
            ) from exc
        except GuardUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Subject validation is temporarily unavailable: {exc}",
            ) from exc
        if not verdict.is_valid:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Query is not a {subject_config.display_name} concept: "
                    f"{verdict.reason}"
                ),
            )

        job = Job(
            input_mode="topic",
            query=query,
            subject=body.subject,
            orientation=body.orientation,
            language=body.language,
        )

    await jobs.create(job)
    await queue.enqueue(job.id)
    return CreateVideoResponse(
        id=job.id,
        input_mode=job.input_mode,
        subject=job.subject,
        orientation=job.orientation,
        language=job.language,
        status=job.status,
    )


@router.get("/videos", response_model=list[JobSummary])
async def list_videos(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[JobSummary]:
    jobs, _, _, _, _ = _deps(request)

    parsed_status: JobStatus | None = None
    if status_filter is not None:
        try:
            parsed_status = JobStatus(status_filter.upper())
        except ValueError:
            valid = ", ".join(s.value for s in JobStatus)
            raise HTTPException(
                status_code=400, detail=f"Invalid status '{status_filter}'. Valid: {valid}."
            ) from None

    return [JobSummary.from_job(j) for j in await jobs.list(parsed_status)]


@router.get("/videos/{job_id}", response_model=JobDetail)
async def get_video_job(job_id: str, request: Request) -> JobDetail:
    jobs, artifacts, _, _, _ = _deps(request)
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobDetail.from_job(job, artifacts.list_names(job_id))


@router.delete("/videos/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_video_job(job_id: str, request: Request) -> Response:
    """Delete a job and its on-disk artifacts. Allowed in any state; a job
    still processing simply has its record dropped and the pipeline's later
    updates become no-ops. YouTube upload records are left intact."""
    jobs, artifacts, _, _, _ = _deps(request)
    if not await purge_job(job_id, jobs, artifacts):
        raise HTTPException(status_code=404, detail="Job not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/videos/{job_id}/video")
async def download_video(job_id: str, request: Request) -> FileResponse:
    jobs, artifacts, _, _, _ = _deps(request)
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status != JobStatus.COMPLETED or not job.video_path:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Video is not ready.",
                "status": job.status.value,
                "current_step": job.current_step.value if job.current_step else None,
                "error_message": job.error_message,
            },
        )
    if not artifacts.exists(job_id, "video.mp4"):
        raise HTTPException(status_code=404, detail="Video file missing from artifact store.")
    return FileResponse(
        job.video_path, media_type="video/mp4", filename=f"{job.subject}-{job_id}.mp4"
    )


@router.get("/videos/{job_id}/artifacts/{name}")
async def download_artifact(job_id: str, name: str, request: Request) -> FileResponse:
    jobs, artifacts, _, _, _ = _deps(request)
    if name not in ALLOWED_ARTIFACTS:
        allowed = ", ".join(sorted(ALLOWED_ARTIFACTS))
        raise HTTPException(status_code=400, detail=f"Unknown artifact. Allowed: {allowed}.")
    if await jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not artifacts.exists(job_id, name):
        raise HTTPException(status_code=404, detail=f"Artifact '{name}' not produced yet.")
    return FileResponse(artifacts.path_for(job_id, name), filename=name)
