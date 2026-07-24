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


class DeriveCaptionsLongFormTests(unittest.TestCase):
    def test_long_form_chunks_are_three_to_seven_words(self) -> None:
        sentence = "Thunder clouds gather, fast tonight over the hills."
        chunks = segment.derive_captions([sentence], orientation="horizontal")
        for c in chunks:
            self.assertGreaterEqual(len(c.split()), 3)
            self.assertLessEqual(len(c.split()), 7)
        self.assertEqual(" ".join(chunks).split(), sentence.split())

    def test_punctuation_preferred_break_lands_after_comma(self) -> None:
        sentence = "Thunder clouds gather, fast tonight over the hills."
        chunks = segment.derive_captions([sentence], orientation="horizontal")
        self.assertEqual(chunks[0], "Thunder clouds gather,")

    def test_trailing_remainder_is_merged_back_into_bounds(self) -> None:
        # Under the OLD algorithm this would split into a 3-word chunk and an
        # under-minimum 2-word trailing chunk; the fix must merge them.
        sentence = "Thunder clouds gather, fast tonight"
        chunks = segment.derive_captions([sentence], orientation="horizontal")
        for c in chunks:
            self.assertGreaterEqual(len(c.split()), 3)
            self.assertLessEqual(len(c.split()), 7)
        self.assertEqual(" ".join(chunks).split(), sentence.split())

    def test_unavoidable_short_remainder_does_not_crash(self) -> None:
        # Too few total words to ever reach min_words=3 for long-form.
        chunks = segment.derive_captions(["Two words"], orientation="horizontal")
        self.assertEqual(chunks, ["Two words"])

    def test_default_orientation_still_two_to_five_words(self) -> None:
        sentence = "Thunder clouds gather, fast tonight over the hills."
        chunks = segment.derive_captions([sentence])  # no orientation arg
        for c in chunks:
            self.assertLessEqual(len(c.split()), 5)


class DeriveCaptionsSentenceBoundaryTests(unittest.TestCase):
    """A caption must never straddle a sentence boundary — a short sentence
    gets its own (possibly under-minimum) caption instead of borrowing words
    from its neighbor. Covers both call shapes: genuinely separate sentence
    strings, and one already-joined scene string (what
    derive_captions_semantic's fallback actually passes in production).

    Uses >=2-word short sentences ("No way.") rather than 1-word ones: a
    1-word sentence like "Hi." now gets folded into the NEXT sentence by
    split_sentences itself (see test_sections.py) before derive_captions ever
    sees a boundary there — by design, so it's no longer a useful example of
    this specific invariant."""

    def test_short_sentence_does_not_borrow_from_the_next_sentence(self) -> None:
        chunks = segment.derive_captions(
            ["No way.", "This is a much longer follow-up sentence here."]
        )
        self.assertEqual(chunks[0], "No way.")

    def test_short_sentence_does_not_borrow_when_pre_joined_into_one_string(self) -> None:
        # This is the shape derive_captions_semantic's fallback actually calls
        # derive_captions with: one already-flattened multi-sentence string.
        text = "No way. This is a much longer follow-up sentence here."
        chunks = segment.derive_captions([text])
        self.assertEqual(chunks[0], "No way.")
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_long_form_short_sentence_does_not_borrow_either(self) -> None:
        text = "Not at all. That answer covers far more than three words on its own."
        chunks = segment.derive_captions([text], orientation="horizontal")
        self.assertEqual(chunks[0], "Not at all.")
        self.assertEqual(" ".join(chunks).split(), text.split())


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
        # Each sentence is >=2 words so split_sentences' short-segment merge
        # (see test_sections.py) doesn't fold any of these together.
        sentences = segment.build_sentence_index("One thing. Two things! Three things?")
        self.assertEqual([s["i"] for s in sentences], [1, 2, 3])
        self.assertEqual(sentences[0]["text"], "One thing.")


class SegmentScriptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = get_subject_config("tech", Settings())

    async def test_whole_script_single_window(self) -> None:
        settings = Settings(segment_sentence_window=40, semantic_captions_enabled=False)
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
        settings = Settings(segment_sentence_window=3, semantic_captions_enabled=False)
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
        settings = Settings(segment_sentence_window=2, semantic_captions_enabled=False)
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
        settings = Settings(segment_sentence_window=40, semantic_captions_enabled=False)
        sentences = segment.build_sentence_index("Hello there. Goodbye now.")
        client = FakeClient(["not json"])

        scenes_index, metadata = await segment.segment_script(
            client, settings, self.config, sentences,
        )

        flat = [i for s in scenes_index for i in s.idx_sentences]
        self.assertEqual(flat, [1, 2])
        self.assertEqual(metadata, {})


class DeriveCaptionsSemanticTests(unittest.IsolatedAsyncioTestCase):
    """The LLM chunks each scene's paragraph and returns caption strings; code
    validates each scene reproduces its own words, else falls back to greedy."""

    def _assert_scene_valid(self, captions: list[str], scene_text: str) -> None:
        # word integrity: captions joined reproduce the scene's exact words
        self.assertEqual(" ".join(captions).split(), scene_text.split())
        for c in captions:
            self.assertLessEqual(len(c.split()), 5)
            self.assertLessEqual(len(c), 55)

    async def test_semantic_break_moves_leading_conjunction(self) -> None:
        settings = Settings()
        text = "Thực nghiệm hóa học Cùng các chuyên gia"
        # Model keeps "Cùng" at the START of the next caption, not dangling.
        payload = json.dumps({
            "scenes": [{"captions": ["Thực nghiệm hóa học", "Cùng các chuyên gia"]}]
        })
        client = FakeClient([payload])

        result = await segment.derive_captions_semantic(client, settings, [[text]])

        self.assertEqual(result[0], ["Thực nghiệm hóa học", "Cùng các chuyên gia"])
        self._assert_scene_valid(result[0], text)

    async def test_one_call_for_all_scenes(self) -> None:
        settings = Settings()
        scenes = [["alpha beta gamma"], ["delta epsilon"], ["zeta eta theta"]]
        payload = json.dumps({
            "scenes": [
                {"captions": ["alpha beta gamma"]},
                {"captions": ["delta epsilon"]},
                {"captions": ["zeta eta theta"]},
            ]
        })
        client = FakeClient([payload])

        result = await segment.derive_captions_semantic(client, settings, scenes)

        self.assertEqual(len(client.calls), 1)  # single call for all three scenes
        self.assertEqual(result, [["alpha beta gamma"], ["delta epsilon"], ["zeta eta theta"]])

    async def test_reworded_scene_falls_back_to_greedy(self) -> None:
        settings = Settings()
        text = "The cat sat on the mat"
        # Model dropped "the" — validation must reject and fall back.
        payload = json.dumps({"scenes": [{"captions": ["The cat sat", "on mat"]}]})
        client = FakeClient([payload])

        result = await segment.derive_captions_semantic(client, settings, [[text]])

        self.assertEqual(result[0], segment.derive_captions([text]))

    async def test_oversize_caption_is_cap_repaired(self) -> None:
        settings = Settings()
        text = " ".join(f"word{i}" for i in range(1, 13))  # 12 words
        # Model returns all 12 words as one caption (violates the ≤5-word cap).
        payload = json.dumps({"scenes": [{"captions": [text]}]})
        client = FakeClient([payload])

        result = await segment.derive_captions_semantic(client, settings, [[text]])

        self.assertGreater(len(result[0]), 1)  # re-split, not one 12-word caption
        self._assert_scene_valid(result[0], text)

    async def test_bad_json_falls_back_to_greedy(self) -> None:
        settings = Settings()
        text = "This is a fairly long test sentence with many words"
        client = FakeClient(["not json"])

        result = await segment.derive_captions_semantic(client, settings, [[text]])

        self.assertEqual(result[0], segment.derive_captions([text]))

    async def test_missing_scene_entry_falls_back_per_scene(self) -> None:
        settings = Settings()
        t1, t2 = "alpha beta gamma", "delta epsilon zeta"
        # Only one scene returned for two inputs → second scene uses greedy.
        payload = json.dumps({"scenes": [{"captions": ["alpha beta gamma"]}]})
        client = FakeClient([payload])

        result = await segment.derive_captions_semantic(client, settings, [[t1], [t2]])

        self.assertEqual(result[0], ["alpha beta gamma"])
        self.assertEqual(result[1], segment.derive_captions([t2]))

    async def test_stub_mode_makes_no_llm_call(self) -> None:
        settings = Settings(use_stub_pipeline=True)
        text = "one two three four five six"
        client = FakeClient([])  # empty queue: a call would IndexError

        result = await segment.derive_captions_semantic(client, settings, [[text]])

        self.assertEqual(client.calls, [])
        self.assertEqual(result[0], segment.derive_captions([text]))

    async def test_disabled_toggle_makes_no_llm_call(self) -> None:
        settings = Settings(semantic_captions_enabled=False)
        text = "one two three four five six"
        client = FakeClient([])

        result = await segment.derive_captions_semantic(client, settings, [[text]])

        self.assertEqual(client.calls, [])
        self.assertEqual(result[0], segment.derive_captions([text]))

    async def test_disabled_toggle_uses_long_form_bounds_when_horizontal(self) -> None:
        settings = Settings(semantic_captions_enabled=False)
        text = "Thunder clouds gather, fast tonight over the windy hills tonight"
        client = FakeClient([])

        result = await segment.derive_captions_semantic(
            client, settings, [[text]], orientation="horizontal",
        )

        self.assertEqual(client.calls, [])
        self.assertEqual(result[0], segment.derive_captions([text], orientation="horizontal"))
        for c in result[0]:
            self.assertLessEqual(len(c.split()), 7)


class SegmentScriptSemanticTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = get_subject_config("tech", Settings())

    async def test_single_window_makes_segment_then_caption_call(self) -> None:
        settings = Settings(segment_sentence_window=40)  # semantic on by default
        script = "One thing happens. Then another thing happens now."
        sentences = segment.build_sentence_index(script)
        segment_payload = json.dumps({
            "scenes": [{"idx_sentences": [1]}, {"idx_sentences": [2]}],
            "config": {"description": "d"},
        })
        # Both scenes chunked in ONE caption call.
        caption_payload = json.dumps({
            "scenes": [
                {"captions": ["One thing happens."]},
                {"captions": ["Then another", "thing happens now."]},
            ]
        })
        client = FakeClient([segment_payload, caption_payload])

        scenes_index, _ = await segment.segment_script(
            client, settings, self.config, sentences, orientation="vertical",
        )

        self.assertEqual(len(client.calls), 2)  # segment, then one caption call
        # Second call is the caption call: scene paragraphs in its user message.
        self.assertIn("SCENES:", client.calls[1][1]["content"])
        self.assertIn("One thing happens.", client.calls[1][1]["content"])
        segment.assert_three_way_equality(scenes_index, sentences, script)

    async def test_caption_failure_still_preserves_equality(self) -> None:
        settings = Settings(segment_sentence_window=40)
        script = "One thing happens. Then another thing happens now."
        sentences = segment.build_sentence_index(script)
        segment_payload = json.dumps({"scenes": [{"idx_sentences": [1]}]})
        client = FakeClient([segment_payload, "not json"])  # caption call fails

        scenes_index, _ = await segment.segment_script(
            client, settings, self.config, sentences,
        )

        # Greedy fallback still reproduces the script word-for-word.
        segment.assert_three_way_equality(scenes_index, sentences, script)

    async def test_horizontal_orientation_reaches_greedy_captions(self) -> None:
        settings = Settings(segment_sentence_window=40, semantic_captions_enabled=False)
        script = "Thunder clouds gather, fast tonight over the windy hills tonight."
        sentences = segment.build_sentence_index(script)
        segment_payload = json.dumps({"scenes": [{"idx_sentences": [1]}]})
        client = FakeClient([segment_payload])

        scenes_index, _ = await segment.segment_script(
            client, settings, self.config, sentences, orientation="horizontal",
        )

        for caption in scenes_index[0].captions:
            self.assertLessEqual(len(caption.split()), 7)
        segment.assert_three_way_equality(scenes_index, sentences, script)


if __name__ == "__main__":
    unittest.main()
