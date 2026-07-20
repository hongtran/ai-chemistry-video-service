"""Outer-loop behaviour: alignment + layout gate share up to
settings.outer_retry_limit rounds, and each failure re-splits only the
section(s) that own the offending scenes."""
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.domain.models import JobStatus, PipelineStep
from app.pipeline import orchestrator as orch
from app.pipeline.orchestrator import RealVideoPipeline
from app.pipeline.steps import layout_gate, scene_split
from app.pipeline.steps.align import AlignmentError
from app.storage.jobs import InMemoryJobRepository
from app.domain.models import Job


def _scene(sid: str) -> dict:
    return {
        "id": sid, "type": "cover", "eyebrow": "E", "headline": "H",
        "captions": ["a b", "c d"],
    }


def _timed(sid: str) -> dict:
    return {**_scene(sid), "start": 0.0, "duration": 4.0,
            "captionTiming": [{"text": "a b", "start": 0, "end": 2,
                               "words": [{"text": "a", "start": 0, "end": 1}]}]}


class FakeArtifacts:
    def __init__(self) -> None:
        self.saved: dict[str, object] = {}

    def path_for(self, job_id, name):
        return Path(f"/tmp/{job_id}/{name}")

    def save_bytes(self, job_id, name, data):
        self.saved[name] = data
        return self.path_for(job_id, name)

    def save_text(self, job_id, name, text):
        self.saved[name] = text
        return self.path_for(job_id, name)

    def save_json(self, job_id, name, obj):
        self.saved[name] = obj
        return self.path_for(job_id, name)


class OuterLoopTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = Settings()
        self.jobs = InMemoryJobRepository()
        self.artifacts = FakeArtifacts()
        self.job = Job(query="What is RAG?", subject="tech", orientation="vertical")
        await self.jobs.create(self.job)
        self.pipeline = RealVideoPipeline(
            self.jobs, self.artifacts, client=object(), settings=self.settings
        )
        self.steps: list[str] = []
        self.resplit_feedback: list[str] = []

    def _stack(self, *, align_results, gate_results):
        """Patch every step around the outer loop. align_results/gate_results
        are per-round outcomes (an Exception is raised, else returned)."""
        aligns, gates = list(align_results), list(gate_results)

        async def fake_split_sections(subject_config, sections, orientation, script, transcript, language):
            for s in sections:
                s.scenes = [_scene(f"{s.id_prefix}hook")]
            return {"description": "d"}

        async def fake_resplit(subject_config, targets, feedback):
            self.resplit_feedback.append(feedback)
            for s in targets:
                s.scenes = [_scene(f"{s.id_prefix}hook")]
            return {}

        def fake_align(scenes, words, duration):
            out = aligns.pop(0)
            if isinstance(out, Exception):
                raise out
            return out

        async def fake_gate(settings, subject_config, data, data_path):
            out = gates.pop(0)
            if isinstance(out, Exception):
                raise out
            return out

        original_step = self.pipeline._step

        async def spy_step(job_id, step):
            self.steps.append(step.value)
            return await original_step(job_id, step)

        return (
            mock.patch.object(orch.narration, "generate_script",
                              mock.AsyncMock(return_value="a script " * 20)),
            mock.patch.object(orch.tts, "synthesize", mock.AsyncMock(return_value=b"mp3")),
            mock.patch.object(orch.transcribe, "transcribe_words",
                              mock.AsyncMock(return_value=([{"text": "a", "start": 0, "end": 1}], 4.0, "a b c d"))),
            mock.patch.object(self.pipeline, "_split_sections", fake_split_sections),
            mock.patch.object(self.pipeline, "_resplit_sections", fake_resplit),
            mock.patch.object(orch, "align_scenes", fake_align),
            mock.patch.object(orch.layout_gate, "run_layout_gate", fake_gate),
            mock.patch.object(orch.compose, "validate_data", lambda d, s: None),
            mock.patch.object(orch.render, "render_video", mock.AsyncMock()),
            mock.patch.object(self.pipeline, "_step", spy_step),
        )

    async def _run(self, *, align_results, gate_results):
        patches = self._stack(align_results=align_results, gate_results=gate_results)
        for p in patches:
            p.start()
        try:
            await self.pipeline.run(self.job.id)
        finally:
            for p in patches:
                p.stop()
        return await self.jobs.get(self.job.id)

    async def test_clean_first_round_renders_without_retrying(self) -> None:
        job = await self._run(align_results=[[_timed("hook")]], gate_results=[[]])

        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(self.resplit_feedback, [])
        self.assertEqual(self.steps.count(PipelineStep.ALIGNMENT.value), 1)
        self.assertEqual(self.steps.count(PipelineStep.LAYOUT_GATE.value), 1)
        self.assertIn(PipelineStep.RENDER.value, self.steps)

    async def test_alignment_failure_recovers_on_the_next_round(self) -> None:
        job = await self._run(
            align_results=[AlignmentError("drifted", ["hook"]), [_timed("hook")]],
            gate_results=[[]],
        )

        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(len(self.resplit_feedback), 1)
        self.assertIn("FAILED WORD ALIGNMENT", self.resplit_feedback[0])
        self.assertEqual(self.steps.count(PipelineStep.ALIGNMENT.value), 2)

    async def test_layout_errors_recover_on_the_next_round(self) -> None:
        issue = {"code": "text_box_overflow", "sceneId": "hook", "sceneType": "cover",
                 "selector": "#h", "text": "long", "message": "m"}
        job = await self._run(
            align_results=[[_timed("hook")], [_timed("hook")]],
            gate_results=[[issue], []],
        )

        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(len(self.resplit_feedback), 1)
        self.assertIn("layout errors", self.resplit_feedback[0])
        self.assertEqual(self.steps.count(PipelineStep.LAYOUT_GATE.value), 2)

    async def test_persistent_layout_errors_fail_the_job_after_all_rounds(self) -> None:
        issue = {"code": "text_box_overflow", "sceneId": "hook", "sceneType": "cover",
                 "selector": "#h", "text": "long", "message": "m"}
        job = await self._run(
            align_results=[[_timed("hook")]] * 3,
            gate_results=[[issue]] * 3,
        )

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertTrue(job.error_message.startswith("layout_gate:"), job.error_message)
        self.assertIn("after 3 rounds", job.error_message)
        self.assertNotIn(PipelineStep.RENDER.value, self.steps)
        # Two corrective rounds, then the third gives up.
        self.assertEqual(len(self.resplit_feedback), 2)

    async def test_persistent_alignment_failure_fails_the_job(self) -> None:
        job = await self._run(
            align_results=[AlignmentError("drifted", ["hook"])] * 3,
            gate_results=[],
        )

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertTrue(job.error_message.startswith("alignment:"), job.error_message)
        self.assertIn("after 3 rounds", job.error_message)

    async def test_gate_infra_failure_fails_the_job_immediately(self) -> None:
        job = await self._run(
            align_results=[[_timed("hook")]],
            gate_results=[layout_gate.LayoutGateError("npx exploded")],
        )

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertTrue(job.error_message.startswith("layout_gate:"), job.error_message)
        self.assertIn("npx exploded", job.error_message)


class IssueGroupingTests(unittest.TestCase):
    def test_issues_group_by_owning_section(self) -> None:
        grouped = orch._issues_by_section([
            {"sceneId": "s0-hook"}, {"sceneId": "s2-flow"}, {"sceneId": "s0-intro"},
        ])
        self.assertEqual(sorted(grouped), [0, 2])
        self.assertEqual(len(grouped[0]), 2)

    def test_unprefixed_ids_group_to_section_zero(self) -> None:
        grouped = orch._issues_by_section([{"sceneId": "hook"}, {"sceneId": "?"}])
        self.assertEqual(list(grouped), [0])
        self.assertEqual(len(grouped[0]), 2)


if __name__ == "__main__":
    unittest.main()
