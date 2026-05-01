from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_token: str = Field(default="", alias="API_TOKEN")

    whisper_model: str = Field(default="medium", alias="WHISPER_MODEL")
    whisperx_compute_type: str = Field(default="int8", alias="WHISPERX_COMPUTE_TYPE")
    whisperx_device: str = Field(default="cpu", alias="WHISPERX_DEVICE")
    whisper_language: str = Field(default="", alias="WHISPER_LANGUAGE")

    diarization_model: str = Field(
        default="pyannote/speaker-diarization-community-1",
        alias="DIARIZATION_MODEL",
    )
    hf_token: str = Field(default="", alias="HF_TOKEN")

    max_concurrent_jobs: int = Field(default=1, alias="MAX_CONCURRENT_JOBS")
    retain_days: int = Field(default=30, alias="RETAIN_DAYS")

    data_dir: Path = Field(default=Path("/data"), alias="DATA_DIR")
    hf_home: Path = Field(default=Path("/data/models/hf"), alias="HF_HOME")

    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    max_upload_mb: int = Field(default=500, alias="MAX_UPLOAD_MB")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def language_or_none(self) -> str | None:
        return self.whisper_language or None


settings = Settings()
