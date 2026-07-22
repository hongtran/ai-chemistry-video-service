"""Best-effort alignment paths that have no audio signal to work with at all
(empty transcript, scene with no captions) — align_scenes must still return
usable numeric timing and never raise."""
import unittest

from app.pipeline.steps.align import align_scenes


def _words(pairs: list[tuple[str, float, float]]) -> list[dict]:
    return [{"text": t, "start": s, "end": e} for t, s, e in pairs]


class NoUsableWordsTests(unittest.TestCase):
    def test_empty_word_list_falls_back_to_proportional_split(self) -> None:
        scenes = [
            {"id": "a", "type": "cover", "captions": ["one two"]},
            {"id": "b", "type": "cta", "captions": ["three four five six"]},
        ]

        a, b = align_scenes(scenes, [], total_duration=10.0)  # must not raise

        self.assertEqual(a["start"], 0.0)
        self.assertAlmostEqual(a["start"] + a["duration"], b["start"], places=2)
        self.assertAlmostEqual(b["start"] + b["duration"], 10.0, places=2)
        # Weighted by caption word count: b has more words, so a longer slot.
        self.assertGreater(b["duration"], a["duration"])
        for scene in (a, b):
            for chunk in scene["captionTiming"]:
                self.assertIsInstance(chunk["start"], float)
                self.assertIsInstance(chunk["end"], float)

    def test_words_with_no_alignable_subwords_falls_back(self) -> None:
        # Punctuation-only "words" expand to nothing in _expand_token.
        words = _words([("...", 0.0, 0.1), ("--", 0.1, 0.2)])
        scenes = [{"id": "a", "type": "cover", "captions": ["hello world"]}]

        [scene] = align_scenes(scenes, words, total_duration=5.0)

        self.assertEqual(scene["start"], 0.0)
        self.assertEqual(scene["duration"], 5.0)


class NoCaptionsTests(unittest.TestCase):
    def test_scene_with_no_captions_gets_a_proportional_slot(self) -> None:
        words = _words([("hello", 0.0, 0.5), ("world", 0.5, 1.0)])
        scenes = [
            {"id": "a", "type": "cover", "captions": ["hello world"]},
            {"id": "b", "type": "cta", "captions": []},
        ]

        a, b = align_scenes(scenes, words, total_duration=2.0)  # must not raise

        self.assertEqual(b["captionTiming"], [])
        self.assertIsInstance(b["start"], float)
        self.assertIsInstance(b["duration"], float)


class InterpolationTests(unittest.TestCase):
    def test_unanchored_chunk_gets_interpolated_numeric_timing(self) -> None:
        # Middle chunk doesn't match the audio at all; the aligner must not
        # advance its cursor past the unmatched audio, and must still hand
        # back numeric (not None) timing for every word.
        words = _words([
            ("one", 0.0, 0.5), ("two", 0.5, 1.0),
            ("five", 2.0, 2.5), ("six", 2.5, 3.0),
        ])
        scenes = [{
            "id": "s1", "type": "cover",
            "captions": ["one two", "three four", "five six"],
        }]

        [scene] = align_scenes(scenes, words, total_duration=3.0)

        chunks = scene["captionTiming"]
        self.assertEqual(len(chunks), 3)
        for chunk in chunks:
            self.assertIsInstance(chunk["start"], float)
            self.assertIsInstance(chunk["end"], float)
            for w in chunk["words"]:
                self.assertIsInstance(w["start"], float)
                self.assertIsInstance(w["end"], float)
        # Timeline stays monotonic despite the gap in the middle.
        starts = [c["start"] for c in chunks]
        self.assertEqual(starts, sorted(starts))

    def test_next_chunk_still_anchors_after_a_dropped_chunk(self) -> None:
        # The unmatched middle chunk must not consume the aligner's position,
        # so "five six" (which IS in the audio) still anchors correctly.
        words = _words([
            ("one", 0.0, 0.5), ("two", 0.5, 1.0),
            ("five", 2.0, 2.5), ("six", 2.5, 3.0),
        ])
        scenes = [{
            "id": "s1", "type": "cover",
            "captions": ["one two", "totally unrelated words", "five six"],
        }]

        [scene] = align_scenes(scenes, words, total_duration=3.0)

        last_chunk = scene["captionTiming"][-1]
        self.assertEqual(last_chunk["text"], "five six")
        # "five" really starts at 2.0s — the anchor must not have been lost.
        self.assertAlmostEqual(last_chunk["words"][0]["start"], 2.0, places=1)


if __name__ == "__main__":
    unittest.main()
