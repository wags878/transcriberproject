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
    # served_by/model identify the tier + model that produced this result.
    assert result.served_by == "speaches@http://localhost:8001"
    assert result.model == "Systran/faster-whisper-large-v3"


def _mock_post_client(fake_body):
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = AsyncMock()
    mock_response.json = lambda: fake_body
    mock_response.raise_for_status = lambda: None
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.asyncio
async def test_transcribe_hits_transcriptions_with_language(tmp_audio: Path) -> None:
    backend = SpeachesASR(base_url="http://localhost:8001", model_id="m")
    mock_client = _mock_post_client({"language": "es", "duration": 1.0, "segments": []})
    with patch.object(backend, "_client", mock_client):
        await backend.transcribe(tmp_audio, language="es", task="transcribe")
    url = mock_client.post.call_args.args[0]
    form = mock_client.post.call_args.kwargs["data"]
    assert url.endswith("/v1/audio/transcriptions")
    assert form["language"] == "es"


@pytest.mark.asyncio
async def test_translate_hits_translations_without_language(tmp_audio: Path) -> None:
    backend = SpeachesASR(base_url="http://localhost:8001", model_id="m")
    mock_client = _mock_post_client({"language": "es", "duration": 1.0, "segments": []})
    with patch.object(backend, "_client", mock_client):
        await backend.transcribe(tmp_audio, language="es", task="translate")
    url = mock_client.post.call_args.args[0]
    form = mock_client.post.call_args.kwargs["data"]
    # translations endpoint outputs English; source is auto-detected (no language).
    assert url.endswith("/v1/audio/translations")
    assert "language" not in form


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
