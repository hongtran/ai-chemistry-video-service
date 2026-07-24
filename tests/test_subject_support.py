import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.router import request_video
from app.api.schemas import CreateVideoRequest
from app.config import Settings
from app.domain.models import JobStatus
from app.llm.client import GuardResult
from app.pipeline.steps import compose, scene_split
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


def _request(
    settings: Settings,
    jobs: InMemoryJobRepository,
    queue: RecordingQueue,
    guard: RecordingGuard,
) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                jobs=jobs,
                artifacts=None,
                queue=queue,
                guard=guard,
                normalizer=None,
            )
        )
    )


class SubjectSupportTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_defaults_to_lab_management_and_stores_subject(self) -> None:
        settings = Settings()
        jobs = InMemoryJobRepository()
        queue = RecordingQueue()
        guard = RecordingGuard()

        response = await request_video(
            CreateVideoRequest(query="  How does the pH scale work?  "),
            _request(settings, jobs, queue, guard),
        )

        self.assertEqual(response.status, JobStatus.PENDING)
        self.assertEqual(response.subject, "lab-management")
        self.assertEqual(response.orientation, "vertical")
        self.assertEqual(
            guard.calls, [("How does the pH scale work?", "lab-management")]
        )
        self.assertEqual(queue.enqueued, [response.id])
        job = await jobs.get(response.id)
        self.assertIsNotNone(job)
        self.assertEqual(job.subject, "lab-management")
        self.assertEqual(job.orientation, "vertical")

    async def test_guard_rejection_does_not_enqueue(self) -> None:
        settings = Settings()
        jobs = InMemoryJobRepository()
        queue = RecordingQueue()
        guard = RecordingGuard(GuardResult(is_valid=False, reason="not in domain"))

        with self.assertRaises(HTTPException) as caught:
            await request_video(
                CreateVideoRequest(query="World War II"),
                _request(settings, jobs, queue, guard),
            )

        self.assertEqual(getattr(caught.exception, "status_code"), 400)
        self.assertEqual(queue.enqueued, [])
        self.assertEqual(await jobs.list(), [])

    def test_lab_management_and_tech_subjects_are_accepted_others_are_not(self) -> None:
        for subject in ("lab-management", "tech"):
            request = CreateVideoRequest(query="What is RAG?", subject=subject)
            self.assertEqual(request.subject, subject)
        with self.assertRaises(ValidationError):
            CreateVideoRequest(query="World War II", subject="history")

    def test_orientation_defaults_to_vertical_and_rejects_invalid(self) -> None:
        self.assertEqual(CreateVideoRequest(query="x").orientation, "vertical")
        self.assertEqual(
            CreateVideoRequest(query="x", orientation="horizontal").orientation,
            "horizontal",
        )
        with self.assertRaises(ValidationError):
            CreateVideoRequest(query="x", orientation="diagonal")

    def test_subject_config_drives_schema_and_compose_fallback(self) -> None:
        settings = Settings()
        subject_config = get_subject_config("lab-management", settings)

        schema = scene_split.load_scene_schema(subject_config)
        data = compose.build_data("!!!", "12345678-xxxx", subject_config, [], 1.23)
        expected_schema_path = (
            settings.hyperframes_dir / "templates" / "lab-management" / "schema.json"
        )

        self.assertEqual(subject_config.renderer_template, "lab-management")
        self.assertEqual(subject_config.scene_schema_path, expected_schema_path)
        self.assertIsInstance(subject_config.narration_style, str)
        self.assertIsInstance(subject_config.scene_split_prompt, str)
        self.assertIn("ISO/IEC 17025", subject_config.narration_style)
        self.assertIn("GOLDEN EXAMPLES", subject_config.scene_examples)
        self.assertIn("scenes", schema["properties"])
        self.assertEqual(data["config"]["slug"], "lab-management-video-12345678")
        self.assertEqual(data["config"]["orientation"], "vertical")
        self.assertEqual(data["config"]["width"], 1080)
        self.assertEqual(data["config"]["height"], 1920)

    def test_tech_subject_config_is_well_formed(self) -> None:
        settings = Settings()
        subject_config = get_subject_config("tech", settings)

        schema = scene_split.load_scene_schema(subject_config)
        expected_schema_path = settings.hyperframes_dir / "templates" / "tech" / "schema.json"

        self.assertEqual(subject_config.renderer_template, "tech")
        self.assertEqual(subject_config.scene_schema_path, expected_schema_path)
        self.assertIn("scenes", schema["properties"])
        schema_types = set(
            schema["properties"]["scenes"]["items"]["properties"]["type"]["enum"]
        )
        self.assertEqual(schema_types, set(subject_config.required_content_fields))

    def test_unsupported_subject_raises(self) -> None:
        settings = Settings()
        with self.assertRaises(ValueError):
            get_subject_config("history", settings)

    def test_settings_no_longer_exposes_legacy_schema_path(self) -> None:
        self.assertFalse(hasattr(Settings(), "scene_schema_path"))
        self.assertFalse(Path("schemas/scene_schema.json").exists())


if __name__ == "__main__":
    unittest.main()
