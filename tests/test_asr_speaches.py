from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.asr import SpeachesASR


@pytest.fixture
def tmp_audio(tmp_path: Path) -> Path:
    p = tmp_path / "clip.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 100)
    return p


@pytest.mark.asyncio
async def test_speaches_parses_verbose_json(tmp_audio: Path) -> None:
    fake_body = {
        "text": "hello world how are you",
        "language": "en",
        "duration": 3.5,
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "hello world"},
            {"start": 1.5, "end": 3.5, "text": "how are you"},
        ],
    }
    backend = SpeachesASR(
        base_url="http://localhost:8001",
        model_id="Systran/faster-whisper-large-v3",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = AsyncMock()
    mock_response.json = lambda: fake_body
    mock_response.raise_for_status = lambda: None
    mock_client.post = AsyncMock(return_value=mock_response)
    with patch.object(backend, "_client", mock_client):
        result = await backend.transcribe(tmp_audio)
    assert len(result.segments) == 2
    assert result.segments[0]["text"] == "hello world"
    assert result.language == "en"
    assert result.duration_seconds == 3.5


@pytest.mark.asyncio
async def test_speaches_health_ok_on_200(tmp_audio: Path) -> None:
    backend = SpeachesASR(
        base_url="http://localhost:8001",
        model_id="Systran/faster-whisper-large-v3",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client.get = AsyncMock(return_value=mock_response)
    with patch.object(backend, "_client", mock_client):
        assert await backend.health() is True


@pytest.mark.asyncio
async def test_speaches_health_false_on_connect_error() -> None:
    backend = SpeachesASR(
        base_url="http://localhost:8001",
        model_id="Systran/faster-whisper-large-v3",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("nope"))
    with patch.object(backend, "_client", mock_client):
        assert await backend.health() is False


@pytest.mark.asyncio
async def test_speaches_name() -> None:
    backend = SpeachesASR(
        base_url="http://localhost:8001",
        model_id="whatever",
    )
    assert backend.name() == "speaches@http://localhost:8001"
