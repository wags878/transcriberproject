from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.asr import ASRResult, ASRRouter


class _StubBackend:
    def __init__(self, name: str, *, healthy: bool = True, segments: list[dict[str, Any]] | None = None) -> None:
        self._name = name
        self._healthy = healthy
        self._segments = segments if segments is not None else [{"start": 0.0, "end": 1.0, "text": name}]
        self.transcribe_called = False

    def name(self) -> str:
        return self._name

    async def load(self) -> None:
        return None

    async def health(self) -> bool:
        return self._healthy

    async def transcribe(self, audio_path: Path, *, language: str | None = None,
                         task: str = "transcribe") -> ASRResult:
        self.transcribe_called = True
        self.transcribe_task = task
        return ASRResult(segments=self._segments, language="en", duration_seconds=1.0)


@pytest.fixture
def tmp_audio(tmp_path: Path) -> Path:
    p = tmp_path / "clip.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 32)
    return p


async def test_router_first_healthy_wins(tmp_audio: Path) -> None:
    a = _StubBackend("A", healthy=True)
    b = _StubBackend("B", healthy=True)
    router = ASRRouter([a, b])
    result = await router.transcribe(tmp_audio)
    assert a.transcribe_called is True
    assert b.transcribe_called is False
    assert result.segments[0]["text"] == "A"
    assert result.served_by == "A"


async def test_router_falls_through_when_first_unhealthy(tmp_audio: Path) -> None:
    a = _StubBackend("A", healthy=False)
    b = _StubBackend("B", healthy=True)
    router = ASRRouter([a, b])
    result = await router.transcribe(tmp_audio)
    assert a.transcribe_called is False
    assert b.transcribe_called is True
    assert result.segments[0]["text"] == "B"
    assert result.served_by == "B"


async def test_router_propagates_task(tmp_audio: Path) -> None:
    a = _StubBackend("A", healthy=True)
    router = ASRRouter([a])
    await router.transcribe(tmp_audio, task="translate")
    assert a.transcribe_task == "translate"


async def test_router_raises_when_all_unhealthy(tmp_audio: Path) -> None:
    a = _StubBackend("A", healthy=False)
    b = _StubBackend("B", healthy=False)
    router = ASRRouter([a, b])
    with pytest.raises(RuntimeError, match="no healthy ASR backend"):
        await router.transcribe(tmp_audio)


async def test_router_falls_through_on_transcribe_exception(tmp_audio: Path) -> None:
    class _Boom(_StubBackend):
        async def transcribe(self, audio_path: Path, *, language: str | None = None,
                             task: str = "transcribe") -> ASRResult:
            raise RuntimeError("kaboom")

    a = _Boom("A", healthy=True)
    b = _StubBackend("B", healthy=True)
    router = ASRRouter([a, b])
    result = await router.transcribe(tmp_audio)
    assert b.transcribe_called is True
    assert result.segments[0]["text"] == "B"


def test_router_name_lists_backends() -> None:
    a = _StubBackend("A")
    b = _StubBackend("B")
    router = ASRRouter([a, b])
    assert router.name() == "router[A,B]"
