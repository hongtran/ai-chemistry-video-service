"""Credential-free demo pipeline (Phase 1, kept permanently behind
USE_STUB_PIPELINE): walks every step with short sleeps and writes placeholder
artifacts so status polling, listing, artifact download, and the video
endpoint are all demoable without OpenAI or a render environment."""
import asyncio

from app.domain.models import JobStatus, PipelineStep
from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository

# Smallest useful placeholder: not a playable mp4, just proves the download path.
_PLACEHOLDER_VIDEO = b"stub video placeholder (real mp4 arrives with the Phase 2 pipeline)\n"


class StubPipeline:
    def __init__(
        self,
        jobs: JobRepository,
        artifacts: ArtifactStore,
        step_delay: float = 1.0,
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._step_delay = step_delay

    async def run(self, job_id: str) -> None:
        job = await self._jobs.get(job_id)
        if job is None:
            return
        await self._jobs.update(job_id, status=JobStatus.PROCESSING)

        for step in PipelineStep:
            await self._jobs.update(job_id, current_step=step)
            await asyncio.sleep(self._step_delay)
            self._write_placeholder(job_id, step, job.query, job.orientation)

        video_path = self._artifacts.path_for(job_id, "video.mp4")
        await self._jobs.update(
            job_id,
            status=JobStatus.COMPLETED,
            video_path=str(video_path),
        )

    def _write_placeholder(
        self, job_id: str, step: PipelineStep, query: str, orientation: str = "vertical"
    ) -> None:
        if step is PipelineStep.NARRATION:
            self._artifacts.save_text(
                job_id, "script.txt", f"[stub] narration script for: {query}\n"
            )
        elif step is PipelineStep.TTS:
            self._artifacts.save_bytes(job_id, "narration.mp3", b"stub audio\n")
        elif step is PipelineStep.TRANSCRIPTION:
            self._artifacts.save_json(
                job_id,
                "transcript.json",
                {"text": "stub.", "words": [{"text": "stub", "start": 0.0, "end": 0.5}]},
            )
        elif step is PipelineStep.SEGMENT:
            self._artifacts.save_json(
                job_id,
                "scenes_index.json",
                [{"scene_id": "scene-1", "idx_sentences": [1], "captions": [query]}],
            )
        elif step is PipelineStep.AUTHORING:
            self._artifacts.save_json(job_id, "scenes.json", [{"type": "stub", "caption": query}])
        elif step is PipelineStep.COMPOSE:
            horizontal = orientation == "horizontal"
            self._artifacts.save_json(
                job_id,
                "data.json",
                {
                    "config": {
                        "orientation": orientation,
                        "width": 1920 if horizontal else 1080,
                        "height": 1080 if horizontal else 1920,
                    },
                    "scenes": [],
                },
            )
            self._artifacts.save_json(
                job_id,
                "meta.json",
                {
                    "id": "stub-slug",
                    "name": query,
                    "description": f"[stub] YouTube description for: {query}",
                    "hashtags": ["stub"],
                    "tags": ["stub video"],
                },
            )
        elif step is PipelineStep.RENDER:
            self._artifacts.save_bytes(job_id, "video.mp4", _PLACEHOLDER_VIDEO)
