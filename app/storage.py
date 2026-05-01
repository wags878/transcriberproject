from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime
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


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(title: str | None, fallback: str = "untitled", maxlen: int = 40) -> str:
    if not title:
        return fallback
    s = _SLUG_RE.sub("-", title.lower()).strip("-")
    return (s[:maxlen] or fallback)


def build_stem(created_at: datetime, title: str | None, job_id: str) -> str:
    slug = slugify(title)
    short = job_id.replace("-", "")[:8]
    return f"{created_at:%Y-%m-%d}_{created_at:%H%M}_{slug}_{short}"


def _index_dir() -> Path:
    return settings.outputs_dir / ".index"


def write_stem_index(job_id: str, stem: str) -> None:
    idx_dir = _index_dir()
    idx_dir.mkdir(parents=True, exist_ok=True)
    (idx_dir / job_id).write_text(stem)


def resolve_stem(job_id: str) -> str | None:
    p = _index_dir() / job_id
    return p.read_text().strip() if p.is_file() else None


def transcript_paths(job_id: str) -> tuple[Path, Path] | None:
    """Resolve a job_id to its (txt_path, json_path).

    Tries the stem index first (Phase 2 filenames). Falls back to legacy
    {job_id}.txt / {job_id}.json (Phase 1 filenames) so old transcripts on
    disk remain fetchable. Returns None when neither exists.
    """
    stem = resolve_stem(job_id)
    if stem:
        return (
            settings.outputs_dir / f"{stem}.txt",
            settings.outputs_dir / f"{stem}.json",
        )
    legacy_txt = settings.outputs_dir / f"{job_id}.txt"
    legacy_json = settings.outputs_dir / f"{job_id}.json"
    if legacy_txt.exists() or legacy_json.exists():
        return (legacy_txt, legacy_json)
    return None


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
