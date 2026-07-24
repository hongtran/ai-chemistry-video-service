import unittest

from app.pipeline.steps.sections import split_for_tts, split_sentences, window_sentences


def _sentences(n: int, words_each: int = 10, tag: str = "w") -> str:
    return " ".join(" ".join([f"{tag}{i}"] * words_each) + "." for i in range(n))


class SplitSentencesTests(unittest.TestCase):
    def test_splits_on_terminal_punctuation(self) -> None:
        self.assertEqual(
            split_sentences("One sentence. Two sentence! Three sentence? Four sentence."),
            ["One sentence.", "Two sentence!", "Three sentence?", "Four sentence."],
        )

    def test_empty_text_yields_no_sentences(self) -> None:
        self.assertEqual(split_sentences(""), [])

    def test_single_word_segments_merge_pairwise(self) -> None:
        # Each raw segment is 1 word — under the 2-word minimum, so they pair
        # up front-to-back rather than standing alone.
        self.assertEqual(
            split_sentences("One. Two! Three? Four."),
            ["One. Two!", "Three? Four."],
        )

    def test_short_lead_in_merges_forward_into_the_next_sentence(self) -> None:
        self.assertEqual(split_sentences("Hi! how are you."), ["Hi! how are you."])

    def test_numbering_marker_merges_forward_into_the_next_sentence(self) -> None:
        self.assertEqual(split_sentences("1. mẫu trắng."), ["1. mẫu trắng."])

    def test_short_trailing_fragment_merges_backward(self) -> None:
        # Nothing follows "Yes!" to merge forward into, so it folds into the
        # previous sentence instead.
        self.assertEqual(split_sentences("This is great. Yes!"), ["This is great. Yes!"])

    def test_merging_preserves_every_word(self) -> None:
        text = "Hi! how are you. This is fine."
        self.assertEqual(" ".join(split_sentences(text)).split(), text.split())


class SplitForTTSTests(unittest.TestCase):
    def test_short_text_is_one_chunk(self) -> None:
        self.assertEqual(split_for_tts("Hello world.", 4000), ["Hello world."])

    def test_packs_sentences_up_to_the_cap(self) -> None:
        text = "aaaa. bbbb. cccc."
        chunks = split_for_tts(text, 11)  # fits two 5-char sentences + space
        self.assertTrue(all(len(c) <= 11 for c in chunks), chunks)
        self.assertEqual(" ".join(chunks), text)

    def test_never_exceeds_cap_and_preserves_all_words(self) -> None:
        text = _sentences(40, words_each=8)
        chunks = split_for_tts(text, 200)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 200)
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_single_oversized_sentence_is_hard_wrapped(self) -> None:
        # One sentence far over the cap, no interior punctuation to split on.
        text = " ".join(["word"] * 100) + "."
        chunks = split_for_tts(text, 50)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 50)
        self.assertEqual(" ".join(chunks).split(), text.split())

    def test_empty_text_yields_no_chunks(self) -> None:
        self.assertEqual(split_for_tts("", 4000), [])


class WindowSentencesTests(unittest.TestCase):
    def _index(self, n: int) -> list[dict]:
        return [{"i": i, "text": f"sentence {i}."} for i in range(1, n + 1)]

    def test_short_index_is_a_single_window(self) -> None:
        sentences = self._index(10)
        windows = window_sentences(sentences, 40)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0], sentences)

    def test_long_index_splits_preserving_global_i(self) -> None:
        sentences = self._index(100)
        windows = window_sentences(sentences, 40)

        self.assertEqual(len(windows), 3)
        self.assertEqual(len(windows[0]), 40)
        self.assertEqual(len(windows[1]), 40)
        self.assertEqual(len(windows[2]), 20)
        # Global numbering preserved across windows, contiguous and in order.
        flat = [s["i"] for w in windows for s in w]
        self.assertEqual(flat, list(range(1, 101)))

    def test_zero_or_negative_window_is_a_single_window(self) -> None:
        sentences = self._index(50)
        self.assertEqual(window_sentences(sentences, 0), [sentences])


if __name__ == "__main__":
    unittest.main()
