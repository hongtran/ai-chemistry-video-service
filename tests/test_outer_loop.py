"""Outer-loop behaviour (split-first pipeline): alignment runs ONCE and never
retries (best-effort by design); only the layout gate loops, up to
settings.outer_retry_limit rounds, re-authoring only the offending scene(s)."""
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.domain.models import Job, JobStatus, PipelineStep
from app.languages import DEFAULT_LANGUAGE
from app.pipeline import orchestrator as orch
from app.pipeline.orchestrator import RealVideoPipeline
from app.pipeline.steps import layout_gate
from app.pipeline.steps.segment import SceneIndex
from app.storage.jobs import InMemoryJobRepository


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
        self.reauthor_feedback: list[str] = []

    def _stack(self, *, gate_results, timed_scene=None):
        """Patch every step around the outer loop. gate_results is a per-round
        list of layout-gate outcomes (an Exception is raised, else returned)."""
        gates = list(gate_results)
        scenes_index = [SceneIndex(scene_id="hook", idx_sentences=[1], captions=["a b", "c d"])]
        timed = timed_scene or _timed("hook")

        async def fake_segment_script(client, settings, subject_config, sentences, **kw):
            return scenes_index, {"description": "d"}

        async def fake_author_scenes(client, settings, subject_config, idx, by_index, script, **kw):
            return [_scene("hook")]

        async def fake_reauthor_scenes(client, settings, subject_config, offending, by_index,
                                        script, all_types, feedback, **kw):
            self.reauthor_feedback.append(feedback)
            return [_scene(s.scene_id) for s in offending]

        def fake_align(scenes, words, duration, language=DEFAULT_LANGUAGE):
            return [dict(timed)]

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
            mock.patch.object(orch.segment, "segment_script", fake_segment_script),
            mock.patch.object(orch.segment, "assert_three_way_equality", lambda *a, **k: None),
            mock.patch.object(orch.tts, "synthesize", mock.AsyncMock(return_value=b"mp3")),
            mock.patch.object(orch.transcribe, "transcribe_words",
                              mock.AsyncMock(return_value=([{"text": "a", "start": 0, "end": 1}], 4.0, "a b c d"))),
            mock.patch.object(orch.author, "author_scenes", fake_author_scenes),
            mock.patch.object(orch.author, "reauthor_scenes", fake_reauthor_scenes),
            mock.patch.object(orch, "align_scenes", fake_align),
            mock.patch.object(orch.layout_gate, "run_layout_gate", fake_gate),
            mock.patch.object(orch.compose, "validate_data", lambda d, s: None),
            mock.patch.object(orch.render, "render_video", mock.AsyncMock()),
            mock.patch.object(self.pipeline, "_step", spy_step),
        )

    async def _run(self, *, gate_results):
        patches = self._stack(gate_results=gate_results)
        for p in patches:
            p.start()
        try:
            await self.pipeline.run(self.job.id)
        finally:
            for p in patches:
                p.stop()
        return await self.jobs.get(self.job.id)

    async def test_clean_first_round_renders_without_retrying(self) -> None:
        job = await self._run(gate_results=[[]])

        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(self.reauthor_feedback, [])
        self.assertEqual(self.steps.count(PipelineStep.ALIGNMENT.value), 1)
        self.assertEqual(self.steps.count(PipelineStep.LAYOUT_GATE.value), 1)
        self.assertIn(PipelineStep.RENDER.value, self.steps)

    async def test_alignment_runs_once_never_retries(self) -> None:
        job = await self._run(gate_results=[[]])
        # Alignment isn't in the per-round loop at all — one call regardless.
        self.assertEqual(self.steps.count(PipelineStep.ALIGNMENT.value), 1)
        self.assertEqual(job.status, JobStatus.COMPLETED)

    async def test_layout_errors_recover_on_the_next_round(self) -> None:
        issue = {"code": "text_box_overflow", "sceneId": "hook", "sceneType": "cover",
                 "selector": "#h", "text": "long", "message": "m"}
        job = await self._run(gate_results=[[issue], []])

        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(len(self.reauthor_feedback), 1)
        self.assertIn("layout errors", self.reauthor_feedback[0])
        self.assertEqual(self.steps.count(PipelineStep.LAYOUT_GATE.value), 2)
        # Timing survives the re-author untouched.
        final_scenes = self.artifacts.saved["scenes.json"]
        self.assertEqual(final_scenes[0]["start"], 0.0)
        self.assertEqual(final_scenes[0]["duration"], 4.0)

    async def test_persistent_layout_errors_fail_the_job_after_all_rounds(self) -> None:
        issue = {"code": "text_box_overflow", "sceneId": "hook", "sceneType": "cover",
                 "selector": "#h", "text": "long", "message": "m"}
        job = await self._run(gate_results=[[issue]] * 3)

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertTrue(job.error_message.startswith("layout_gate:"), job.error_message)
        self.assertIn("after 3 rounds", job.error_message)
        self.assertNotIn(PipelineStep.RENDER.value, self.steps)
        # Two corrective rounds, then the third gives up.
        self.assertEqual(len(self.reauthor_feedback), 2)

    async def test_gate_infra_failure_fails_the_job_immediately(self) -> None:
        job = await self._run(gate_results=[layout_gate.LayoutGateError("npx exploded")])

        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertTrue(job.error_message.startswith("layout_gate:"), job.error_message)
        self.assertIn("npx exploded", job.error_message)


class OffendingIdsGroupingTests(unittest.TestCase):
    def test_issues_group_by_scene_id(self) -> None:
        grouped = orch._offending_ids([
            {"sceneId": "hook"}, {"sceneId": "flow"}, {"sceneId": "hook"},
        ])
        self.assertEqual(sorted(grouped), ["flow", "hook"])
        self.assertEqual(len(grouped["hook"]), 2)


if __name__ == "__main__":
    unittest.main()
