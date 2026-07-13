"""End-to-end acceptance check for Track B (task B3/B4 acceptance).

Proves the *real* code path that runs in the service when ENABLE_ROLE_LABELS is
on: enroll voice A as 'Therapist' → run real pyannote diarization on a 2-speaker
synthetic clip → apply `app.roles.apply_role_labels` with the real embedding
model → confirm the enrolled voice's turns are labeled 'Therapist' and the other
speaker 'Client', and that 'Therapist' actually lands on truth speaker A (not B).

    docker compose -f docker-compose.gpu.yml run --rm \
        -v ${PWD}/ml:/app/ml -v ${PWD}/app:/app/app \
        transcribe-svc python -m ml.enroll.verify_e2e

Synthetic only, no PHI. Exit code 0 = PASS. This exercises the same functions the
pipeline calls; it does not need the HTTP server (avoids a port clash with the
running stack).
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from app.config import settings
from app.diarize import Diarizer
from app.embed import PyannoteEmbedding
from app.roles import apply_role_labels, load_enrollments
from ml.enroll.enroll import build_enrollment

REPO_ROOT = Path(__file__).resolve().parents[2]
ENROLL_CLIP = REPO_ROOT / "ml" / "synth" / "out" / "enroll_therapist" / "audio.mp3"
TEST_DIR = REPO_ROOT / "ml" / "synth" / "out" / "2spk_long"  # A + B, A is therapist
TRUTH_SPEAKER = "A"


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def main() -> int:
    truth = json.loads((TEST_DIR / "truth.json").read_text(encoding="utf-8"))
    audio = TEST_DIR / "audio.mp3"

    with tempfile.TemporaryDirectory(prefix="verify-") as tmp:
        enroll_dir = Path(tmp)
        build_enrollment(
            "Therapist", [ENROLL_CLIP], enroll_dir,
            model_name=settings.embedding_model, device=settings.whisperx_device,
            hf_token=settings.hf_token or None, hf_home=settings.hf_home_str,
        )
        enrollments = load_enrollments(enroll_dir)

        # Real diarization → treat each turn as a segment (roles only needs
        # start/end/speaker). This is exactly what the pipeline feeds in.
        diarizer = Diarizer()
        turns = asyncio.run(diarizer.turns(audio))
        segments = [dict(t) for t in turns]

        embedder = PyannoteEmbedding(
            model_name=settings.embedding_model, device=settings.whisperx_device,
            hf_token=settings.hf_token or None, hf_home=settings.hf_home_str,
        )
        labeled = apply_role_labels(
            audio, segments, embedder=embedder, enrollments=enrollments,
            threshold=settings.role_match_threshold, client_label=settings.client_label,
        )

    labels = {s["speaker"] for s in labeled}
    print(f"Distinct labels after role pass: {sorted(labels)}")

    # Which truth speaker does 'Therapist'-labeled time overlap most?
    truth_regions: dict[str, list[tuple[float, float]]] = {}
    for turn in truth["turns"]:
        truth_regions.setdefault(turn["speaker"], []).append(
            (float(turn["start"]), float(turn["end"]))
        )
    ther_vs_truth: dict[str, float] = {spk: 0.0 for spk in truth_regions}
    for seg in labeled:
        if seg["speaker"] != "Therapist":
            continue
        for spk, spans in truth_regions.items():
            for b0, b1 in spans:
                ther_vs_truth[spk] += _overlap(
                    float(seg["start"]), float(seg["end"]), b0, b1
                )
    print(f"Therapist-labeled time overlap by truth speaker: "
          f"{ {k: round(v, 1) for k, v in ther_vs_truth.items()} }")

    best_truth = max(ther_vs_truth, key=ther_vs_truth.get) if ther_vs_truth else None
    checks = {
        "Therapist label present": "Therapist" in labels,
        "Client label present (2-spk inference)": settings.client_label in labels,
        "Therapist == truth speaker A": best_truth == TRUTH_SPEAKER,
        "no anonymous SPEAKER_* remain": not any(
            str(l).startswith("SPEAKER_") for l in labels
        ),
    }
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    passed = all(checks.values())
    print("\nRESULT:", "PASS ✅" if passed else "FAIL ❌")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
