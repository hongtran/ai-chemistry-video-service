"""Real video pipeline: sequences the steps and owns every job state
transition. Expected failures surface as status=FAILED with
error_message = "<step>: <reason>"; only genuine bugs escape to the worker.

Split-first flow: the script is segmented into scenes (Pass 1) BEFORE the audio
exists; captions are derived in code so they equal the script exactly. TTS then
speaks the script, Whisper gives word timing, and each scene's typed data is
authored per-scene (Pass 2). Alignment is best-effort (never re-splits), so the
only retry loop is the layout gate, which re-authors just the offending scenes.
"""
import logging

import openai
from openai import AsyncOpenAI

from app.config import Settings
from app.domain.models import JobStatus, PipelineStep
from app.observability import job_trace
from app.pipeline.steps import author, compose, images, layout_gate, narration, render, scene_split, segment, transcribe, tts
from app.pipeline.steps.align import align_scenes
from app.pipeline.steps.layout_gate import LayoutGateError
from app.storage.artifacts import ArtifactStore
from app.storage.jobs import JobRepository
from app.subjects import get_subject_config

logger = logging.getLogger(__name__)


def _offending_ids(issues: list[dict]) -> dict[str, list[dict]]:
    """Group layout findings by the scene id they landed on."""
    grouped: dict[str, list[dict]] = {}
    for issue in issues:
        grouped.setdefault(issue.get("sceneId", "?"), []).append(issue)
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
                step = PipelineStep.SEGMENT
                script = job.script
            else:
                await self._step(job_id, PipelineStep.NARRATION)
                script = await narration.generate_script(
                    self._client, self._settings, subject_config, job.query,
                    job.orientation, job.language,
                )
            self._artifacts.save_text(job_id, "script.txt", script)

            step = await self._step(job_id, PipelineStep.SEGMENT)
            sentences = segment.build_sentence_index(script)
            self._artifacts.save_json(job_id, "sentences.json", sentences)
            scenes_index, metadata = await segment.segment_script(
                self._client, self._settings, subject_config, sentences,
                orientation=job.orientation, language=job.language,
            )
            segment.assert_three_way_equality(scenes_index, sentences, script)
            self._artifacts.save_json(
                job_id, "scenes_index.json", [s.to_dict() for s in scenes_index]
            )

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

            step = await self._step(job_id, PipelineStep.AUTHORING)
            sentences_by_index = {int(s["i"]): s["text"] for s in sentences}
            scenes = await author.author_scenes(
                self._client, self._settings, subject_config, scenes_index,
                sentences_by_index, script,
                orientation=job.orientation, language=job.language,
            )
            self._artifacts.save_json(job_id, "scenes.json", scenes)

            # Generate the actual picture for any image frame (photo/photo-split)
            # from the imagePrompt the authoring step wrote. Idempotent + best-
            # effort (a failed image keeps its placeholder). No-op for subjects
            # without image frames or when images_enabled is off.
            step = await self._step(job_id, PipelineStep.IMAGE_GEN)
            scenes = await images.resolve_images(
                self._client, self._settings, subject_config, scenes,
                orientation=job.orientation,
            )
            self._artifacts.save_json(job_id, "scenes.json", scenes)

            # Alignment runs ONCE and is best-effort — captions are the script
            # (never re-written), so re-authoring content below can't invalidate
            # the timing computed here.
            step = await self._step(job_id, PipelineStep.ALIGNMENT)
            timed_scenes = align_scenes(scenes, words, duration)

            rounds = max(1, self._settings.outer_retry_limit)
            data_path = None
            for attempt in range(1, rounds + 1):
                last = attempt == rounds

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
                    "job %s round %d/%d: %d layout error(s) — re-authoring the "
                    "offending scene(s)", job_id, attempt, rounds, len(issues),
                )
                step = await self._step(job_id, PipelineStep.AUTHORING)
                timed_scenes = await self._reauthor_offending(
                    job_id, subject_config, scenes_index, sentences_by_index,
                    script, timed_scenes, issues, job.orientation, job.language,
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

    async def _reauthor_offending(
        self,
        job_id: str,
        subject_config,
        scenes_index: list[segment.SceneIndex],
        sentences_by_index: dict[int, str],
        script: str,
        timed_scenes: list[dict],
        issues: list[dict],
        orientation: str,
        language: str,
    ) -> list[dict]:
        """Re-author every layout-flagged scene in ONE batched call (so the
        model can pick distinct, roomier types across the flagged siblings),
        preserving each scene's computed timing (captions are unchanged, so
        timing stays valid), then splice the new content back in."""
        by_id = {s.scene_id: s for s in scenes_index}
        grouped = _offending_ids(issues)
        result = [dict(s) for s in timed_scenes]
        index_by_id = {s.get("id"): i for i, s in enumerate(result)}

        offending = [by_id[sid] for sid in grouped if sid in by_id]
        if not offending:
            return result

        # Every scene's chosen type as the variety hint for the re-author.
        all_types = [s.get("type", "") for s in result]
        feedback = "\n\n".join(
            f'For scene "{sid}": {layout_gate.layout_feedback(scene_issues)}'
            for sid, scene_issues in grouped.items() if sid in by_id
        )
        reauthored = await author.reauthor_scenes(
            self._client, self._settings, subject_config, offending,
            sentences_by_index, script, all_types, feedback,
            orientation=orientation, language=language,
        )

        for scene in reauthored:
            pos = index_by_id.get(scene.get("id"))
            if pos is None:
                continue
            old = result[pos]
            # Keep the timing computed once by alignment.
            for field in ("start", "duration", "captionTiming"):
                if field in old:
                    scene[field] = old[field]
            result[pos] = scene

        # Fill images for any image frame the re-author newly introduced
        # (idempotent — scenes that already have a generated image are skipped).
        result = await images.resolve_images(
            self._client, self._settings, subject_config, result,
            orientation=orientation,
        )
        self._artifacts.save_json(job_id, "scenes.json", result)
        return result
