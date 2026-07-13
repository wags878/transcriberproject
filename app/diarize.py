from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("transcribe-svc.diarize")


class Diarizer:
    """Wraps pyannote diarization. Loads the model lazily on first use.

    Heavy imports (whisperx / pyannote / torch) are deferred so test code can
    instantiate this without pulling them in.
    """

    def __init__(self) -> None:
        self._diarizer: Any | None = None
        self._whisperx: Any | None = None
        self._loaded: bool = False
        self._load_lock = asyncio.Lock()

    def is_loaded(self) -> bool:
        return self._loaded

    async def load(self) -> None:
        async with self._load_lock:
            if self._loaded:
                return
            log.info(
                "Loading diarization model (name=%s, device=%s)",
                settings.diarization_model,
                settings.whisperx_device,
            )
            await asyncio.to_thread(self._load_blocking)
            self._loaded = True
            log.info("Diarization model loaded.")

    def _load_blocking(self) -> None:
        os.environ.setdefault("HF_HOME", str(settings.hf_home))
        import whisperx  # type: ignore

        kwargs: dict[str, Any] = {"device": settings.whisperx_device}
        if settings.hf_token:
            kwargs["use_auth_token"] = settings.hf_token
        diarizer = whisperx.DiarizationPipeline(
            model_name=settings.diarization_model,
            **kwargs,
        )
        self._whisperx = whisperx
        self._diarizer = diarizer

    async def turns(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run diarization on the given audio file. Returns a list of
        {'start': float, 'end': float, 'speaker': str} turns, sorted by start.
        """
        if not self._loaded:
            await self.load()
        return await asyncio.to_thread(self._turns_blocking, audio_path, num_speakers)

    def _turns_blocking(
        self,
        audio_path: Path,
        num_speakers: int | None,
    ) -> list[dict[str, Any]]:
        assert self._diarizer is not None and self._whisperx is not None
        audio = self._whisperx.load_audio(str(audio_path))
        kwargs: dict[str, Any] = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers
        diarize_segments = self._diarizer(audio, **kwargs)
        # whisperx returns a pandas DataFrame with columns ['start','end','speaker'].
        turns: list[dict[str, Any]] = []
        for _, row in diarize_segments.iterrows():
            turns.append({
                "start": float(row["start"]),
                "end": float(row["end"]),
                "speaker": str(row["speaker"]),
            })
        turns.sort(key=lambda t: t["start"])
        return turns


class RemoteDiarizer:
    """Offloads diarization to the GPU diarize-svc sidecar over HTTP, with a
    per-request fallback to a local in-process CPU Diarizer.

    The fallback is the whole point of resilience here: if the sidecar is
    unreachable, unhealthy, or errors mid-request (GPU gone, container down,
    OOM), we transparently diarize on CPU instead of failing the job. Same
    turns contract as Diarizer.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 600.0,
        healthcheck_timeout_s: float = 3.0,
        fallback: "Diarizer | None" = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout_s)
        self._healthcheck_timeout = healthcheck_timeout_s
        self._fallback = fallback if fallback is not None else Diarizer()
        # last device that actually served ("cuda"/"cpu"/"cpu-fallback"), for logs
        self.last_device: str = "unknown"

    def is_loaded(self) -> bool:
        return True

    async def load(self) -> None:
        # The sidecar loads its own model; the CPU fallback loads lazily on
        # first use. Nothing to do eagerly here.
        return None

    async def health(self) -> bool:
        try:
            r = await self._client.get(
                f"{self._base_url}/health", timeout=self._healthcheck_timeout
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def turns(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
    ) -> list[dict[str, Any]]:
        if await self.health():
            try:
                return await self._remote_turns(audio_path, num_speakers)
            except Exception as e:  # noqa: BLE001 — any remote failure → CPU
                log.warning(
                    "Remote diarization failed (%s); falling back to local CPU.", e
                )
        else:
            log.warning(
                "Diarize sidecar %s unhealthy; falling back to local CPU.",
                self._base_url,
            )
        self.last_device = "cpu-fallback"
        return await self._fallback.turns(audio_path, num_speakers=num_speakers)

    async def _remote_turns(
        self,
        audio_path: Path,
        num_speakers: int | None,
    ) -> list[dict[str, Any]]:
        with audio_path.open("rb") as fh:
            data = fh.read()
        files = {"audio": (audio_path.name, data, "application/octet-stream")}
        form: dict[str, str] = {}
        if num_speakers is not None:
            form["num_speakers"] = str(num_speakers)
        resp = await self._client.post(
            f"{self._base_url}/diarize", files=files, data=form
        )
        resp.raise_for_status()
        body = resp.json()
        self.last_device = str(body.get("device") or "unknown")
        turns = [
            {
                "start": float(t.get("start") or 0.0),
                "end": float(t.get("end") or 0.0),
                "speaker": str(t.get("speaker") or "SPEAKER_??"),
            }
            for t in body.get("turns", [])
        ]
        turns.sort(key=lambda t: t["start"])
        return turns


def build_diarizer() -> "Diarizer | RemoteDiarizer":
    """Pick the diarizer from config: remote GPU sidecar (with CPU fallback) or
    the local in-process CPU pyannote."""
    if settings.diarize_backend == "remote" and settings.diarize_url:
        log.info(
            "Diarization backend: remote sidecar %s (CPU fallback enabled)",
            settings.diarize_url,
        )
        return RemoteDiarizer(
            settings.diarize_url,
            healthcheck_timeout_s=settings.diarize_healthcheck_timeout_s,
            fallback=Diarizer(),
        )
    return Diarizer()


diarizer = build_diarizer()
