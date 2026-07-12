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

    # --- Phase 3: ASR routing ---
    # 'whisperx' — in-container WhisperX only (Phase 2 behavior; default).
    # 'router'   — try backends in ASR_HOSTS order; sentinel 'local-whisperx'
    #              means "fall back to in-container WhisperX".
    asr_backend: str = Field(default="whisperx", alias="ASR_BACKEND")

    # Comma-separated priority list. URL entries are OpenAI-compat backends
    # (Speaches, whisper.cpp server). The sentinel 'local-whisperx' means
    # in-container WhisperX. Example:
    #   ASR_HOSTS=http://localhost:8001,http://mbp.tailnet.ts.net:8001,local-whisperx
    asr_hosts: str = Field(default="", alias="ASR_HOSTS")

    # HuggingFace model ID to pass to OpenAI-compat backends. Ignored for
    # local-whisperx (which uses WHISPER_MODEL instead).
    asr_model_id: str = Field(
        default="Systran/faster-whisper-large-v3",
        alias="ASR_MODEL_ID",
    )

    # Health-check timeout per backend in seconds.
    asr_healthcheck_timeout_s: float = Field(default=2.0, alias="ASR_HEALTHCHECK_TIMEOUT_S")

    # Tailscale sidecar reusable auth key. Only consumed by the tailscale
    # container itself; the app never reads it. Kept in Settings so a
    # missing value is a clear config error at startup.
    ts_authkey: str = Field(default="", alias="TS_AUTHKEY")

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
