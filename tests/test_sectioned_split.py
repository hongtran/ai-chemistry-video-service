import json
import unittest
from types import SimpleNamespace

from app.config import Settings
from app.pipeline.steps import scene_split
from app.subjects import get_subject_config


def _scenes(*ids: str) -> list[dict]:
    return [
        {
            "id": i,
            "type": "cover",
            "eyebrow": "EB",
            "headline": "H",
            "captions": ["a b", "c d"],
        }
        for i in ids
    ]


def _payload(ids: list[str], config: dict | None = None) -> str:
    body: dict = {"scenes": _scenes(*ids)}
    if config is not None:
        body["config"] = config
    return json.dumps(body)


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, response_format, temperature=None):
        self.calls.append([dict(m) for m in messages])
        self.temperature = temperature
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._responses.pop(0)))]
        )


class SectionedSplitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.config = get_subject_config("tech", self.settings)

    def _sections(self, count: int) -> list[scene_split.SectionState]:
        return [
            scene_split.SectionState(
                index=i, total=count, text=f"part {i} text.", id_prefix=f"s{i}-"
            )
            for i in range(count)
        ]

    async def test_scene_ids_get_their_section_prefix(self) -> None:
        section = self._sections(3)[1]
        client = FakeClient([_payload(["flow", "code"])])

        scenes, _ = await scene_split.split_section(
            client, self.settings, self.config, section, orientation="horizontal"
        )

        self.assertEqual([s["id"] for s in scenes], ["s1-flow", "s1-code"])

    async def test_split_is_sent_at_the_configured_temperature(self) -> None:
        # Left unset, OpenAI defaults to 1.0 and the model paraphrases and
        # drops clauses, which the verbatim rule cannot survive.
        section = self._sections(1)[0]
        client = FakeClient([_payload(["hook"])])

        await scene_split.split_section(
            client, self.settings, self.config, section, orientation="vertical"
        )

        self.assertEqual(client.temperature, self.settings.llm_temperature)
        self.assertEqual(self.settings.llm_temperature, 0.5)

    async def test_model_invented_prefix_does_not_compound(self) -> None:
        section = self._sections(3)[2]
        client = FakeClient([_payload(["s9-hook"])])

        scenes, _ = await scene_split.split_section(
            client, self.settings, self.config, section, orientation="horizontal"
        )

        self.assertEqual([s["id"] for s in scenes], ["s2-hook"])

    async def test_only_section_zero_is_asked_for_metadata(self) -> None:
        sections = self._sections(2)
        meta = {"description": "d", "hashtags": ["ai"], "tags": ["ai agents"]}

        c0 = FakeClient([_payload(["hook"], config=meta)])
        _, m0 = await scene_split.split_section(
            c0, self.settings, self.config, sections[0], orientation="horizontal"
        )
        c1 = FakeClient([_payload(["outro"], config=meta)])
        _, m1 = await scene_split.split_section(
            c1, self.settings, self.config, sections[1], orientation="horizontal"
        )

        self.assertEqual(m0["description"], "d")
        self.assertIn("hashtags", c0.calls[0][1]["content"])
        # Section 1 must neither be asked for, nor return, video-level metadata.
        self.assertEqual(m1, {})
        self.assertNotIn("hashtags", c1.calls[0][1]["content"])

    async def test_long_form_user_message_carries_part_framing_and_arc(self) -> None:
        sections = self._sections(3)
        client = FakeClient([_payload(["mid"])])

        await scene_split.split_section(
            client, self.settings, self.config, sections[1], orientation="horizontal"
        )

        user = client.calls[0][1]["content"]
        self.assertIn("PART 2 of 3", user)
        self.assertIn("MIDDLE part", user)
        self.assertIn("LONG-FORM", user)
        self.assertIn("horizontal 16:9", user)
        self.assertIn("part 1 text.", user)

    async def test_system_prompt_is_identical_across_sections(self) -> None:
        sections = self._sections(3)
        systems = []
        for section in sections:
            client = FakeClient([_payload(["x"])])
            await scene_split.split_section(
                client, self.settings, self.config, section, orientation="horizontal"
            )
            systems.append(client.calls[0][0]["content"])

        self.assertEqual(len(set(systems)), 1, "system prefix must stay cache-stable")

    async def test_feedback_continues_the_sections_conversation(self) -> None:
        section = self._sections(2)[0]

        first = FakeClient([_payload(["hook"], config={"description": "d"})])
        await scene_split.split_section(
            first, self.settings, self.config, section, orientation="horizontal"
        )
        turns_after_first = len(section.messages)

        second = FakeClient([_payload(["hook2"], config={"description": "d2"})])
        scenes, _ = await scene_split.split_section(
            second, self.settings, self.config, section, feedback="FIX: captions drifted"
        )

        # The retry reuses the same conversation rather than starting fresh.
        self.assertGreater(len(section.messages), turns_after_first)
        sent = second.calls[0]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(sent[-1]["content"], "FIX: captions drifted")
        self.assertEqual([s["id"] for s in scenes], ["s0-hook2"])
        self.assertEqual(section.scenes, scenes)

    async def test_exhausting_attempts_raises_with_section_context(self) -> None:
        section = self._sections(4)[2]
        bad = json.dumps({"scenes": [{"id": "x", "type": "bogus-type"}]})
        client = FakeClient([bad] * self.settings.max_split_attempts)

        with self.assertRaises(scene_split.SceneSplitError) as caught:
            await scene_split.split_section(
                client, self.settings, self.config, section, orientation="horizontal"
            )

        self.assertIn("section 3/4", str(caught.exception))
        self.assertEqual(len(client.calls), self.settings.max_split_attempts)


if __name__ == "__main__":
    unittest.main()
