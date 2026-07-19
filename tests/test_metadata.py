import unittest

from app.config import Settings
from app.pipeline.steps import compose
from app.pipeline.steps.scene_split import coerce_metadata
from app.subjects import get_subject_config


class CoerceMetadataTests(unittest.TestCase):
    def test_well_formed_metadata_passes_through(self) -> None:
        meta = coerce_metadata({
            "description": "A video about RAG.",
            "hashtags": ["ai", "rag"],
            "tags": ["ai agents", "retrieval"],
        })
        self.assertEqual(meta["description"], "A video about RAG.")
        self.assertEqual(meta["hashtags"], ["ai", "rag"])
        self.assertEqual(meta["tags"], ["ai agents", "retrieval"])

    def test_stray_hash_and_case_are_normalized(self) -> None:
        meta = coerce_metadata({"hashtags": ["#AI", "  #LLMAgents  "], "tags": ["AI Agents"]})
        self.assertEqual(meta["hashtags"], ["ai", "llmagents"])
        self.assertEqual(meta["tags"], ["ai agents"])

    def test_blank_and_non_scalar_entries_are_dropped(self) -> None:
        meta = coerce_metadata({
            "description": "   ",
            "hashtags": ["ai", "", "   ", {"nope": 1}, ["nested"]],
            "tags": [],
        })
        self.assertNotIn("description", meta)
        self.assertEqual(meta["hashtags"], ["ai"])
        self.assertNotIn("tags", meta)

    def test_garbage_input_yields_empty_dict_never_raises(self) -> None:
        for bad in (None, "a string", 42, [], ["list"], {"hashtags": "not-a-list"}):
            self.assertEqual(coerce_metadata(bad), {})

    def test_numeric_entries_are_stringified(self) -> None:
        meta = coerce_metadata({"tags": [2024, 3.5]})
        self.assertEqual(meta["tags"], ["2024", "3.5"])


class BuildMetaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tech = get_subject_config("tech", Settings())

    def test_meta_mirrors_config_metadata(self) -> None:
        data = compose.build_data(
            "What is RAG?", "abcdef12-xxxx", self.tech, [], 60.0, "vertical",
            {"description": "About RAG.", "hashtags": ["ai"], "tags": ["ai agents"]},
        )
        meta = compose.build_meta(data, "What is RAG?")

        self.assertEqual(meta["id"], data["config"]["slug"])
        self.assertEqual(meta["name"], "What is RAG?")
        self.assertEqual(meta["description"], "About RAG.")
        self.assertEqual(meta["hashtags"], ["ai"])
        self.assertEqual(meta["tags"], ["ai agents"])
        self.assertIn("createdAt", meta)

    def test_absent_metadata_keys_stay_absent(self) -> None:
        data = compose.build_data("q", "abcdef12", self.tech, [], 60.0, "vertical", {})
        meta = compose.build_meta(data, "q")

        for key in ("description", "hashtags", "tags"):
            self.assertNotIn(key, meta)
        self.assertEqual(meta["name"], "q")
        self.assertIsNotNone(meta["id"])


if __name__ == "__main__":
    unittest.main()
