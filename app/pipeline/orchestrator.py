"""Real video pipeline: sequences the steps and owns every job state
transition. Expected failures surface as status=FAILED with
error_message = "<step>: <reason>"; only genuine bugs escape to the worker.

Alignment policy (user decision): if alignment fails, go back to the LLM for
ONE re-scene-split carrying the alignment error as corrective feedback, then
re-align. A second alignment failure fails the job with a clear message.
"""
import logging

import openai
from openai import AsyncOpenAI

from app.config import Settings
from app.domain.models import JobStatus, PipelineStep
from app.pipeline.steps import compose, narration, render, scene_split, transcribe, tts
from app.pipeline.steps.align import AlignmentError, align_scenes
from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository

logger = logging.getLogger(__name__)


class RealVideoPipeline:
    def __init__(
        self,
        jobs: JobRepository,
        artifacts: ArtifactStore,
        client: AsyncOpenAI,
        settings: Settings,
    ) -> None:
        self._jobs = jobs
        self._artifacts = artifacts
        self._client = client
        self._settings = settings

    async def run(self, job_id: str) -> None:
        job = await self._jobs.get(job_id)
        if job is None:
            logger.warning("job %s vanished before processing", job_id)
            return
        await self._jobs.update(job_id, status=JobStatus.PROCESSING)

        step = PipelineStep.NARRATION
        try:
            await self._step(job_id, PipelineStep.NARRATION)
            script = await narration.generate_script(self._client, self._settings, job.query)
            self._artifacts.save_text(job_id, "script.txt", script)

            step = await self._step(job_id, PipelineStep.TTS)
            audio = await tts.synthesize(self._client, self._settings, script)
            audio_path = self._artifacts.save_bytes(job_id, "narration.mp3", audio)

            step = await self._step(job_id, PipelineStep.TRANSCRIPTION)
            words, duration, transcript_text = await transcribe.transcribe_words(
                self._client, self._settings, audio_path
            )
            self._artifacts.save_json(
                job_id, "transcript.json", {"text": transcript_text, "words": words}
            )

            step = await self._step(job_id, PipelineStep.SCENE_SPLIT)
            scenes = await scene_split.generate_scenes(
                self._client, self._settings, script, transcript_text
            )
            self._artifacts.save_json(job_id, "scenes.json", scenes)

            step = await self._step(job_id, PipelineStep.ALIGNMENT)
            try:
                timed_scenes = align_scenes(scenes, words, duration)
            except AlignmentError as first_err:
                logger.warning(
                    "job %s: alignment failed (%s) — retrying via re-scene-split",
                    job_id, first_err,
                )
                step = await self._step(job_id, PipelineStep.SCENE_SPLIT)
                scenes = await scene_split.generate_scenes(
                    self._client, self._settings, script, transcript_text,
                    alignment_feedback=str(first_err),
                )
                self._artifacts.save_json(job_id, "scenes.json", scenes)
                step = await self._step(job_id, PipelineStep.ALIGNMENT)
                try:
                    timed_scenes = align_scenes(scenes, words, duration)
                except AlignmentError as second_err:
                    raise AlignmentError(
                        f"{second_err} (after 1 re-scene-split retry; "
                        f"first failure: {first_err})"
                    ) from second_err

            step = await self._step(job_id, PipelineStep.COMPOSE)
            data = compose.build_data(job.query, job_id, timed_scenes, duration)
            compose.validate_data(data, scene_split.load_scene_schema(self._settings))
            data_path = self._artifacts.save_json(job_id, "data.json", data)

            step = await self._step(job_id, PipelineStep.RENDER)
            out_path = self._artifacts.path_for(job_id, "video.mp4")
            await render.render_video(self._settings, data_path, audio_path, out_path)

            await self._jobs.update(
                job_id, status=JobStatus.COMPLETED, video_path=str(out_path)
            )
            logger.info("job %s completed: %s", job_id, out_path)

        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            # Bad/revoked API key — permanent until fixed. Never put the raw
            # exception (may embed a partial key) into the client-visible
            # error_message.
            logger.critical("job %s failed at %s: OpenAI credentials invalid: %s", job_id, step.value, exc)
            await self._jobs.update(
                job_id,
                status=JobStatus.FAILED,
                error_message=f"{step.value}: pipeline misconfigured (invalid OpenAI credentials)",
            )
        except Exception as exc:  # noqa: BLE001 — expected failures become job state
            message = f"{step.value}: {exc}"
            logger.error("job %s failed — %s", job_id, message)
            await self._jobs.update(
                job_id, status=JobStatus.FAILED, error_message=message
            )

    async def _step(self, job_id: str, step: PipelineStep) -> PipelineStep:
        await self._jobs.update(job_id, current_step=step)
        return step
