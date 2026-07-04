from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    use_stub_pipeline: bool = False

    llm_model: str = "gpt-4o-mini"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    transcribe_model: str = "whisper-1"

    hyperframes_dir: Path = Path("./render_kit")
    scene_schema_path: Path = Path("./schemas/scene_schema.json")
    artifacts_dir: Path = Path("./artifacts")

    worker_concurrency: int = 1
    max_retries: int = 1
    render_timeout_seconds: int = 600

    max_query_length: int = 300
