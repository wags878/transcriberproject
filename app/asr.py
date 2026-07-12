from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Protocol

from app.config import settings

log = logging.getLogger("transcribe-svc.asr")


class ASRResult:
    """Simple container for ASR output. Fields:
    - segments: list of {'start': float, 'end': float, 'text': str, optional 'words': [...]}
    - language: detected language code (e.g. 'en')
    - duration_seconds: total audio duration
    """

    def __init__(
        self,
        segments: list[dict[str, Any]],
        language: str,
        duration_seconds: float,
    ) -> None:
        self.segments = segments
        self.language = language
        self.duration_seconds = duration_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": self.segments,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
        }


class ASRBackend(Protocol):
    """Common surface for every ASR backend (local WhisperX, Speaches,
    whisper.cpp server, future engines).
    """

    async def load(self) -> None: ...
    async def health(self) -> bool: ...
    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRResult: ...
    def name(self) -> str: ...


class LocalWhisperXASR:
    """In-process WhisperX ASR. Fallback tier; also the current Phase 2 path
    when ASR_HOSTS is unset. Loads the model lazily.
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._whisperx: Any | None = None
        self._align_models: dict[str, tuple[Any, Any]] = {}
        self._loaded: bool = False
        self._load_lock = asyncio.Lock()

    def name(self) -> str:
        return "local-whisperx"

    async def load(self) -> None:
        async with self._load_lock:
            if self._loaded:
                return
            log.info(
                "Loading whisperx (model=%s, device=%s, compute_type=%s)",
                settings.whisper_model,
                settings.whisperx_device,
                settings.whisperx_compute_type,
            )
            await asyncio.to_thread(self._load_blocking)
            self._loaded = True

    def _load_blocking(self) -> None:
        os.environ.setdefault("HF_HOME", str(settings.hf_home))
        import whisperx  # type: ignore

        model = whisperx.load_model(
            settings.whisper_model,
            device=settings.whisperx_device,
            compute_type=settings.whisperx_compute_type,
        )
        self._whisperx = whisperx
        self._model = model

    async def health(self) -> bool:
        # Local backend is always available; loading may be slow but never
        # unreachable. Return True even before load — the pipeline will
        # trigger load() on first use.
        return True

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRResult:
        if not self._loaded:
            await self.load()
        return await asyncio.to_thread(self._transcribe_blocking, audio_path, language)

    def _transcribe_blocking(
        self,
        audio_path: Path,
        language: str | None,
    ) -> ASRResult:
        assert self._model is not None and self._whisperx is not None
        whisperx = self._whisperx
        audio = whisperx.load_audio(str(audio_path))
        duration_seconds = float(len(audio)) / 16000.0

        transcribe_kwargs: dict[str, Any] = {}
        if language:
            transcribe_kwargs["language"] = language
        result = self._model.transcribe(audio, **transcribe_kwargs)
        detected_language = result.get("language", language or "en")

        try:
            align_model, align_meta = self._get_align_model(detected_language)
            aligned = whisperx.align(
                result["segments"],
                align_model,
                align_meta,
                audio,
                settings.whisperx_device,
                return_char_alignments=False,
            )
            segments = aligned.get("segments", [])
        except Exception as e:
            log.warning(
                "Alignment failed for language=%s: %s; returning coarse segments.",
                detected_language, e,
            )
            segments = result.get("segments", [])

        return ASRResult(
            segments=segments,
            language=detected_language,
            duration_seconds=duration_seconds,
        )

    def _get_align_model(self, language: str) -> tuple[Any, Any]:
        assert self._whisperx is not None
        if language not in self._align_models:
            self._align_models[language] = self._whisperx.load_align_model(
                language_code=language,
                device=settings.whisperx_device,
            )
        return self._align_models[language]
