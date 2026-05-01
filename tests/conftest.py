from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest

# Set test-mode env vars BEFORE importing the app so settings see them.
os.environ.setdefault("API_TOKEN", "test-token-not-for-prod")
_TMP_DATA = Path(tempfile.mkdtemp(prefix="transcribe-svc-tests-"))
os.environ["DATA_DIR"] = str(_TMP_DATA)
os.environ["HF_HOME"] = str(_TMP_DATA / "models" / "hf")
os.environ.setdefault("WHISPERX_DEVICE", "cpu")
os.environ.setdefault("WHISPERX_COMPUTE_TYPE", "int8")


@pytest.fixture(scope="session")
def client() -> Iterator:
    """FastAPI TestClient with the heavy pipeline stubbed out so tests don't
    need whisperx / torch / pyannote installed.
    """
    from fastapi.testclient import TestClient

    from app import pipeline as pipeline_module
    from app.main import app

    async def _stub_load() -> None:
        return None

    async def _stub_transcribe(audio_path: Path, *, num_speakers=None, language=None):
        return {
            "segments": [
                {"start": 0.0, "end": 1.5, "text": "Hello there.", "speaker": "SPEAKER_00"},
                {"start": 1.6, "end": 3.0, "text": "Hi back.", "speaker": "SPEAKER_01"},
            ],
            "language": language or "en",
            "duration_seconds": 3.0,
            "speakers_detected": 2,
            "elapsed_seconds": 0.01,
        }

    pipeline_module.pipeline.load = _stub_load           # type: ignore[assignment]
    pipeline_module.pipeline.transcribe = _stub_transcribe  # type: ignore[assignment]

    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['API_TOKEN']}"}


@pytest.fixture
def fake_audio() -> bytes:
    # WhisperX is stubbed in conftest, so the actual bytes don't matter — we
    # just need something to multipart-upload.
    return b"RIFF" + b"\x00" * 64
