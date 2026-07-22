"""Pass 1 (segment.py): the LLM decides only scene boundaries; every structural
guarantee — partition correctness, caption derivation, three-way equality — is
enforced in code, never by re-prompting the model."""
import json
import unittest
from types import SimpleNamespace

from app.config import Settings
from app.pipeline.steps import segment
from app.pipeline.steps.segment import SceneIndex
from app.subjects import get_subject_config


class FakeClient:
    """Minimal stand-in for AsyncOpenAI: replays queued JSON responses and
    records the messages each call received."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, response_format, temperature=None):
        self.calls.append([dict(m) for m in messages])
        body = self._responses.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=body))]
        )


class CoercePartitionTests(unittest.TestCase):
    def test_clean_input_is_kept_as_is(self) -> None:
        ranges = segment.coerce_partition([[1], [3], [5]], 6)
        self.assertEqual(ranges, [[1, 2], [3, 4], [5, 6]])

    def test_missing_start_is_forced_to_one(self) -> None:
        ranges = segment.coerce_partition([[2], [4]], 5)
        self.assertEqual(ranges[0][0], 1)
        # every sentence covered exactly once, in order
        flat = [i for r in ranges for i in r]
        self.assertEqual(flat, list(range(1, 6)))

    def test_duplicate_starts_are_deduplicated(self) -> None:
        ranges = segment.coerce_partition([[1], [1], [3]], 4)
        flat = [i for r in ranges for i in r]
        self.assertEqual(flat, [1, 2, 3, 4])
        self.assertEqual(len(ranges), 2)

    def test_out_of_range_starts_are_dropped(self) -> None:
        ranges = segment.coerce_partition([[1], [10]], 4)
        self.assertEqual(ranges, [[1, 2, 3, 4]])

    def test_out_of_order_starts_are_sorted(self) -> None:
        ranges = segment.coerce_partition([[5], [1], [3]], 6)
        self.assertEqual(ranges, [[1, 2], [3, 4], [5, 6]])

    def test_empty_groups_fall_back_to_even_split(self) -> None:
        ranges = segment.coerce_partition([], 6)
        flat = [i for r in ranges for i in r]
        self.assertEqual(flat, list(range(1, 7)))
        self.assertEqual(ranges[0][0], 1)


class DeriveCaptionsTests(unittest.TestCase):
    def test_chunks_are_two_to_five_words(self) -> None:
        sentence = "This is a fairly long test sentence with many words in it."
        chunks = segment.derive_captions([sentence])
        for c in chunks:
            self.assertGreaterEqual(len(c.split()), 1)
            self.assertLessEqual(len(c.split()), 5)

    def test_chunks_reconstruct_the_input_exactly(self) -> None:
        sentences = ["Hello world.", "This is a test.", "Another one here."]
        chunks = segment.derive_captions(sentences)
        self.assertEqual(" ".join(chunks).split(), " ".join(sentences).split())

    def test_respects_the_char_cap(self) -> None:
        sentence = " ".join(["antidisestablishmentarianism"] * 6) + "."
        chunks = segment.derive_captions([sentence])
        for c in chunks:
            self.assertLessEqual(len(c), 55 + len("antidisestablishmentarianism"))
            # every chunk still holds at least one whole word
            self.assertGreaterEqual(len(c.split()), 1)


class AssertThreeWayEqualityTests(unittest.TestCase):
    def test_matching_captions_sentences_script_pass(self) -> None:
        sentences = [{"i": 1, "text": "Hello world."}, {"i": 2, "text": "Bye now."}]
        scenes_index = [
            SceneIndex("scene-1", [1], ["Hello", "world."]),
            SceneIndex("scene-2", [2], ["Bye now."]),
        ]
        script = "Hello world. Bye now."
        segment.assert_three_way_equality(scenes_index, sentences, script)  # no raise

    def test_dropped_word_raises(self) -> None:
        sentences = [{"i": 1, "text": "Hello world."}]
        scenes_index = [SceneIndex("scene-1", [1], ["Hello"])]  # dropped "world."
        with self.assertRaises(segment.SegmentError):
            segment.assert_three_way_equality(scenes_index, sentences, "Hello world.")


class BuildSentenceIndexTests(unittest.TestCase):
    def test_numbers_every_sentence_from_one(self) -> None:
        sentences = segment.build_sentence_index("One. Two! Three?")
        self.assertEqual([s["i"] for s in sentences], [1, 2, 3])
        self.assertEqual(sentences[0]["text"], "One.")


class SegmentScriptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = get_subject_config("tech", Settings())

    async def test_whole_script_single_window(self) -> None:
        settings = Settings(segment_sentence_window=40)
        sentences = segment.build_sentence_index(
            "One thing happens. Then another thing happens. Finally it ends."
        )
        payload = json.dumps({
            "scenes": [{"idx_sentences": [1]}, {"idx_sentences": [2, 3]}],
            "config": {"description": "d", "hashtags": ["#Ai"], "tags": ["ai video"]},
        })
        client = FakeClient([payload])

        scenes_index, metadata = await segment.segment_script(
            client, settings, self.config, sentences, orientation="vertical",
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual([s.idx_sentences for s in scenes_index], [[1], [2, 3]])
        self.assertEqual(metadata["description"], "d")
        self.assertEqual(metadata["hashtags"], ["ai"])
        # Captions derived in code must reconstruct the assigned sentences.
        segment.assert_three_way_equality(
            scenes_index, sentences,
            "One thing happens. Then another thing happens. Finally it ends.",
        )

    async def test_long_form_windows_concatenate_in_global_order(self) -> None:
        settings = Settings(segment_sentence_window=3)
        text = " ".join(f"Sentence number {i}." for i in range(1, 6))  # 5 sentences
        sentences = segment.build_sentence_index(text)
        self.assertEqual(len(sentences), 5)

        # Window 1 (sentences 1-3): model answers with GLOBAL numbering.
        w1 = json.dumps({
            "scenes": [{"idx_sentences": [1]}, {"idx_sentences": [3]}],
            "config": {"description": "video description"},
        })
        # Window 2 (sentences 4-5): model answers with LOCAL (1-based) numbering.
        w2 = json.dumps({"scenes": [{"idx_sentences": [1]}]})
        client = FakeClient([w1, w2])

        scenes_index, metadata = await segment.segment_script(
            client, settings, self.config, sentences, orientation="vertical",
        )

        self.assertEqual(len(client.calls), 2)
        flat = [i for s in scenes_index for i in s.idx_sentences]
        self.assertEqual(flat, [1, 2, 3, 4, 5])
        self.assertEqual(metadata["description"], "video description")
        # Only the first window's user message asked for metadata.
        self.assertIn("YouTube", client.calls[0][1]["content"])
        self.assertNotIn("YouTube", client.calls[1][1]["content"])
        segment.assert_three_way_equality(scenes_index, sentences, text)

    async def test_system_prompt_is_cache_stable_across_windows(self) -> None:
        settings = Settings(segment_sentence_window=2)
        text = " ".join(f"Sentence {i}." for i in range(1, 5))
        sentences = segment.build_sentence_index(text)
        responses = [
            json.dumps({"scenes": [{"idx_sentences": [1]}]}) for _ in range(2)
        ]
        client = FakeClient(responses)

        await segment.segment_script(client, settings, self.config, sentences)

        systems = [call[0]["content"] for call in client.calls]
        self.assertEqual(len(set(systems)), 1)

    async def test_malformed_json_falls_back_without_raising(self) -> None:
        settings = Settings(segment_sentence_window=40)
        sentences = segment.build_sentence_index("Hello there. Goodbye now.")
        client = FakeClient(["not json"])

        scenes_index, metadata = await segment.segment_script(
            client, settings, self.config, sentences,
        )

        flat = [i for s in scenes_index for i in s.idx_sentences]
        self.assertEqual(flat, [1, 2])
        self.assertEqual(metadata, {})


if __name__ == "__main__":
    unittest.main()
