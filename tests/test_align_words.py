import unittest

from app.pipeline.steps.align import AlignmentError, _coverage_report, align_scenes


def _words(pairs: list[tuple[str, float, float]]) -> list[dict]:
    return [{"text": t, "start": s, "end": e} for t, s, e in pairs]


class AlignWordsTests(unittest.TestCase):
    def test_per_word_timing_is_scene_local(self) -> None:
        words = _words([
            ("Hello", 0.0, 0.5),
            ("there", 0.5, 1.0),
            ("friend", 1.0, 1.6),
        ])
        scenes = [{"id": "s1", "type": "cover", "captions": ["Hello there friend"]}]

        [scene] = align_scenes(scenes, words, total_duration=1.6)

        ct = scene["captionTiming"]
        self.assertEqual(len(ct), 1)
        chunk = ct[0]
        self.assertEqual([w["text"] for w in chunk["words"]], ["Hello", "there", "friend"])
        self.assertEqual(chunk["words"][0]["start"], 0.0)
        self.assertEqual(chunk["words"][2]["end"], 1.6)

    def test_scene_local_offset_subtracted_from_words(self) -> None:
        words = _words([
            ("one", 0.0, 0.5),
            ("two", 0.5, 1.0),
            ("three", 1.0, 1.5),
            ("four", 1.5, 2.0),
        ])
        scenes = [
            {"id": "s1", "type": "cover", "captions": ["one two"]},
            {"id": "s2", "type": "cover", "captions": ["three four"]},
        ]

        s1, s2 = align_scenes(scenes, words, total_duration=2.0)

        # s2 starts at ~1.0s; its per-word timings must be relative to that.
        self.assertAlmostEqual(s2["start"], 1.0, places=2)
        self.assertEqual(s2["captionTiming"][0]["words"][0]["text"], "three")
        self.assertEqual(s2["captionTiming"][0]["words"][0]["start"], 0.0)

    def test_multiword_emphasis_is_remarked_per_word(self) -> None:
        words = _words([
            ("retrieval", 0.0, 0.5),
            ("augmented", 0.5, 1.0),
            ("generation", 1.0, 1.6),
        ])
        scenes = [{
            "id": "s1", "type": "cover",
            "captions": ["**retrieval augmented generation**"],
        }]

        [scene] = align_scenes(scenes, words, total_duration=1.6)

        rendered = [w["text"] for w in scene["captionTiming"][0]["words"]]
        self.assertEqual(rendered, ["**retrieval**", "**augmented**", "**generation**"])

    def test_punctuation_only_token_gets_zero_width_slot(self) -> None:
        # "world," has a trailing comma; the transcript word is "world".
        words = _words([
            ("hello", 0.0, 0.5),
            ("world", 0.5, 1.0),
        ])
        scenes = [{"id": "s1", "type": "cover", "captions": ["hello world,"]}]

        [scene] = align_scenes(scenes, words, total_duration=1.0)

        rendered = scene["captionTiming"][0]["words"]
        self.assertEqual([w["text"] for w in rendered], ["hello", "world,"])
        # every word carries numeric timing (no None leaked through)
        for w in rendered:
            self.assertIsInstance(w["start"], float)
            self.assertIsInstance(w["end"], float)

    def test_divergence_raises_with_scene_ids(self) -> None:
        words = _words([("apple", 0.0, 0.5), ("banana", 0.5, 1.0)])
        scenes = [{"id": "mismatch-scene", "type": "cover", "captions": ["totally different words here"]}]

        with self.assertRaises(AlignmentError) as caught:
            align_scenes(scenes, words, total_duration=1.0)

        self.assertEqual(caught.exception.scene_ids, ["mismatch-scene"])


def _spoken(text: str) -> list[dict]:
    return [
        {"text": tok, "start": round(i * 0.4, 2), "end": round(i * 0.4 + 0.4, 2)}
        for i, tok in enumerate(text.split())
    ]


class CoverageReportTests(unittest.TestCase):
    """The chunk that fails to anchor is usually fine; the real defect is text
    the model silently dropped. This is what the retry feedback names."""

    def _report(self, spoken_text: str, captions: list[list[str]]) -> str:
        scenes = [
            {"id": f"s{i}", "type": "cover", "captions": c}
            for i, c in enumerate(captions)
        ]
        return _coverage_report(scenes, _spoken(spoken_text))

    def test_dropped_sentence_is_named(self) -> None:
        report = self._report(
            "Diffusion models are powerful. This approach handles uncertainty "
            "gracefully. Moreover they open new doors.",
            [["Diffusion models", "are powerful."], ["Moreover they open", "new doors."]],
        )

        self.assertIn("MISSING FROM YOUR CAPTIONS", report)
        self.assertIn("This approach handles uncertainty gracefully", report)
        self.assertIn("You dropped this text", report)
        self.assertNotIn("NEVER SPOKEN", report)

    def test_hyphenation_difference_is_not_a_defect(self) -> None:
        # Whisper emits "fine" + "tuning"; the caption writes "fine-tuning".
        # Identical speech — must produce no findings at all.
        report = self._report(
            "we use fine tuning and high quality data",
            [["we use fine-tuning", "and high-quality data"]],
        )
        self.assertEqual(report, "")

    def test_number_spelling_difference_is_not_a_defect(self) -> None:
        report = self._report(
            "the scale runs to 14 and stops",
            [["the scale runs", "to fourteen and stops"]],
        )
        self.assertEqual(report, "")

    def test_emphasis_markers_are_not_a_defect(self) -> None:
        report = self._report(
            "retrieval augmented generation is powerful",
            [["**retrieval augmented generation**", "is powerful"]],
        )
        self.assertEqual(report, "")

    def test_invented_text_is_reported(self) -> None:
        report = self._report(
            "one two three four five six",
            [["one two three", "completely invented phrase", "four five six"]],
        )

        self.assertIn("NEVER SPOKEN", report)
        self.assertIn("completely invented phrase", report)

    def test_faithful_captions_produce_no_report(self) -> None:
        report = self._report(
            "one two three four five six",
            [["one two three"], ["four five six"]],
        )
        self.assertEqual(report, "")

    def test_boundary_drop_blames_the_previous_scene_too(self) -> None:
        # The dropped span must exceed the aligner's chunk lookahead for the
        # next chunk to fail to anchor — the real failure dropped 20 words.
        spoken = _spoken(
            "Diffusion models are powerful. This approach handles uncertainty and "
            "variation in language much more gracefully which is a crucial aspect "
            "of generating high quality text. Moreover they open new doors today."
        )
        scenes = [
            {"id": "a", "type": "cover", "captions": ["Diffusion models", "are powerful."]},
            {"id": "b", "type": "cta", "captions": ["Moreover they open", "new doors today."]},
        ]

        with self.assertRaises(AlignmentError) as caught:
            align_scenes(scenes, spoken, total_duration=12.0)

        # Dropped text sits on the a/b boundary — either section could own it.
        self.assertEqual(caught.exception.scene_ids, ["a", "b"])
        self.assertIn("This approach handles uncertainty", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
