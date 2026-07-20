"""Artifact boundary.

Artifacts (script, audio, transcript, scenes, data.json, video) live only on
disk under <artifacts_dir>/<job_id>/<name> — never on the Job model. Everything
is resolvable from job_id alone. Swap LocalArtifactStore for an S3-backed
implementation without touching pipeline or API code.
"""
import json
import shutil
from pathlib import Path
from typing import Any, Protocol


class ArtifactStore(Protocol):
    def path_for(self, job_id: str, name: str) -> Path: ...

    def save_bytes(self, job_id: str, name: str, data: bytes) -> Path: ...

    def save_text(self, job_id: str, name: str, text: str) -> Path: ...

    def save_json(self, job_id: str, name: str, obj: Any) -> Path: ...

    def load_json(self, job_id: str, name: str) -> Any: ...

    def exists(self, job_id: str, name: str) -> bool: ...

    def list_names(self, job_id: str) -> list[str]: ...

    def delete_all(self, job_id: str) -> None: ...


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, job_id: str, name: str) -> Path:
        return self._root / job_id / name

    def save_bytes(self, job_id: str, name: str, data: bytes) -> Path:
        path = self.path_for(job_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def save_text(self, job_id: str, name: str, text: str) -> Path:
        return self.save_bytes(job_id, name, text.encode("utf-8"))

    def save_json(self, job_id: str, name: str, obj: Any) -> Path:
        return self.save_text(job_id, name, json.dumps(obj, indent=2, ensure_ascii=False))

    def load_json(self, job_id: str, name: str) -> Any:
        return json.loads(self.path_for(job_id, name).read_text("utf-8"))

    def exists(self, job_id: str, name: str) -> bool:
        return self.path_for(job_id, name).is_file()

    def list_names(self, job_id: str) -> list[str]:
        job_dir = self._root / job_id
        if not job_dir.is_dir():
            return []
        return sorted(p.name for p in job_dir.iterdir() if p.is_file())

    def delete_all(self, job_id: str) -> None:
        """Remove a job's entire artifact directory. No-op if it doesn't
        exist. Refuses anything that isn't a direct child of the artifact root
        (defensive against a traversing job_id on this destructive path)."""
        job_dir = (self._root / job_id).resolve()
        if job_dir.parent != self._root.resolve():
            return
        if job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)
