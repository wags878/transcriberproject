from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger("transcribe-svc.pipeline")


class TranscribePipeline:
    """Wrapper around WhisperX. Loads models once and serves transcription requests
    serialized through an asyncio.Semaphore.

    Heavy imports (whisperx / torch) are deferred to load() so that test code can
    instantiate this class without pulling them in.
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._diarizer: Any | None = None
        self._align_models: dict[str, tuple[Any, Any]] = {}
        self._whisperx: Any | None = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._load_lock = asyncio.Lock()

    def is_loaded(self) -> bool:
        return self._model is not None

    async def load(self) -> None:
        """Load whisper + diarization models once. Idempotent."""
        async with self._load_lock:
            if self._model is not None:
                return
            log.info(
                "Loading whisperx (model=%s, device=%s, compute_type=%s)",
                settings.whisper_model,
                settings.whisperx_device,
                settings.whisperx_compute_type,
            )
            await asyncio.to_thread(self._load_blocking)
            log.info("Models loaded.")

    def _load_blocking(self) -> None:
        os.environ.setdefault("HF_HOME", str(settings.hf_home))
        import whisperx  # type: ignore

        self._whisperx = whisperx
        self._model = whisperx.load_model(
            settings.whisper_model,
            device=settings.whisperx_device,
            compute_type=settings.whisperx_compute_type,
        )
        diarize_kwargs: dict[str, Any] = {"device": settings.whisperx_device}
        if settings.hf_token:
            diarize_kwargs["use_auth_token"] = settings.hf_token
        self._diarizer = whisperx.DiarizationPipeline(
            model_name=settings.diarization_model,
            **diarize_kwargs,
        )

    async def transcribe(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Run the full transcribe -> align -> diarize -> assign-speakers pipeline.

        Concurrency is gated by self._semaphore so we never exceed
        MAX_CONCURRENT_JOBS regardless of request rate.
        """
        if self._model is None:
            await self.load()
        async with self._semaphore:
            return await asyncio.to_thread(
                self._transcribe_blocking,
                audio_path,
                num_speakers,
                language,
            )

    def _transcribe_blocking(
        self,
        audio_path: Path,
        num_speakers: int | None,
        language: str | None,
    ) -> dict[str, Any]:
        assert self._model is not None and self._diarizer is not None and self._whisperx is not None
        whisperx = self._whisperx

        t_start = time.monotonic()
        audio = whisperx.load_audio(str(audio_path))
        duration_seconds = float(len(audio)) / 16000.0  # whisperx loads at 16kHz

        transcribe_kwargs: dict[str, Any] = {}
        if language:
            transcribe_kwargs["language"] = language
        result = self._model.transcribe(audio, **transcribe_kwargs)
        detected_language = result.get("language", language or "en")

        # Alignment is per-language; cache by language code.
        try:
            align_model, align_meta = self._get_align_model(detected_language)
            result = whisperx.align(
                result["segments"],
                align_model,
                align_meta,
                audio,
                settings.whisperx_device,
                return_char_alignments=False,
            )
        except Exception as e:  # alignment failures should not fail the whole job
            log.warning("Alignment failed for language=%s: %s; continuing without word timings.", detected_language, e)

        diarize_kwargs: dict[str, Any] = {}
        if num_speakers is not None:
            diarize_kwargs["num_speakers"] = num_speakers
        diarize_segments = self._diarizer(audio, **diarize_kwargs)
        result = whisperx.assign_word_speakers(diarize_segments, result)

        speakers = self._count_speakers(result)
        elapsed = time.monotonic() - t_start
        log.info(
            "Transcribed %.1fs of audio in %.1fs (%.2fx realtime); language=%s speakers=%d",
            duration_seconds,
            elapsed,
            (duration_seconds / elapsed) if elapsed > 0 else 0.0,
            detected_language,
            speakers,
        )
        return {
            "segments": result.get("segments", []),
            "language": detected_language,
            "duration_seconds": duration_seconds,
            "speakers_detected": speakers,
            "elapsed_seconds": elapsed,
        }

    def _get_align_model(self, language: str) -> tuple[Any, Any]:
        assert self._whisperx is not None
        if language not in self._align_models:
            self._align_models[language] = self._whisperx.load_align_model(
                language_code=language,
                device=settings.whisperx_device,
            )
        return self._align_models[language]

    @staticmethod
    def _count_speakers(result: dict[str, Any]) -> int:
        speakers: set[str] = set()
        for seg in result.get("segments", []):
            spk = seg.get("speaker")
            if spk:
                speakers.add(spk)
            for word in seg.get("words", []):
                spk = word.get("speaker")
                if spk:
                    speakers.add(spk)
        return len(speakers)


def render_txt(result: dict[str, Any]) -> str:
    """Render WhisperX-style result to a speaker-labeled .txt.

    Phase 1 output: '[mm:ss] SPEAKER_XX: text', one line per segment.
    Phase 2 will do paragraph-per-turn merging.
    """
    lines: list[str] = []
    for seg in result.get("segments", []):
        start = float(seg.get("start") or 0.0)
        speaker = seg.get("speaker") or "SPEAKER_??"
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        mm = int(start // 60)
        ss = int(start % 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {speaker}: {text}")
    return "\n\n".join(lines) + ("\n" if lines else "")


def render_json(job_id: str, result: dict[str, Any]) -> str:
    """Render WhisperX result + a header to JSON string."""
    payload = {
        "id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": result.get("duration_seconds"),
        "language": result.get("language"),
        "speakers_detected": result.get("speakers_detected"),
        "model": settings.whisper_model,
        "compute_type": settings.whisperx_compute_type,
        "device": settings.whisperx_device,
        "diarization_model": settings.diarization_model,
        "segments": result.get("segments", []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)


def _json_default(obj: Any) -> Any:
    # WhisperX may return numpy floats / ints in segments.
    try:
        return float(obj)
    except (TypeError, ValueError):
        return str(obj)


pipeline = TranscribePipeline()
