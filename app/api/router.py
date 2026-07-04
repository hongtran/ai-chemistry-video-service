from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from app.api.schemas import CreateVideoRequest, CreateVideoResponse, JobDetail, JobSummary
from app.domain.models import Job, JobStatus
from app.llm.client import ChemistryGuard, GuardMisconfiguredError, GuardUnavailableError
from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository
from app.worker.queue import JobQueue

router = APIRouter(prefix="/api/v1", tags=["videos"])

# Only artifacts the pipeline produces are downloadable — no path traversal.
ALLOWED_ARTIFACTS = {
    "script.txt",
    "narration.mp3",
    "transcript.json",
    "scenes.json",
    "data.json",
    "video.mp4",
}


def _deps(request: Request) -> tuple[JobRepository, ArtifactStore, JobQueue, ChemistryGuard]:
    s = request.app.state
    return s.jobs, s.artifacts, s.queue, s.guard


@router.post(
    "/videos", response_model=CreateVideoResponse, status_code=status.HTTP_202_ACCEPTED
)
async def request_video(body: CreateVideoRequest, request: Request) -> CreateVideoResponse:
    jobs, _, queue, guard = _deps(request)

    query = body.query.strip()
    max_len = request.app.state.settings.max_query_length
    if not query:
        raise HTTPException(status_code=400, detail="Query must not be empty.")
    if len(query) > max_len:
        raise HTTPException(
            status_code=400, detail=f"Query too long (max {max_len} characters)."
        )

    try:
        verdict = await guard.check(query)
    except GuardMisconfiguredError as exc:
        raise HTTPException(
            status_code=500,
            detail="Chemistry validation is misconfigured. Contact support.",
        ) from exc
    except GuardUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Chemistry validation is temporarily unavailable: {exc}",
        ) from exc
    if not verdict.is_chemistry:
        raise HTTPException(
            status_code=400,
            detail=f"Query is not a chemistry concept: {verdict.reason}",
        )

    job = Job(query=query)
    await jobs.create(job)
    await queue.enqueue(job.id)
    return CreateVideoResponse(id=job.id, status=job.status)


@router.get("/videos", response_model=list[JobSummary])
async def list_videos(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[JobSummary]:
    jobs, _, _, _ = _deps(request)

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
    jobs, artifacts, _, _ = _deps(request)
    job = await jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return JobDetail.from_job(job, artifacts.list_names(job_id))


@router.get("/videos/{job_id}/video")
async def download_video(job_id: str, request: Request) -> FileResponse:
    jobs, artifacts, _, _ = _deps(request)
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
        job.video_path, media_type="video/mp4", filename=f"chemistry-{job_id}.mp4"
    )


@router.get("/videos/{job_id}/artifacts/{name}")
async def download_artifact(job_id: str, name: str, request: Request) -> FileResponse:
    jobs, artifacts, _, _ = _deps(request)
    if name not in ALLOWED_ARTIFACTS:
        allowed = ", ".join(sorted(ALLOWED_ARTIFACTS))
        raise HTTPException(status_code=400, detail=f"Unknown artifact. Allowed: {allowed}.")
    if await jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not artifacts.exists(job_id, name):
        raise HTTPException(status_code=404, detail=f"Artifact '{name}' not produced yet.")
    return FileResponse(artifacts.path_for(job_id, name), filename=name)
