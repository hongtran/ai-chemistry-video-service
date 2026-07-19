from app.config import Settings
from app.subjects import chemistry, tech
from app.subjects.base import SUPPORTED_SUBJECTS, SubjectConfig, SubjectName


def get_subject_config(subject: str, settings: Settings) -> SubjectConfig:
    if subject == "chemistry":
        return chemistry.get_config(settings)
    if subject == "tech":
        return tech.get_config(settings)
    raise ValueError(f"Unsupported subject: {subject}")


__all__ = [
    "SUPPORTED_SUBJECTS",
    "SubjectConfig",
    "SubjectName",
    "get_subject_config",
]
