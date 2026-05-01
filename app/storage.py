from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.config import settings


def ensure_dirs() -> None:
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)
    settings.models_dir.mkdir(parents=True, exist_ok=True)


def new_job_id() -> str:
    return str(uuid.uuid4())


async def save_upload(job_id: str, upload: UploadFile) -> Path:
    ensure_dirs()
    suffix = Path(upload.filename or "audio").suffix or ".bin"
    target = settings.uploads_dir / f"{job_id}{suffix}"
    with target.open("wb") as fh:
        while chunk := await upload.read(1024 * 1024):
            fh.write(chunk)
    return target


def transcript_paths(job_id: str) -> tuple[Path, Path]:
    return (
        settings.outputs_dir / f"{job_id}.txt",
        settings.outputs_dir / f"{job_id}.json",
    )


def dir_size_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
    return round(total / (1024 * 1024), 2)


def free_space_mb(path: Path) -> float:
    if not path.exists():
        return 0.0
    return round(shutil.disk_usage(path).free / (1024 * 1024), 2)
