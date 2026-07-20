import json
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.router import request_video
from app.api.schemas import CreateVideoRequest
from app.config import Settings
from app.domain.models import JobStatus
from app.llm.client import GuardResult
from app.pipeline.steps import scene_split
from app.pipeline.steps.narration import _language_clause
from app.pipeline.steps.scene_split import _language_block, build_user_message
from app.storage.jobs import InMemoryJobRepository
from app.subjects import get_subject_config


class RecordingGuard:
    def __init__(self, result: GuardResult | None = None) -> None:
        self.result = result or GuardResult(is_valid=True, reason="ok")
        self.calls: list[tuple[str, str]] = []

    async def check(self, query: str, subject: str) -> GuardResult:
        self.calls.append((query, subject))
        return self.result


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


def _request(settings, jobs, queue, guard) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings, jobs=jobs, artifacts=None, queue=queue, guard=guard
            )
        )
    )


class VoiceForLanguageTests(unittest.TestCase):
    def test_mapped_language_uses_its_voice(self) -> None:
        settings = Settings(tts_voice="alloy", tts_voice_by_language={"vi": "nova"})
        self.assertEqual(settings.voice_for_language("vi"), "nova")

    def test_unmapped_language_falls_back_to_global_voice(self) -> None:
        settings = Settings(tts_voice="alloy", tts_voice_by_language={"vi": "nova"})
        self.assertEqual(settings.voice_for_language("fr"), "alloy")


class NarrationLanguageClauseTests(unittest.TestCase):
    def test_default_language_is_a_no_op(self) -> None:
        self.assertEqual(_language_clause("en"), "")

    def test_non_default_language_names_the_target(self) -> None:
        clause = _language_clause("vi")
        self.assertIn("Vietnamese", clause)
        self.assertIn("ENTIRE narration", clause)


class SceneSplitLanguageBlockTests(unittest.TestCase):
    def test_default_language_block_is_empty(self) -> None:
        self.assertEqual(_language_block("en"), "")

    def test_non_default_language_block_names_the_target(self) -> None:
        block = _language_block("vi")
        self.assertIn("Vietnamese", block)
        self.assertIn("LANGUAGE:", block)

    def test_user_message_carries_the_block_for_non_default_language(self) -> None:
        section = scene_split.SectionState(index=0, total=1, text="hello", id_prefix="")
        vi = build_user_message(section, "vertical", None, "hello", "vi")
        en = build_user_message(section, "vertical", None, "hello", "en")
        self.assertIn("LANGUAGE:", vi)
        self.assertNotIn("LANGUAGE:", en)


class FakeClient:
    """Replays queued JSON responses and records the messages each call got."""

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


class SceneSplitLanguageWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_language_reaches_user_message_not_system_prefix(self) -> None:
        settings = Settings()
        config = get_subject_config("chemistry", settings)
        client = FakeClient([json.dumps({"config": {"description": "d"}, "scenes": _SCENES})])

        await scene_split.generate_scenes(
            client, settings, config, "the script", "the transcript", language="vi"
        )

        system, user = client.calls[0][0], client.calls[0][1]
        # Per-request language instruction must stay out of the cache-stable prefix.
        self.assertNotIn("LANGUAGE:", system["content"])
        self.assertIn("Vietnamese", user["content"])


class RequestLanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_language_defaults_to_en_and_rejects_invalid(self) -> None:
        self.assertEqual(CreateVideoRequest(query="x").language, "en")
        self.assertEqual(CreateVideoRequest(query="x", language="vi").language, "vi")
        with self.assertRaises(ValidationError):
            CreateVideoRequest(query="x", language="fr")

    async def test_request_stores_and_echoes_language(self) -> None:
        settings = Settings()
        jobs = InMemoryJobRepository()
        queue = RecordingQueue()
        guard = RecordingGuard()

        response = await request_video(
            CreateVideoRequest(query="How does the pH scale work?", language="vi"),
            _request(settings, jobs, queue, guard),
        )

        self.assertEqual(response.status, JobStatus.PENDING)
        self.assertEqual(response.language, "vi")
        job = await jobs.get(response.id)
        self.assertIsNotNone(job)
        self.assertEqual(job.language, "vi")


if __name__ == "__main__":
    unittest.main()
