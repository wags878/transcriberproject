"""GPU diarization sidecar.

A tiny FastAPI service that runs pyannote speaker diarization on CUDA and returns
turns over HTTP, so the main transcribe-svc can offload the pipeline's dominant
CPU cost without touching its frozen torch/pyannote pins (see docs/BLOCKERS.md
B-003). Mirrors the Speaches ASR split: separate container, modern CUDA stack,
called over localhost within the shared tailscale netns.

Contract:
  GET  /health          -> {"status","device","model","loaded"}
  POST /diarize         -> {"turns":[{"start","end","speaker"}], "device"}
      multipart: audio (file, required), num_speakers (int, optional)

Turns match what app/stitch.py expects: [{start, end, speaker}], sorted by start,
speaker labels like "SPEAKER_00".
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("diarize-svc")

MODEL = os.getenv("DIARIZATION_MODEL", "pyannote/speaker-diarization-3.1")
HF_TOKEN = os.getenv("HF_TOKEN", "") or None

_state: dict[str, Any] = {"pipeline": None, "device": "cpu"}


def _load_pipeline() -> None:
    import torch
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(MODEL, use_auth_token=HF_TOKEN)
    if pipeline is None:
        raise RuntimeError(
            f"Pipeline.from_pretrained({MODEL!r}) returned None — check HF_TOKEN "
            "and that the model's user conditions are accepted."
        )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipeline.to(torch.device(device))
    _state["pipeline"] = pipeline
    _state["device"] = device
    log.info("Loaded diarization pipeline %s on %s", MODEL, device)
    if device != "cuda":
        log.warning("CUDA not available — diarization sidecar is running on CPU!")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Preload at startup so the first request isn't a cold model load, and so an
    # unhealthy GPU/token surfaces immediately rather than on first use.
    _load_pipeline()
    yield


app = FastAPI(title="diarize-svc", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "device": _state["device"],
        "model": MODEL,
        "loaded": _state["pipeline"] is not None,
    }


def _to_wav(src: Path, dst: Path) -> None:
    """Decode any ffmpeg-readable input to 16 kHz mono wav for pyannote."""
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src),
         "-ac", "1", "-ar", "16000", str(dst)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {proc.stderr.strip()}")


@app.post("/diarize")
async def diarize(
    audio: UploadFile = File(...),
    num_speakers: int | None = Form(default=None),
) -> dict[str, Any]:
    pipeline = _state["pipeline"]
    if pipeline is None:
        raise HTTPException(status_code=503, detail="pipeline not loaded")

    with tempfile.TemporaryDirectory(prefix="diar-") as tmp:
        tmp_dir = Path(tmp)
        raw = tmp_dir / (audio.filename or "audio.bin")
        with raw.open("wb") as fh:
            while chunk := await audio.read(1024 * 1024):
                fh.write(chunk)
        wav = tmp_dir / "audio.wav"
        try:
            _to_wav(raw, wav)
        except RuntimeError as e:
            raise HTTPException(status_code=415, detail=str(e)) from e

        kwargs: dict[str, Any] = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers
        diarization = pipeline(str(wav), **kwargs)

    turns = [
        {"start": float(seg.start), "end": float(seg.end), "speaker": str(label)}
        for seg, _, label in diarization.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda t: t["start"])
    return {"turns": turns, "device": _state["device"]}
