import json
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.pipeline.steps import layout_gate
from app.pipeline.steps.layout_gate import (
    LayoutGateError,
    layout_feedback,
    run_layout_gate,
    sample_times,
    scene_at,
)
from app.subjects import get_subject_config

_SCENES = [
    {"id": "s0-hook", "type": "cover", "start": 0.0, "duration": 4.0},
    {"id": "s1-flow", "type": "pipeline", "start": 4.0, "duration": 6.0},
]

_DATA = {"config": {"slug": "probe", "topic": "t", "totalDuration": 10.0}, "scenes": _SCENES}


def _issue(code: str, severity: str, time: float, selector: str = "#x") -> dict:
    return {
        "code": code,
        "severity": severity,
        "time": time,
        "selector": selector,
        "text": "some text",
        "message": "msg",
    }


class SampleTimesTests(unittest.TestCase):
    def test_never_samples_a_scene_boundary(self) -> None:
        times = sample_times(_SCENES)
        for boundary in (0.0, 4.0, 10.0):
            self.assertNotIn(boundary, times)

    def test_every_sample_lands_inside_a_scene(self) -> None:
        for t in sample_times(_SCENES):
            self.assertIsNotNone(scene_at(_SCENES, t), f"t={t} maps to no scene")

    def test_samples_after_entrance_settles_not_at_start(self) -> None:
        # Entrance animations run ~0.3-0.5s; sampling at start+0.1 measured as
        # blind (elements still faded out), so the first probe must be later.
        first = min(sample_times([{"id": "a", "start": 0.0, "duration": 6.0}]))
        self.assertGreaterEqual(first, 1.0)

    def test_two_probes_for_long_scenes_one_for_short(self) -> None:
        self.assertEqual(len(sample_times([{"id": "a", "start": 0, "duration": 6}])), 2)
        self.assertEqual(len(sample_times([{"id": "a", "start": 0, "duration": 1.5}])), 1)

    def test_zero_duration_scenes_are_skipped(self) -> None:
        self.assertEqual(sample_times([{"id": "a", "start": 0, "duration": 0}]), [])


class SceneAtTests(unittest.TestCase):
    def test_maps_time_to_the_scene_on_screen(self) -> None:
        self.assertEqual(scene_at(_SCENES, 0.0)["id"], "s0-hook")
        self.assertEqual(scene_at(_SCENES, 3.99)["id"], "s0-hook")
        self.assertEqual(scene_at(_SCENES, 4.0)["id"], "s1-flow")
        self.assertEqual(scene_at(_SCENES, 9.9)["id"], "s1-flow")

    def test_time_outside_the_timeline_maps_to_nothing(self) -> None:
        self.assertIsNone(scene_at(_SCENES, 10.0))
        self.assertIsNone(scene_at(_SCENES, -1.0))


class LayoutFeedbackTests(unittest.TestCase):
    def test_names_the_scene_field_and_keeps_the_verbatim_rule(self) -> None:
        issues = [{
            "code": "text_box_overflow", "sceneId": "s1-flow", "sceneType": "pipeline",
            "selector": "#nucleus-symbol", "text": "WayTooLong", "message": "Text extends outside.",
        }]
        text = layout_feedback(issues)

        self.assertIn("s1-flow", text)
        self.assertIn("pipeline", text)
        self.assertIn("text_box_overflow", text)
        self.assertIn("WayTooLong", text)
        self.assertIn("Do NOT change the captions' words", text)

    def test_long_text_is_truncated(self) -> None:
        issues = [{
            "code": "clipped_text", "sceneId": "a", "sceneType": "cover",
            "selector": "#h", "text": "x" * 500, "message": "m",
        }]
        self.assertNotIn("x" * 200, layout_feedback(issues))


class RunLayoutGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.config = get_subject_config("lab-management", self.settings)
        self.data_path = Path("/tmp/does-not-matter.json")

    def _patch(self, inspect_result):
        """Fake both subprocesses: populate always succeeds, inspect returns
        (code, stdout, stderr)."""
        async def fake_run(program, args, cwd, timeout):
            if program == "node":
                return (0, "", "")
            return inspect_result
        return mock.patch.object(layout_gate, "_run", side_effect=fake_run)

    def _patch_sequence(self, inspect_results):
        """Like _patch but returns a different inspect result per call, so a
        transient inspect flake followed by a clean run can be simulated."""
        results = iter(inspect_results)

        async def fake_run(program, args, cwd, timeout):
            if program == "node":
                return (0, "", "")
            return next(results)
        return mock.patch.object(layout_gate, "_run", side_effect=fake_run)

    def _report(self, issues: list[dict]) -> str:
        return json.dumps({"ok": True, "issues": issues})

    async def _gate(self, inspect_result):
        with self._patch(inspect_result):
            return await run_layout_gate(
                self.settings, self.config, _DATA, self.data_path
            )

    async def test_clean_report_passes(self) -> None:
        issues = await self._gate((0, self._report([]), ""))
        self.assertEqual(issues, [])

    async def test_only_error_severity_gates(self) -> None:
        report = self._report([
            _issue("content_overlap", "warning", 1.5),
            _issue("canvas_overflow", "info", 1.5),
            _issue("text_box_overflow", "error", 1.5),
            _issue("container_overflow", "warning", 7.0),
        ])
        issues = await self._gate((1, report, ""))

        self.assertEqual([i["code"] for i in issues], ["text_box_overflow"])

    async def test_findings_are_tagged_with_their_owning_scene(self) -> None:
        report = self._report([
            _issue("clipped_text", "error", 1.5),
            _issue("text_occluded", "error", 7.0),
        ])
        issues = await self._gate((1, report, ""))

        self.assertEqual(
            [(i["sceneId"], i["sceneType"]) for i in issues],
            [("s0-hook", "cover"), ("s1-flow", "pipeline")],
        )

    async def test_finding_outside_any_scene_is_still_reported(self) -> None:
        report = self._report([_issue("clipped_text", "error", 99.0)])
        issues = await self._gate((1, report, ""))

        self.assertEqual(issues[0]["sceneId"], "?")

    async def test_nonzero_exit_with_json_is_parsed_not_an_infra_failure(self) -> None:
        # inspect exits non-zero when errors exist but still prints its JSON.
        report = self._report([_issue("text_box_overflow", "error", 1.5)])
        issues = await self._gate((1, "noise on stdout\n" + report, ""))
        self.assertEqual(len(issues), 1)

    async def test_no_json_output_raises_infra_error(self) -> None:
        with self.assertRaises(LayoutGateError):
            await self._gate((1, "Segmentation fault", "npx blew up"))

    async def test_unparseable_json_raises_infra_error(self) -> None:
        with self.assertRaises(LayoutGateError):
            await self._gate((0, "{not valid json", ""))

    async def test_truncated_inspect_output_recovers_on_retry(self) -> None:
        # Observed in the wild: inspect's stdout was truncated mid-string
        # (~8KB of a ~49KB report), so json.loads failed. A re-run parses fine.
        # A transient inspect flake must not fail the whole job.
        good = self._report([_issue("text_box_overflow", "error", 1.5)])
        truncated = good[: len(good) // 2]  # cut off mid-JSON
        with self._patch_sequence([(0, truncated, ""), (1, good, "")]):
            issues = await run_layout_gate(
                self.settings, self.config, _DATA, self.data_path
            )
        self.assertEqual(len(issues), 1)

    async def test_persistent_inspect_failure_still_raises(self) -> None:
        truncated = self._report([]) [:10]
        with self._patch_sequence([(0, truncated, "")] * 10):
            with self.assertRaises(LayoutGateError):
                await run_layout_gate(
                    self.settings, self.config, _DATA, self.data_path
                )

    async def test_populate_failure_raises_infra_error(self) -> None:
        async def fake_run(program, args, cwd, timeout):
            if program == "node":
                return (1, "", "populate exploded")
            return (0, self._report([]), "")

        with mock.patch.object(layout_gate, "_run", side_effect=fake_run):
            with self.assertRaises(LayoutGateError) as caught:
                await run_layout_gate(self.settings, self.config, _DATA, self.data_path)
        self.assertIn("populate", str(caught.exception))

    async def test_missing_populate_script_raises_before_running_anything(self) -> None:
        config = get_subject_config("lab-management", self.settings)
        object.__setattr__(config, "renderer_template", "no-such-template")
        with self.assertRaises(LayoutGateError):
            await run_layout_gate(self.settings, config, _DATA, self.data_path)

    async def test_scenes_without_duration_skip_inspect_entirely(self) -> None:
        data = {"config": {"slug": "p"}, "scenes": [{"id": "a", "start": 0, "duration": 0}]}
        with self._patch((0, self._report([]), "")):
            self.assertEqual(
                await run_layout_gate(self.settings, self.config, data, self.data_path),
                [],
            )


if __name__ == "__main__":
    unittest.main()
