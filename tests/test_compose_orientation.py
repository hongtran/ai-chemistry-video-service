import unittest

from app.config import Settings
from app.pipeline.steps import compose
from app.pipeline.steps.narration import _length_clause
from app.subjects import get_subject_config


class ComposeOrientationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.tech = get_subject_config("tech", self.settings)

    def test_horizontal_sets_16_9_canvas_and_slug_suffix(self) -> None:
        data = compose.build_data(
            "What is RAG?", "abcdef12-xxxx", self.tech, [], 300.0, "horizontal"
        )

        self.assertEqual(data["config"]["orientation"], "horizontal")
        self.assertEqual(data["config"]["width"], 1920)
        self.assertEqual(data["config"]["height"], 1080)
        self.assertTrue(data["config"]["slug"].endswith("-h"))

    def test_vertical_sets_9_16_canvas_without_suffix(self) -> None:
        data = compose.build_data(
            "What is RAG?", "abcdef12-xxxx", self.tech, [], 60.0, "vertical"
        )

        self.assertEqual(data["config"]["width"], 1080)
        self.assertEqual(data["config"]["height"], 1920)
        self.assertFalse(data["config"]["slug"].endswith("-h"))

    def test_cap_highlight_comes_from_subject_config(self) -> None:
        data = compose.build_data("q", "abcdef12", self.tech, [], 10.0)
        self.assertEqual(data["config"]["capHighlight"], self.tech.cap_highlight)

    def test_metadata_fields_passed_through_when_present(self) -> None:
        meta = {
            "description": "A video about RAG.",
            "hashtags": ["ai", "rag"],
            "tags": ["ai agents", "retrieval"],
        }
        data = compose.build_data("q", "abcdef12", self.tech, [], 10.0, "vertical", meta)

        self.assertEqual(data["config"]["description"], "A video about RAG.")
        self.assertEqual(data["config"]["hashtags"], ["ai", "rag"])
        self.assertEqual(data["config"]["tags"], ["ai agents", "retrieval"])

    def test_metadata_keys_omitted_when_absent_or_empty(self) -> None:
        data = compose.build_data("q", "abcdef12", self.tech, [], 10.0, "vertical", {})
        for key in ("description", "hashtags", "tags"):
            self.assertNotIn(key, data["config"])

        data = compose.build_data(
            "q", "abcdef12", self.tech, [], 10.0, "vertical",
            {"description": "", "hashtags": [], "tags": []},
        )
        for key in ("description", "hashtags", "tags"):
            self.assertNotIn(key, data["config"])


class NarrationLengthClauseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()

    def test_vertical_asks_for_seconds_horizontal_asks_for_long_form_minutes(self) -> None:
        for subject in ("chemistry", "tech"):
            config = get_subject_config(subject, self.settings)

            vertical = _length_clause(config, "vertical")
            self.assertIn("45 to 90 seconds", vertical)
            self.assertNotIn("LONG-FORM", vertical)

            horizontal = _length_clause(config, "horizontal")
            self.assertIn("5 to 10 minutes", horizontal)
            self.assertIn("LONG-FORM", horizontal)


if __name__ == "__main__":
    unittest.main()
