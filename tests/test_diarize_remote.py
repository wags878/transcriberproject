"""RemoteDiarizer — GPU sidecar client with CPU fallback. Torch-free."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx

from app.diarize import RemoteDiarizer


class _FakeFallback:
    """Stand-in for the local CPU Diarizer."""
    def __init__(self) -> None:
        self.called = False

    async def turns(self, audio_path: Path, *, num_speakers: int | None = None):
        self.called = True
        return [{"start": 0.0, "end": 1.0, "speaker": "CPU_SPEAKER"}]


def _audio(tmp_path: Path) -> Path:
    p = tmp_path / "clip.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 32)
    return p


async def test_remote_returns_turns_on_success(tmp_path: Path) -> None:
    fb = _FakeFallback()
    rd = RemoteDiarizer("http://sidecar:8002", fallback=fb)
    mock = AsyncMock(spec=httpx.AsyncClient)
    health = AsyncMock(); health.status_code = 200
    mock.get = AsyncMock(return_value=health)
    post = AsyncMock()
    post.json = lambda: {"turns": [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}], "device": "cuda"}
    post.raise_for_status = lambda: None
    mock.post = AsyncMock(return_value=post)
    rd._client = mock

    turns = await rd.turns(_audio(tmp_path))
    assert turns == [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}]
    assert rd.last_device == "cuda"
    assert fb.called is False  # GPU path served; no fallback


async def test_remote_falls_back_to_cpu_when_unhealthy(tmp_path: Path) -> None:
    fb = _FakeFallback()
    rd = RemoteDiarizer("http://sidecar:8002", fallback=fb)
    mock = AsyncMock(spec=httpx.AsyncClient)
    mock.get = AsyncMock(side_effect=httpx.ConnectError("sidecar down"))
    rd._client = mock

    turns = await rd.turns(_audio(tmp_path))
    assert fb.called is True
    assert turns[0]["speaker"] == "CPU_SPEAKER"
    assert rd.last_device == "cpu-fallback"


async def test_remote_falls_back_to_cpu_on_request_error(tmp_path: Path) -> None:
    fb = _FakeFallback()
    rd = RemoteDiarizer("http://sidecar:8002", fallback=fb)
    mock = AsyncMock(spec=httpx.AsyncClient)
    health = AsyncMock(); health.status_code = 200
    mock.get = AsyncMock(return_value=health)
    mock.post = AsyncMock(side_effect=httpx.ReadTimeout("GPU OOM"))
    rd._client = mock

    turns = await rd.turns(_audio(tmp_path))
    assert fb.called is True
    assert rd.last_device == "cpu-fallback"


async def test_remote_passes_num_speakers(tmp_path: Path) -> None:
    fb = _FakeFallback()
    rd = RemoteDiarizer("http://sidecar:8002", fallback=fb)
    mock = AsyncMock(spec=httpx.AsyncClient)
    health = AsyncMock(); health.status_code = 200
    mock.get = AsyncMock(return_value=health)
    post = AsyncMock()
    post.json = lambda: {"turns": [], "device": "cuda"}
    post.raise_for_status = lambda: None
    mock.post = AsyncMock(return_value=post)
    rd._client = mock

    await rd.turns(_audio(tmp_path), num_speakers=3)
    assert mock.post.call_args.kwargs["data"] == {"num_speakers": "3"}
