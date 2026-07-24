import unittest
from types import SimpleNamespace

from app.api.router import request_video
from app.api.schemas import CreateVideoRequest
from app.config import Settings
from app.llm.client import (
    NormalizerUnavailableError,
    ScriptNormalization,
)
from app.storage.jobs import InMemoryJobRepository


class RecordingQueue:
    def __init__(self) -> None:
        self.enqueued: list[str] = []

    async def enqueue(self, job_id: str) -> None:
        self.enqueued.append(job_id)


class RecordingNormalizer:
    """Returns a fixed normalization and records how it was called."""

    def __init__(self, result: ScriptNormalization) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    async def normalize(self, script, subject, language) -> ScriptNormalization:
        self.calls.append((script, subject, language))
        return self.result


class FailingNormalizer:
    async def normalize(self, script, subject, language) -> ScriptNormalization:
        raise NormalizerUnavailableError("boom")


def _request(settings, jobs, queue, normalizer) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                jobs=jobs,
                artifacts=None,
                queue=queue,
                guard=None,
                normalizer=normalizer,
            )
        )
    )


RAW_SCRIPT = "# My Heading\n\n- point one\n- point two\n\nUse top_k for retrieval."


class ScriptNormalizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalized_narration_and_title_overwrite_job(self) -> None:
        settings = Settings()
        jobs = InMemoryJobRepository()
        queue = RecordingQueue()
        normalizer = RecordingNormalizer(
            ScriptNormalization(
                title="Retrieval basics",
                narration="Use top k for retrieval.",
            )
        )

        response = await request_video(
            CreateVideoRequest(
                input_mode="script", script=RAW_SCRIPT, subject="tech"
            ),
            _request(settings, jobs, queue, normalizer),
        )

        # Normalizer received the raw script for this subject/language.
        self.assertEqual(normalizer.calls, [(RAW_SCRIPT, "tech", "en")])
        job = await jobs.get(response.id)
        self.assertIsNotNone(job)
        # The cleaned narration and LLM title are what persist on the job.
        self.assertEqual(job.script, "Use top k for retrieval.")
        self.assertEqual(job.query, "Retrieval basics")
        self.assertIn(job.id, queue.enqueued)

    async def test_falls_back_to_raw_script_and_heuristic_title_on_failure(self) -> None:
        settings = Settings()
        jobs = InMemoryJobRepository()
        queue = RecordingQueue()

        response = await request_video(
            CreateVideoRequest(
                input_mode="script", script=RAW_SCRIPT, subject="tech"
            ),
            _request(settings, jobs, queue, FailingNormalizer()),
        )

        job = await jobs.get(response.id)
        self.assertIsNotNone(job)
        # Job is still created; raw script kept verbatim, title from the first line.
        self.assertEqual(job.script, RAW_SCRIPT)
        self.assertEqual(job.query, "# My Heading")
        self.assertIn(job.id, queue.enqueued)


if __name__ == "__main__":
    unittest.main()
