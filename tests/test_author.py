"""Pass 2 (author.py): scenes are authored TOGETHER in one call so the model
can vary frame types. id and captions are always code-applied — the model's own
id/captions/timing are never trusted — scene order matches scenes_index, and the
type-variety hint threads across sequential batches."""
import json
import unittest
from types import SimpleNamespace

from app.config import Settings
from app.pipeline.steps import author
from app.pipeline.steps.segment import SceneIndex
from app.subjects import get_subject_config


class FakeClient:
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


def _scene(n: int, idx: list[int], captions: list[str]) -> SceneIndex:
    return SceneIndex(scene_id=f"scene-{n}", idx_sentences=idx, captions=captions)


def _scenes_payload(entries: list[dict]) -> str:
    return json.dumps({"scenes": entries})


class AuthorBatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.config = get_subject_config("tech", self.settings)
        self.sentences = {1: "First sentence.", 2: "Second sentence.", 3: "Third one."}

    async def test_authors_whole_batch_in_one_call(self) -> None:
        batch = [_scene(1, [1], ["First sentence."]), _scene(2, [2], ["Second sentence."])]
        payload = _scenes_payload([
            {"id": "scene-1", "type": "cover", "eyebrow": "E1", "headline": "H1"},
            {"id": "scene-2", "type": "quote", "quote": "q", "attribution": "a"},
        ])
        client = FakeClient([payload])

        scenes = await author.author_batch(
            client, self.settings, self.config, batch, self.sentences,
            "First sentence. Second sentence.", orientation="vertical", prior_types=[],
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual([s["id"] for s in scenes], ["scene-1", "scene-2"])
        self.assertEqual([s["type"] for s in scenes], ["cover", "quote"])

    async def test_id_and_captions_are_code_applied_not_trusted(self) -> None:
        batch = [_scene(1, [1], ["First sentence."])]
        payload = _scenes_payload([{
            "id": "model-renamed", "type": "cover", "eyebrow": "E", "headline": "H",
            "captions": ["model", "rewrote"], "start": 99, "duration": 5,
        }])
        client = FakeClient([payload])

        [scene] = await author.author_batch(
            client, self.settings, self.config, batch, self.sentences,
            "First sentence.", orientation="vertical", prior_types=[],
        )

        self.assertEqual(scene["id"], "scene-1")
        self.assertEqual(scene["captions"], ["First sentence."])
        self.assertNotIn("start", scene)
        self.assertNotIn("duration", scene)

    async def test_reordered_response_is_realigned_by_id(self) -> None:
        batch = [_scene(1, [1], ["a"]), _scene(2, [2], ["b"])]
        # Model returns them out of order — must be realigned to batch order by id.
        payload = _scenes_payload([
            {"id": "scene-2", "type": "quote", "quote": "q", "attribution": "x"},
            {"id": "scene-1", "type": "cover", "eyebrow": "E", "headline": "H"},
        ])
        client = FakeClient([payload])

        scenes = await author.author_batch(
            client, self.settings, self.config, batch, self.sentences, "a b",
            orientation="vertical", prior_types=[],
        )

        self.assertEqual([s["id"] for s in scenes], ["scene-1", "scene-2"])
        self.assertEqual(scenes[0]["type"], "cover")

    async def test_content_field_errors_trigger_reprompt(self) -> None:
        batch = [_scene(1, [1], ["a"])]
        missing = _scenes_payload([{"id": "scene-1", "type": "stats", "eyebrow": "E"}])
        fixed = _scenes_payload([
            {"id": "scene-1", "type": "stats", "stat": "5", "statLabel": "x"}
        ])
        client = FakeClient([missing, fixed])

        scenes = await author.author_batch(
            client, self.settings, self.config, batch, self.sentences, "a",
            orientation="vertical", prior_types=[],
        )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(scenes[0]["stat"], "5")

    async def test_exhausting_attempts_raises(self) -> None:
        batch = [_scene(1, [1], ["a"])]
        bad = _scenes_payload([{"id": "scene-1", "type": "bogus-type"}])
        client = FakeClient([bad] * self.settings.max_split_attempts)

        with self.assertRaises(author.AuthorError):
            await author.author_batch(
                client, self.settings, self.config, batch, self.sentences, "a",
                orientation="vertical", prior_types=[],
            )
        self.assertEqual(len(client.calls), self.settings.max_split_attempts)

    async def test_prior_types_hint_reaches_the_user_message(self) -> None:
        batch = [_scene(2, [2], ["b"])]
        payload = _scenes_payload([{"id": "scene-2", "type": "quote", "quote": "q", "attribution": "a"}])
        client = FakeClient([payload])

        await author.author_batch(
            client, self.settings, self.config, batch, self.sentences, "b",
            orientation="vertical", prior_types=["cover", "pipeline"],
        )

        user = client.calls[0][1]["content"]
        self.assertIn("ALREADY USED", user)
        self.assertIn("pipeline", user)


class AuthorScenesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = get_subject_config("tech", Settings())

    async def test_single_batch_when_video_fits(self) -> None:
        settings = Settings(author_batch_size=12)
        scenes_index = [_scene(i, [i], [f"cap{i}"]) for i in range(1, 4)]
        sentences = {i: f"Sentence {i}." for i in range(1, 4)}
        payload = _scenes_payload([
            {"id": f"scene-{i}", "type": "cover", "eyebrow": "E", "headline": "H"}
            for i in range(1, 4)
        ])
        client = FakeClient([payload])

        scenes = await author.author_scenes(
            client, settings, self.config, scenes_index, sentences, "full script",
            orientation="vertical",
        )

        self.assertEqual(len(client.calls), 1)
        self.assertEqual([s["id"] for s in scenes], ["scene-1", "scene-2", "scene-3"])

    async def test_multiple_batches_thread_prior_types(self) -> None:
        settings = Settings(author_batch_size=2)
        scenes_index = [_scene(i, [i], [f"cap{i}"]) for i in range(1, 5)]
        sentences = {i: f"Sentence {i}." for i in range(1, 5)}
        batch1 = _scenes_payload([
            {"id": "scene-1", "type": "cover", "eyebrow": "E", "headline": "H"},
            {"id": "scene-2", "type": "pipeline", "title": "T", "nodes": [{"label": "n"}]},
        ])
        batch2 = _scenes_payload([
            {"id": "scene-3", "type": "quote", "quote": "q", "attribution": "a"},
            {"id": "scene-4", "type": "cta", "subheadline": "s"},
        ])
        client = FakeClient([batch1, batch2])

        scenes = await author.author_scenes(
            client, settings, self.config, scenes_index, sentences, "full script",
            orientation="vertical",
        )

        self.assertEqual(len(client.calls), 2)
        self.assertEqual([s["id"] for s in scenes], [f"scene-{i}" for i in range(1, 5)])
        # Second batch's user message must carry the first batch's chosen types.
        second_user = client.calls[1][1]["content"]
        self.assertIn("cover", second_user)
        self.assertIn("pipeline", second_user)

    async def test_system_prompt_is_cache_stable_across_batches(self) -> None:
        settings = Settings(author_batch_size=1)
        scenes_index = [_scene(i, [i], ["cap"]) for i in range(1, 3)]
        sentences = {1: "One.", 2: "Two."}
        responses = [
            _scenes_payload([{"id": f"scene-{i}", "type": "cover", "eyebrow": "E", "headline": "H"}])
            for i in range(1, 3)
        ]
        client = FakeClient(responses)

        await author.author_scenes(
            client, settings, self.config, scenes_index, sentences, "One. Two.",
        )

        systems = [call[0]["content"] for call in client.calls]
        self.assertEqual(len(set(systems)), 1)


if __name__ == "__main__":
    unittest.main()
