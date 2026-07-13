from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.asr import ASRBackend, ASRResult, LocalWhisperXASR
from app.config import settings
from app.diarize import Diarizer, RemoteDiarizer, build_diarizer
from app.stitch import stitch_speakers

log = logging.getLogger("transcribe-svc.pipeline")


class TranscribePipeline:
    """Orchestrates: ASR (any ASRBackend) + diarization (Diarizer) run
    concurrently on the same audio; then stitch_speakers() joins them.

    Concurrency across requests is gated by a semaphore sized to
    settings.max_concurrent_jobs.
    """

    def __init__(
        self,
        asr: ASRBackend | None = None,
        diarizer: "Diarizer | RemoteDiarizer | None" = None,
    ) -> None:
        self._asr: ASRBackend = asr or LocalWhisperXASR()
        self._diarizer: Diarizer | RemoteDiarizer = diarizer or build_diarizer()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._loaded = False
        self._load_lock = asyncio.Lock()

    def is_loaded(self) -> bool:
        return self._loaded

    async def load(self) -> None:
        async with self._load_lock:
            if self._loaded:
                return
            # Load in parallel — ASR load may be a no-op for HTTP-backed
            # backends, and diarization can pull from HF concurrently.
            await asyncio.gather(self._asr.load(), self._diarizer.load())
            self._loaded = True
            log.info("Pipeline loaded (asr=%s, diarize_device=%s)",
                     self._asr.name(), settings.whisperx_device)

    async def transcribe(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
        language: str | None = None,
        task: str = "transcribe",
    ) -> dict[str, Any]:
        if not self._loaded:
            await self.load()
        async with self._semaphore:
            t_start = time.monotonic()
            asr_result, turns = await asyncio.gather(
                self._asr.transcribe(audio_path, language=language, task=task),
                self._diarizer.turns(audio_path, num_speakers=num_speakers),
            )
            segments_with_speakers = stitch_speakers(asr_result.segments, turns)
            # Optional Track B pass: relabel enrolled voices (e.g. Therapist /
            # Client) after stitching. Flag-gated and off by default, so the /v1
            # contract is unchanged unless explicitly enabled. Runs off-thread
            # since it loads/uses the pyannote embedding model.
            if settings.enable_role_labels:
                from app.roles import role_labeler
                segments_with_speakers = await asyncio.to_thread(
                    role_labeler.label, audio_path, segments_with_speakers
                )
            speakers = self._count_speakers(segments_with_speakers)
            elapsed = time.monotonic() - t_start
            # Report the tier that actually served (asr_result.served_by), not
            # the router chain; fall back to the configured backend's name.
            served_by = asr_result.served_by or self._asr.name()
            log.info(
                "Transcribed %.1fs of audio in %.1fs (%.2fx realtime); "
                "asr=%s language=%s speakers=%d",
                asr_result.duration_seconds,
                elapsed,
                (asr_result.duration_seconds / elapsed) if elapsed > 0 else 0.0,
                served_by,
                asr_result.language,
                speakers,
            )
            return {
                "segments": segments_with_speakers,
                "language": asr_result.language,
                "duration_seconds": asr_result.duration_seconds,
                "speakers_detected": speakers,
                "elapsed_seconds": elapsed,
                "asr_backend": served_by,
                "asr_model": asr_result.model,
                "diarize_device": getattr(self._diarizer, "last_device", settings.whisperx_device),
                "task": task,
                # For translate, the segment text is English regardless of the
                # detected source `language`.
                "output_language": "en" if task == "translate" else asr_result.language,
            }

    @staticmethod
    def _count_speakers(segments: list[dict[str, Any]]) -> int:
        speakers: set[str] = set()
        for seg in segments:
            spk = seg.get("speaker")
            if spk and spk != "SPEAKER_??":
                speakers.add(spk)
        return len(speakers)


def render_txt(result: dict[str, Any]) -> str:
    """Render pipeline result to a speaker-labeled .txt.

    One paragraph per speaker turn (consecutive same-speaker segments merged),
    '[mm:ss] SPEAKER_XX: text' prefix using the start time of the turn.
    """
    paragraphs: list[tuple[float, str, list[str]]] = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker") or "SPEAKER_??"
        if paragraphs and paragraphs[-1][1] == speaker:
            paragraphs[-1][2].append(text)
        else:
            paragraphs.append((float(seg.get("start") or 0.0), speaker, [text]))
    lines: list[str] = []
    for start, speaker, parts in paragraphs:
        mm = int(start // 60)
        ss = int(start % 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {speaker}: {' '.join(parts)}")
    return ("\n\n".join(lines) + "\n") if lines else ""


def render_json(job_id: str, result: dict[str, Any]) -> str:
    """Render pipeline result + a header to JSON string."""
    payload = {
        "id": job_id,
        "created_at": result.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "duration_seconds": result.get("duration_seconds"),
        "language": result.get("language"),
        "task": result.get("task") or "transcribe",
        "output_language": result.get("output_language") or result.get("language"),
        "speakers_detected": result.get("speakers_detected"),
        "model": result.get("asr_model") or settings.whisper_model,
        "compute_type": settings.whisperx_compute_type,
        "device": settings.whisperx_device,
        "diarization_model": settings.diarization_model,
        "diarize_device": result.get("diarize_device"),
        "asr_backend": result.get("asr_backend"),
        "segments": result.get("segments", []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)


def _json_default(obj: Any) -> Any:
    try:
        return float(obj)
    except (TypeError, ValueError):
        return str(obj)


def _build_asr_backend() -> ASRBackend:
    if settings.asr_backend == "whisperx":
        return LocalWhisperXASR()
    if settings.asr_backend == "router":
        from app.asr import ASRRouter, SpeachesASR
        hosts = [h.strip() for h in settings.asr_hosts.split(",") if h.strip()]
        if not hosts:
            log.warning("ASR_BACKEND=router but ASR_HOSTS is empty; using LocalWhisperXASR")
            return LocalWhisperXASR()
        backends: list[ASRBackend] = []
        for h in hosts:
            if h == "local-whisperx":
                backends.append(LocalWhisperXASR())
            elif h.startswith("http://") or h.startswith("https://"):
                backends.append(SpeachesASR(
                    base_url=h,
                    model_id=settings.asr_model_id,
                    healthcheck_timeout_s=settings.asr_healthcheck_timeout_s,
                ))
            else:
                log.warning("Ignoring unrecognized ASR_HOSTS entry: %r", h)
        if not backends:
            return LocalWhisperXASR()
        return ASRRouter(backends)
    raise ValueError(f"Unknown ASR_BACKEND={settings.asr_backend!r}; expected 'whisperx' or 'router'")


pipeline = TranscribePipeline(asr=_build_asr_backend())
