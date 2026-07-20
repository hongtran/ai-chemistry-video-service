"""Real video pipeline: sequences the steps and owns every job state
transition. Expected failures surface as status=FAILED with
error_message = "<step>: <reason>"; only genuine bugs escape to the worker.

Retry policy (user decision): alignment and the layout gate share ONE outer
loop of up to settings.outer_retry_limit rounds. Each round aligns, composes,
and gates the candidate; a failure at either step feeds the specific complaint
back to the section(s) that own the offending scenes and re-splits only those,
reusing each section's existing conversation. Exhausting the rounds fails the
job with the last complaint.
"""
import logging

import openai
from openai import AsyncOpenAI

from app.config import Settings
from app.domain.models import JobStatus, PipelineStep
from app.observability import job_trace
from app.pipeline.steps import compose, layout_gate, narration, render, scene_split, transcribe, tts
from app.pipeline.steps.align import AlignmentError, align_scenes
from app.pipeline.steps.layout_gate import LayoutGateError
from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository
from app.subjects import get_subject_config

logger = logging.getLogger(__name__)


def _collect_scenes(sections: list[scene_split.SectionState]) -> list[dict]:
    """The whole video's scenes, in narration order, from each section's
    latest split."""
    return [scene for section in sections for scene in (section.scenes or [])]


def _alignment_feedback(err: AlignmentError) -> str:
    return (
        "PREVIOUS ATTEMPT FAILED WORD ALIGNMENT against the recorded audio:\n"
        f"{err}\n"
        "Re-split your part so the captions, concatenated in order, reproduce "
        "the TRANSCRIPT word for word — restore any text listed as missing "
        "above (that is the actual defect; the chunk named as not matching is "
        "usually fine, it just arrives before the audio reaches it). Do not "
        "paraphrase, summarise, or skip a sentence because it echoes a nearby "
        "one. Return the corrected full JSON object."
    )


