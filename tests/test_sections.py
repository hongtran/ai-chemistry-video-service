import unittest

from app.config import Settings
from app.pipeline.steps.scene_split import build_sections, sections_for_scene_ids
from app.pipeline.steps.sections import (
    build_arc_rule,
    section_index_from_scene_id,
    split_for_tts,
    split_into_sections,
    split_sentences,
)
from app.subjects import get_subject_config


def _sentences(n: int, words_each: int = 10, tag: str = "w") -> str:
    return " ".join(" ".join([f"{tag}{i}"] * words_each) + "." for i in range(n))


class SplitSentencesTests(unittest.TestCase):
    def test_splits_on_terminal_punctuation(self) -> None:
        self.assertEqual(
            split_sentences("One. Two! Three? Four."),
            ["One.", "Two!", "Three?", "Four."],
        )

    def test_empty_text_yields_no_sentences(self) -> None:
        self.assertEqual(split_sentences(""), [])


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


class SplitIntoSectionsTests(unittest.TestCase):
    def test_short_narration_is_a_single_section(self) -> None:
        text = _sentences(3, words_each=10)  # ~30 words
        self.assertEqual(split_into_sections(text, 200), [text])

    def test_long_narration_splits_on_word_budget(self) -> None:
        text = _sentences(60, words_each=10)  # ~600 words
        sections = split_into_sections(text, 200)

        self.assertGreater(len(sections), 1)
        # No content lost or reordered.
        self.assertEqual(" ".join(sections).split(), text.split())
        for s in sections[:-1]:
            self.assertLessEqual(len(s.split()), 200 + 10)

    def test_runt_tail_is_merged_into_previous_section(self) -> None:
        # 21 sentences x 10 words = 210 words: a 200-word budget would leave a
        # 10-word tail, which must be absorbed rather than left as its own
        # section (the closing section has to carry the CTA).
        text = _sentences(21, words_each=10)
        sections = split_into_sections(text, 200)

        self.assertEqual(len(sections), 1)
        self.assertEqual(" ".join(sections).split(), text.split())

    def test_substantial_tail_stays_its_own_section(self) -> None:
        text = _sentences(30, words_each=10)  # 300 words -> 200 + 100 tail
        sections = split_into_sections(text, 200)

        self.assertEqual(len(sections), 2)
        self.assertGreaterEqual(len(sections[1].split()), 100)


class SectionIndexTests(unittest.TestCase):
    def test_prefixed_ids_report_their_section(self) -> None:
        self.assertEqual(section_index_from_scene_id("s3-pipeline"), 3)
        self.assertEqual(section_index_from_scene_id("s0-hook"), 0)
        self.assertEqual(section_index_from_scene_id("s12-outro"), 12)

    def test_unprefixed_ids_belong_to_section_zero(self) -> None:
        self.assertEqual(section_index_from_scene_id("hook"), 0)
        self.assertEqual(section_index_from_scene_id("scene-1"), 0)


class BuildArcRuleTests(unittest.TestCase):
    def test_single_section_keeps_the_short_arc(self) -> None:
        rule = build_arc_rule(0, 1)
        self.assertIn("6-9 scenes", rule)
        self.assertNotIn("LONG-FORM", rule)

    def test_long_form_positions_get_distinct_guidance(self) -> None:
        opening, middle, closing = (
            build_arc_rule(0, 3), build_arc_rule(1, 3), build_arc_rule(2, 3)
        )
        for rule in (opening, middle, closing):
            self.assertIn("LONG-FORM", rule)
            self.assertNotIn("6-9 scenes", rule)

        self.assertIn("OPENING", opening)
        self.assertIn("Do NOT close or add a CTA", opening)
        self.assertIn("MIDDLE", middle)
        self.assertIn("CLOSING", closing)


class BuildSectionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.tech = get_subject_config("tech", self.settings)

    def test_vertical_is_always_one_unprefixed_section(self) -> None:
        text = _sentences(60, words_each=10)  # long text, but short-form canvas
        sections = build_sections(self.tech, "vertical", text)

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].id_prefix, "")
        self.assertEqual(sections[0].total, 1)

    def test_horizontal_sections_and_prefixes(self) -> None:
        text = _sentences(60, words_each=10)
        sections = build_sections(self.tech, "horizontal", text)

        self.assertGreater(len(sections), 1)
        for i, section in enumerate(sections):
            self.assertEqual(section.index, i)
            self.assertEqual(section.total, len(sections))
            self.assertEqual(section.id_prefix, f"s{i}-")
        self.assertEqual(
            " ".join(s.text for s in sections).split(), text.split()
        )

    def test_short_horizontal_narration_still_yields_one_section(self) -> None:
        sections = build_sections(self.tech, "horizontal", _sentences(2, words_each=5))
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].id_prefix, "")


class SectionsForSceneIdsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tech = get_subject_config("tech", Settings())
        self.sections = build_sections(
            self.tech, "horizontal", _sentences(60, words_each=10)
        )

    def test_targets_only_the_owning_sections(self) -> None:
        targets = sections_for_scene_ids(self.sections, ["s1-pipeline"])
        self.assertEqual([s.index for s in targets], [1])

    def test_multiple_ids_collapse_to_their_distinct_sections(self) -> None:
        targets = sections_for_scene_ids(
            self.sections, ["s0-hook", "s1-flow", "s1-code"]
        )
        self.assertEqual([s.index for s in targets], [0, 1])

    def test_unknown_ids_fall_back_to_the_first_section(self) -> None:
        targets = sections_for_scene_ids(self.sections, [])
        self.assertEqual([s.index for s in targets], [0])


if __name__ == "__main__":
    unittest.main()
