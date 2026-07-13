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

    # Diarization backend. 'local' — in-process pyannote on CPU (default,
    # Phase 2/3 behavior). 'remote' — offload to the GPU diarize-svc sidecar at
    # DIARIZE_URL, with a per-request fallback to local CPU if it's unreachable.
    diarize_backend: str = Field(default="local", alias="DIARIZE_BACKEND")
    diarize_url: str = Field(default="", alias="DIARIZE_URL")
    diarize_healthcheck_timeout_s: float = Field(
        default=3.0, alias="DIARIZE_HEALTHCHECK_TIMEOUT_S"
    )

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

    # --- Track B: speaker role labels (voice enrollment) ---
    # Off by default: when false the /v1 contract is unchanged and speakers stay
    # anonymous SPEAKER_00/01. When true, the pipeline embeds each diarized
    # cluster after stitching, cosine-matches it against enrolled voiceprints in
    # ENROLLMENTS_DIR, and relabels matches (e.g. 'Therapist'), inferring the
    # other speaker as CLIENT_LABEL in a 2-speaker session.
    enable_role_labels: bool = Field(default=False, alias="ENABLE_ROLE_LABELS")
    # Directory of enrolled *.npy voiceprints (built offline by ml/enroll). These
    # are biometric data — keep them out of the repo and off shared storage.
    enrollments_dir: Path = Field(default=Path("/data/enrollments"), alias="ENROLLMENTS_DIR")
    # Pretrained speaker-embedding model (pyannote wespeaker; already cached by
    # the diarizer, so no new dependency). See ml/enroll/embed.py.
    embedding_model: str = Field(
        default="pyannote/wespeaker-voxceleb-resnet34-LM",
        alias="EMBEDDING_MODEL",
    )
    # Minimum cosine similarity to accept an enrollment match. Operating point
    # chosen by ml/enroll/sweep.py — see its report. Conservative default.
    role_match_threshold: float = Field(default=0.5, alias="ROLE_MATCH_THRESHOLD")
    # Label applied to the non-enrolled speaker in a 2-speaker session.
    client_label: str = Field(default="Client", alias="CLIENT_LABEL")

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

    @property
    def hf_home_str(self) -> str:
        return str(self.hf_home)


settings = Settings()
