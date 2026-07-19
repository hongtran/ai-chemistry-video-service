import json
import unittest
from types import SimpleNamespace

from app.config import Settings
from app.pipeline.steps import scene_split
from app.subjects import get_subject_config

_SCENES = [
    {
        "id": "hook",
        "type": "cover",
        "eyebrow": "CHEMISTRY",
        "headline": "The pH Scale",
        "captions": ["Have you ever", "wondered why."],
    },
    {
        "id": "closing",
        "type": "cta",
        "eyebrow": "TAKEAWAY",
        "headline": "Chemistry Is Everywhere",
        "subheadline": "Every sip, every bite.",
        "captions": ["So next time", "you will know."],
    },
]


class FakeClient:
    """Minimal stand-in for AsyncOpenAI: replays queued JSON responses and
    records the messages each call received."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, response_format, temperature=None):
        self.calls.append([dict(m) for m in messages])
        self.temperature = temperature
        body = self._responses.pop(0)
        message = SimpleNamespace(content=body)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class SceneSplitMetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.config = get_subject_config("chemistry", self.settings)

    async def _run(self, responses: list[str]):
        client = FakeClient(responses)
        scenes, metadata = await scene_split.generate_scenes(
            client, self.settings, self.config, "the script", "the transcript"
        )
        return client, scenes, metadata

    async def test_metadata_is_returned_alongside_scenes(self) -> None:
        payload = json.dumps({
            "config": {
                "description": "How the pH scale works.",
                "hashtags": ["#Chemistry", "PH"],
                "tags": ["ph scale", "acids and bases"],
            },
            "scenes": _SCENES,
        })

        client, scenes, metadata = await self._run([payload])

        self.assertEqual([s["id"] for s in scenes], ["hook", "closing"])
        self.assertEqual(metadata["description"], "How the pH scale works.")
        self.assertEqual(metadata["hashtags"], ["chemistry", "ph"])
        self.assertEqual(metadata["tags"], ["ph scale", "acids and bases"])

    async def test_metadata_request_lives_in_user_message_not_system(self) -> None:
        payload = json.dumps({"config": {"description": "d"}, "scenes": _SCENES})
        client, _, _ = await self._run([payload])

        system, user = client.calls[0][0], client.calls[0][1]
        self.assertEqual(system["role"], "system")
        self.assertEqual(user["role"], "user")
        # The cache-stable system prefix must not carry the per-request block.
        self.assertNotIn("hashtags", system["content"])
        self.assertIn("hashtags", user["content"])

    async def test_missing_config_does_not_fail_the_job(self) -> None:
        payload = json.dumps({"scenes": _SCENES})
        _, scenes, metadata = await self._run([payload])

        self.assertEqual(len(scenes), 2)
        self.assertEqual(metadata, {})

    async def test_garbage_config_does_not_fail_the_job(self) -> None:
        payload = json.dumps({"config": "not-an-object", "scenes": _SCENES})
        _, scenes, metadata = await self._run([payload])

        self.assertEqual(len(scenes), 2)
        self.assertEqual(metadata, {})

    async def test_metadata_survives_a_corrective_reprompt(self) -> None:
        bad = json.dumps({"scenes": [{"id": "x", "type": "not-a-real-type"}]})
        good = json.dumps({"config": {"description": "Recovered."}, "scenes": _SCENES})

        client, scenes, metadata = await self._run([bad, good])

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(scenes), 2)
        self.assertEqual(metadata["description"], "Recovered.")


if __name__ == "__main__":
    unittest.main()
