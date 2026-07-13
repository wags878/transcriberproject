from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

import json

from app import storage
from app.auth import bearer_auth
from app.config import settings
from app.pipeline import pipeline, render_json, render_txt
from app.relabel import apply_speaker_labels
from app.schemas import (
    HealthResponse,
    RelabelRequest,
    StorageResponse,
    TranscribeResponse,
)

log = logging.getLogger("transcribe-svc")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    storage.ensure_dirs()
    removed = storage.cleanup_old_files(settings.retain_days)
    log.info(
        "Retention: removed %d uploads, %d outputs (RETAIN_DAYS=%d)",
        removed["uploads"], removed["outputs"], settings.retain_days,
    )
    log.info("Starting transcribe-svc (model=%s, device=%s, compute_type=%s, max_concurrent=%d)",
             settings.whisper_model,
             settings.whisperx_device,
             settings.whisperx_compute_type,
             settings.max_concurrent_jobs)
    # Models are loaded lazily on first request to keep startup fast and let
    # the health check go ready quickly. Set EAGER_LOAD=1 in env to preload.
    import os
    if os.getenv("EAGER_LOAD") == "1":
        await pipeline.load()
    yield


app = FastAPI(
    title="transcribe-svc",
    version="0.1.0",
    description="Self-hosted diarized transcription service. See docs/API.md.",
    lifespan=lifespan,
    docs_url=None,        # internal-only service; suppress public OpenAPI UI
    redoc_url=None,
    openapi_url=None,
)


@app.get("/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        device=settings.whisperx_device,
        compute_type=settings.whisperx_compute_type,
        gpu=settings.whisperx_device == "cuda",
    )


@app.post(
    "/v1/transcribe",
    response_model=TranscribeResponse,
    dependencies=[Depends(bearer_auth)],
)
async def transcribe(
    audio: UploadFile = File(...),
    title: str | None = Form(default=None),
    num_speakers: int | None = Form(default=None),
    language: str | None = Form(default=None),
    task: str = Form(default="transcribe"),
) -> TranscribeResponse:
    if not audio.filename:
        raise HTTPException(status_code=400, detail="audio file required")
    if task not in ("transcribe", "translate"):
        raise HTTPException(
            status_code=400,
            detail="task must be 'transcribe' or 'translate'",
        )

    job_id = storage.new_job_id()
    created_at = datetime.now(timezone.utc)
    stem = storage.build_stem(created_at, title, job_id)

    upload_path: Path = await storage.save_upload(job_id, audio)

    size_mb = upload_path.stat().st_size / (1024 * 1024)
    if size_mb > settings.max_upload_mb:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"upload exceeds {settings.max_upload_mb} MB",
        )

    try:
        result = await pipeline.transcribe(
            upload_path,
            num_speakers=num_speakers,
            language=language or settings.language_or_none,
            task=task,
        )
    except Exception as e:
        log.exception("Transcription failed for job %s", job_id)
        raise HTTPException(status_code=500, detail=f"transcription failed: {e}") from e

    result["created_at"] = created_at.isoformat()
    # The requested task is authoritative for what the transcript text is, so
    # stamp it (and the resulting output language) onto the result before render.
    result["task"] = task
    result["output_language"] = "en" if task == "translate" else (result.get("language") or "")
    txt_path = settings.outputs_dir / f"{stem}.txt"
    json_path = settings.outputs_dir / f"{stem}.json"
    txt_path.write_text(render_txt(result), encoding="utf-8")
    json_path.write_text(render_json(job_id, result), encoding="utf-8")
    storage.write_stem_index(job_id, stem)

    log.info("Job %s title=%r stem=%s", job_id, title, stem)

    return TranscribeResponse(
        id=job_id,
        transcript_txt_url=f"/v1/results/{job_id}/transcript.txt",
        transcript_json_url=f"/v1/results/{job_id}/transcript.json",
        speakers_detected=int(result.get("speakers_detected") or 0),
        duration_seconds=float(result.get("duration_seconds") or 0.0),
        language=str(result.get("language") or ""),
        task=str(result.get("task") or "transcribe"),
        output_language=str(result.get("output_language") or result.get("language") or ""),
    )


@app.get(
    "/v1/results/{job_id}/transcript.txt",
    dependencies=[Depends(bearer_auth)],
)
async def get_transcript_txt(job_id: str) -> FileResponse:
    paths = storage.transcript_paths(job_id)
    if paths is None or not paths[0].exists():
        raise HTTPException(status_code=404, detail="transcript not found")
    return FileResponse(paths[0], media_type="text/plain; charset=utf-8")


@app.get(
    "/v1/results/{job_id}/transcript.json",
    dependencies=[Depends(bearer_auth)],
)
async def get_transcript_json(job_id: str) -> FileResponse:
    paths = storage.transcript_paths(job_id)
    if paths is None or not paths[1].exists():
        raise HTTPException(status_code=404, detail="transcript not found")
    return FileResponse(paths[1], media_type="application/json")


@app.post(
    "/v1/results/{job_id}/relabel",
    dependencies=[Depends(bearer_auth)],
)
async def relabel_speakers(job_id: str, req: RelabelRequest) -> JSONResponse:
    """Manually overwrite speaker labels for a completed transcript.

    The client sends one final speaker label per segment (in order); we persist
    it back to the stored .txt and .json so downloads and history reflect the
    edit. The always-available manual override for imperfect auto-labeling.
    """
    paths = storage.transcript_paths(job_id)
    if paths is None or not paths[1].exists():
        raise HTTPException(status_code=404, detail="transcript not found")
    txt_path, json_path = paths
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    try:
        updated = apply_speaker_labels(doc, req.speakers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    json_path.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    txt_path.write_text(render_txt(updated), encoding="utf-8")
    log.info("Job %s relabeled: %d speakers", job_id, updated["speakers_detected"])
    return JSONResponse(content=updated)


@app.get(
    "/v1/admin/storage",
    response_model=StorageResponse,
    dependencies=[Depends(bearer_auth)],
)
async def admin_storage() -> StorageResponse:
    return StorageResponse(
        uploads_mb=storage.dir_size_mb(settings.uploads_dir),
        outputs_mb=storage.dir_size_mb(settings.outputs_dir),
        models_mb=storage.dir_size_mb(settings.models_dir),
    )


@app.exception_handler(HTTPException)
async def _http_exc(_, exc: HTTPException) -> JSONResponse:  # type: ignore[override]
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail},
        headers=exc.headers or {},
    )


# --- Static PWA client -------------------------------------------------------
# Served same-origin (no CORS) so it works via both the published localhost port
# and the tailnet URL. Mounted LAST so every /v1 API route above takes
# precedence; anything else (/, /icon.svg, /manifest.webmanifest, /sw.js) is
# served from app/static. The client holds the bearer token in localStorage and
# sends it on each request — the API itself stays auth-gated.
import mimetypes

from fastapi.staticfiles import StaticFiles

mimetypes.add_type("application/manifest+json", ".webmanifest")

# Synthetic demo clips (read-only) for the client's "try a sample" buttons.
# Mounted before the catch-all so /samples/* resolves here.
_samples_dir = Path(__file__).parent.parent / "samples"
if _samples_dir.is_dir():
    app.mount("/samples", StaticFiles(directory=str(_samples_dir)), name="samples")

_static_dir = Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="pwa")
