from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.config import settings

log = logging.getLogger("transcribe-svc.asr")


class ASRResult:
    """Simple container for ASR output. Fields:
    - segments: list of {'start': float, 'end': float, 'text': str, optional 'words': [...]}
    - language: detected language code (e.g. 'en')
    - duration_seconds: total audio duration
    - served_by: name() of the concrete backend that produced this result. The
      router leaves each backend's value intact so the pipeline can report which
      tier actually answered (not the whole chain) — the point of the field is
      diagnosing fallback drift.
    - model: the model identifier that backend used (e.g. 'medium' for local
      WhisperX, 'Systran/faster-whisper-large-v3' for Speaches), so the .json
      header reflects reality rather than the local fallback's config.
    """

    def __init__(
        self,
        segments: list[dict[str, Any]],
        language: str,
        duration_seconds: float,
        *,
        served_by: str | None = None,
        model: str | None = None,
    ) -> None:
        self.segments = segments
        self.language = language
        self.duration_seconds = duration_seconds
        self.served_by = served_by
        self.model = model

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": self.segments,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "served_by": self.served_by,
            "model": self.model,
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
            served_by=self.name(),
            model=settings.whisper_model,
        )

    def _get_align_model(self, language: str) -> tuple[Any, Any]:
        assert self._whisperx is not None
        if language not in self._align_models:
            self._align_models[language] = self._whisperx.load_align_model(
                language_code=language,
                device=settings.whisperx_device,
            )
        return self._align_models[language]


class SpeachesASR:
    """OpenAI-compatible ASR client. Works against Speaches, whisper.cpp
    server (with --inference-path /v1/audio/transcriptions), or any other
    server exposing that endpoint.

    base_url: e.g. 'http://localhost:8001'
    model_id: HuggingFace model ID passed as the 'model' form field.
    healthcheck_timeout_s: short timeout for the /v1/models liveness probe so
        a dead backend is skipped fast (the factory in pipeline.py wires this
        from settings.asr_healthcheck_timeout_s).
    """

    def __init__(
        self,
        base_url: str,
        model_id: str,
        *,
        timeout_s: float = 300.0,
        healthcheck_timeout_s: float = 2.0,
        response_format: str = "verbose_json",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._timeout = timeout_s
        self._healthcheck_timeout = healthcheck_timeout_s
        self._response_format = response_format
        self._client = httpx.AsyncClient(timeout=timeout_s)

    def name(self) -> str:
        return f"speaches@{self._base_url}"

    async def load(self) -> None:
        # No-op: model lives on the remote host, loaded on its first call.
        return None

    async def health(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self._base_url}/v1/models",
                timeout=self._healthcheck_timeout,
            )
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            return False
        except httpx.HTTPError:
            return False

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRResult:
        with audio_path.open("rb") as fh:
            data = fh.read()
        files = {"file": (audio_path.name, data, "audio/wav")}
        form: dict[str, str] = {
            "model": self._model_id,
            "response_format": self._response_format,
        }
        if language:
            form["language"] = language
        resp = await self._client.post(
            f"{self._base_url}/v1/audio/transcriptions",
            files=files,
            data=form,
        )
        resp.raise_for_status()
        body = resp.json()
        segments = [
            {
                "start": float(s.get("start") or 0.0),
                "end": float(s.get("end") or 0.0),
                "text": str(s.get("text") or ""),
            }
            for s in body.get("segments", [])
        ]
        duration = float(body.get("duration") or 0.0)
        detected_language = str(body.get("language") or language or "en")
        return ASRResult(
            segments=segments,
            language=detected_language,
            duration_seconds=duration,
            served_by=self.name(),
            model=self._model_id,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class ASRRouter:
    """Tries backends in priority order. First one that passes a health
    check gets the request. If its transcribe() raises, falls through to
    the next healthy backend. If none respond, raises RuntimeError.
    """

    def __init__(self, backends: list[ASRBackend]) -> None:
        if not backends:
            raise ValueError("ASRRouter requires at least one backend")
        self._backends = backends

    def name(self) -> str:
        return "router[" + ",".join(b.name() for b in self._backends) + "]"

    async def load(self) -> None:
        # Backends load lazily on first call; nothing to do here.
        return None

    async def health(self) -> bool:
        for b in self._backends:
            if await b.health():
                return True
        return False

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRResult:
        last_exc: Exception | None = None
        for b in self._backends:
            if not await b.health():
                log.info("ASRRouter: backend %s unhealthy, skipping", b.name())
                continue
            try:
                log.info("ASRRouter: routing to %s", b.name())
                result = await b.transcribe(audio_path, language=language)
                # Preserve the concrete backend's identity; only fill in if a
                # backend didn't set it, so the pipeline reports the tier that
                # actually served rather than the whole router chain.
                if result.served_by is None:
                    result.served_by = b.name()
                return result
            except Exception as e:
                log.warning(
                    "ASRRouter: backend %s failed mid-request (%s); falling through",
                    b.name(), e,
                )
                last_exc = e
        if last_exc is not None:
            raise RuntimeError(f"no healthy ASR backend responded successfully: {last_exc}") from last_exc
        raise RuntimeError("no healthy ASR backend responded successfully")
