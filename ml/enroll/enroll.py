"""Build and store a voice enrollment from one or more reference clips.

    python -m ml.enroll.enroll --name Therapist \
        --clip ref1.wav --clip ref2.wav --out-dir /data/enrollments

Embeds each reference clip (whole-file) with the pyannote speaker-embedding
model, averages them into a single L2-normalized voiceprint, and writes
``<name>.npy`` plus a ``<name>.json`` sidecar (model, dimension, source clips,
timestamp).

**These vectors are biometric data.** Store them outside the repo
(``enrollments/`` is gitignored) and off shared/cloud storage. For synthetic
test voices this is moot, but the same tool is used for real voices.

Runs offline. Defaults for model/device/token/dir come from the service config
so a real enrollment and the service agree, but every value is overridable via
flags. Reuses the service image's torch/pyannote (no new deps).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.embed import PyannoteEmbedding, average_embeddings


def build_enrollment(
    name: str,
    clips: list[Path],
    out_dir: Path,
    *,
    model_name: str,
    device: str,
    hf_token: str | None,
    hf_home: str | None,
) -> Path:
    """Embed each clip, average, and persist ``<name>.npy`` + ``<name>.json``.
    Returns the path to the saved vector."""
    if not clips:
        raise ValueError("at least one reference clip is required")
    for clip in clips:
        if not clip.is_file():
            raise FileNotFoundError(clip)

    embedder = PyannoteEmbedding(
        model_name=model_name, device=device, hf_token=hf_token, hf_home=hf_home
    )
    vectors = [embedder.embed(clip) for clip in clips]
    vector = average_embeddings(vectors)

    out_dir.mkdir(parents=True, exist_ok=True)
    npy_path = out_dir / f"{name}.npy"
    json_path = out_dir / f"{name}.json"
    np.save(npy_path, vector)
    json_path.write_text(
        json.dumps(
            {
                "name": name,
                "model": model_name,
                "dim": int(vector.shape[0]),
                "num_reference_clips": len(clips),
                "reference_clips": [c.name for c in clips],
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return npy_path


def _defaults() -> dict[str, str]:
    """Pull sensible defaults from the service config when it's importable,
    else fall back to bare constants (keeps ml/ runnable standalone)."""
    try:
        from app.config import settings

        return {
            "model": settings.embedding_model,
            "device": settings.whisperx_device,
            "hf_token": settings.hf_token,
            "hf_home": settings.hf_home_str,
            "out_dir": str(settings.enrollments_dir),
        }
    except Exception:  # pragma: no cover - standalone fallback
        import os

        from app.embed import DEFAULT_EMBEDDING_MODEL

        return {
            "model": os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
            "device": os.getenv("WHISPERX_DEVICE", "cpu"),
            "hf_token": os.getenv("HF_TOKEN", ""),
            "hf_home": os.getenv("HF_HOME", ""),
            "out_dir": os.getenv("ENROLLMENTS_DIR", "enrollments"),
        }


def main(argv: list[str] | None = None) -> int:
    d = _defaults()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--name", required=True, help="enrollment label, e.g. Therapist")
    ap.add_argument("--clip", dest="clips", action="append", type=Path, required=True,
                    help="reference audio clip (repeatable)")
    ap.add_argument("--out-dir", type=Path, default=Path(d["out_dir"]))
    ap.add_argument("--model", default=d["model"])
    ap.add_argument("--device", default=d["device"])
    ap.add_argument("--hf-token", default=d["hf_token"] or None)
    ap.add_argument("--hf-home", default=d["hf_home"] or None)
    args = ap.parse_args(argv)

    path = build_enrollment(
        args.name, args.clips, args.out_dir,
        model_name=args.model, device=args.device,
        hf_token=args.hf_token, hf_home=args.hf_home,
    )
    print(f"wrote {path} (+ {path.with_suffix('.json')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