def _issues_by_section(issues: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for issue in issues:
        index = scene_split.section_index_from_scene_id(issue.get("sceneId", ""))
        grouped.setdefault(index, []).append(issue)
    return grouped


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
        # One Langfuse trace per job groups every LLM generation below under it
        # (no-op when Langfuse is disabled).
        with job_trace(self._settings, job):
            await self._process(job_id, job)

    async def _process(self, job_id: str, job) -> None:
        await self._jobs.update(job_id, status=JobStatus.PROCESSING)

        step = PipelineStep.NARRATION
        try:
            subject_config = get_subject_config(job.subject, self._settings)

            if job.input_mode == "script":
                # User-supplied narration: skip generation, use it verbatim.
                step = PipelineStep.TTS
                script = job.script
            else:
                await self._step(job_id, PipelineStep.NARRATION)
                script = await narration.generate_script(
                    self._client, self._settings, subject_config, job.query,
                    job.orientation, job.language,
                )
            self._artifacts.save_text(job_id, "script.txt", script)

            step = await self._step(job_id, PipelineStep.TTS)
            audio = await tts.synthesize(
                self._client, self._settings, script, job.language
            )
            audio_path = self._artifacts.save_bytes(job_id, "narration.mp3", audio)

            step = await self._step(job_id, PipelineStep.TRANSCRIPTION)
            words, duration, transcript_text = await transcribe.transcribe_words(
                self._client, self._settings, audio_path, job.language
            )
            self._artifacts.save_json(
                job_id, "transcript.json", {"text": transcript_text, "words": words}
            )

            step = await self._step(job_id, PipelineStep.SCENE_SPLIT)
            sections = scene_split.build_sections(
                subject_config, job.orientation, transcript_text
            )
            if len(sections) > 1:
                logger.info(
                    "job %s: long-form (%s) — splitting narration into %d sections",
                    job_id, job.orientation, len(sections),
                )
            metadata = await self._split_sections(
                subject_config, sections, job.orientation, script, transcript_text,
                job.language,
            )
            scenes = _collect_scenes(sections)
            self._artifacts.save_json(job_id, "scenes.json", scenes)

            rounds = max(1, self._settings.outer_retry_limit)
            for attempt in range(1, rounds + 1):
                last = attempt == rounds

                step = await self._step(job_id, PipelineStep.ALIGNMENT)
                try:
                    timed_scenes = align_scenes(scenes, words, duration)
                except AlignmentError as err:
                    if last:
                        raise AlignmentError(
                            f"{err} (still failing after {rounds} rounds)"
                        ) from err
                    logger.warning(
                        "job %s round %d/%d: alignment failed (%s) — re-splitting "
                        "the owning section(s)", job_id, attempt, rounds, err,
                    )
                    step = await self._step(job_id, PipelineStep.SCENE_SPLIT)
                    metadata, scenes = await self._retarget(
                        job_id, subject_config, sections, err.scene_ids,
                        _alignment_feedback(err), metadata,
                    )
                    continue

                step = await self._step(job_id, PipelineStep.COMPOSE)
                data = compose.build_data(
                    job.query, job_id, subject_config, timed_scenes, duration,
                    job.orientation, metadata,
                )
                compose.validate_data(data, scene_split.load_scene_schema(subject_config))
                data_path = self._artifacts.save_json(job_id, "data.json", data)
                # Mirrors the meta.json populate.js writes next to the render, so
                # the YouTube fields are reachable over the artifacts API too.
                self._artifacts.save_json(
                    job_id, "meta.json", compose.build_meta(data, job.query)
                )

                step = await self._step(job_id, PipelineStep.LAYOUT_GATE)
                issues = await layout_gate.run_layout_gate(
                    self._settings, subject_config, data, data_path
                )
                if not issues:
                    break
                if last:
                    detail = "; ".join(
                        f'{i.get("code")} on scene "{i.get("sceneId")}" ({i.get("selector")})'
                        for i in issues[:5]
                    )
                    raise LayoutGateError(
                        f"{len(issues)} layout error(s) persisted after {rounds} "
                        f"rounds: {detail}"
                    )
                logger.warning(
                    "job %s round %d/%d: %d layout error(s) — re-splitting the "
                    "owning section(s)", job_id, attempt, rounds, len(issues),
                )
                step = await self._step(job_id, PipelineStep.SCENE_SPLIT)
                metadata, scenes = await self._retarget_layout(
                    job_id, subject_config, sections, issues, metadata
                )

            step = await self._step(job_id, PipelineStep.RENDER)
            out_path = self._artifacts.path_for(job_id, "video.mp4")
            await render.render_video(
                self._settings, subject_config, data_path, audio_path, out_path
            )

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

    async def _split_sections(
        self,
        subject_config,
        sections: list[scene_split.SectionState],
        orientation: str,
        script: str,
        transcript_text: str,
        language: str,
    ) -> dict:
        """Split every section in narration order. Returns the video-level
        YouTube metadata (only section 0 is asked for it)."""
        metadata: dict = {}
        for section in sections:
            _, section_metadata = await scene_split.split_section(
                self._client,
                self._settings,
                subject_config,
                section,
                orientation=orientation,
                script=script,
                full_transcript=transcript_text,
                language=language,
            )
            metadata = metadata or section_metadata
        return metadata

    async def _retarget(
        self,
        job_id: str,
        subject_config,
        sections: list[scene_split.SectionState],
        scene_ids: list[str],
        feedback: str,
        metadata: dict,
    ) -> tuple[dict, list[dict]]:
        """Re-split the sections owning `scene_ids` with one shared complaint,
        then rebuild the video's scene list."""
        targets = scene_split.sections_for_scene_ids(sections, scene_ids)
        retry_metadata = await self._resplit_sections(subject_config, targets, feedback)
        # Keep earlier metadata if the retry didn't produce any — it describes
        # the same video either way.
        metadata = retry_metadata or metadata
        scenes = _collect_scenes(sections)
        self._artifacts.save_json(job_id, "scenes.json", scenes)
        return metadata, scenes

    async def _retarget_layout(
        self,
        job_id: str,
        subject_config,
        sections: list[scene_split.SectionState],
        issues: list[dict],
        metadata: dict,
    ) -> tuple[dict, list[dict]]:
        """Layout findings are per-section: each section hears only about the
        scenes it actually wrote."""
        by_index = _issues_by_section(issues)
        for index, section_issues in by_index.items():
            targets = [s for s in sections if s.index == index] or sections[:1]
            retry_metadata = await self._resplit_sections(
                subject_config, targets, layout_gate.layout_feedback(section_issues)
            )
            metadata = retry_metadata or metadata
        scenes = _collect_scenes(sections)
        self._artifacts.save_json(job_id, "scenes.json", scenes)
        return metadata, scenes

    async def _resplit_sections(
        self,
        subject_config,
        targets: list[scene_split.SectionState],
        feedback: str,
    ) -> dict:
        """Re-split only the sections a failure blamed, continuing each one's
        existing conversation so the model keeps its context for that part."""
        metadata: dict = {}
        for section in targets:
            _, section_metadata = await scene_split.split_section(
                self._client,
                self._settings,
                subject_config,
                section,
                feedback=feedback,
            )
            metadata = metadata or section_metadata
        return metadata
