from __future__ import annotations

import re
import shutil
import time
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


def cleanup_old_files(retain_days: int) -> dict[str, int]:
    """Delete files in uploads/ and outputs/ older than retain_days.

    retain_days < 0  -> cleanup is disabled, returns zero counts.
    retain_days == 0 -> delete anything older than the moment of this call
                        (spec acceptance test relies on this).
    models_dir is never swept.
    """
    counts = {"uploads": 0, "outputs": 0}
    if retain_days < 0:
        return counts
    cutoff = time.time() - retain_days * 86400
    idx_dir_path = _index_dir().resolve() if _index_dir().exists() else None
    for label, root in (("uploads", settings.uploads_dir),
                        ("outputs", settings.outputs_dir)):
        if not root.exists():
            continue
        root_resolved = root.resolve()
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            p_resolved = p.resolve()
            try:
                p_resolved.relative_to(root_resolved)
            except ValueError:
                continue
            # Skip the stem-index directory; the explicit sweep below
            # handles dangling index entries.
            if idx_dir_path is not None:
                try:
                    p_resolved.relative_to(idx_dir_path)
                    continue
                except ValueError:
                    pass
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                counts[label] += 1
    idx_dir = _index_dir()
    if idx_dir.is_dir():
        for entry in idx_dir.iterdir():
            if not entry.is_file():
                continue
            stem = entry.read_text().strip()
            txt = settings.outputs_dir / f"{stem}.txt"
            js = settings.outputs_dir / f"{stem}.json"
            if not txt.exists() and not js.exists():
                entry.unlink(missing_ok=True)
    return counts


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
