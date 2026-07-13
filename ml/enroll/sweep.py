"""Threshold sweep for voice enrollment (Track B, task B3).

Offline, synthetic-only: enroll voice A ("Therapist"), then embed each truth
speaker's regions in multi-speaker eval clips and measure cosine similarity to
the enrollment. Voice A should score high (genuine match, from *different*
sentences than the enrollment); voices B/C should score low. The gap between the
lowest positive and the highest negative is the usable operating window; we pick
the midpoint as the recommended ROLE_MATCH_THRESHOLD.

    python -m ml.enroll.sweep

Writes ``ml/enroll/reports/<date>-threshold-sweep.md`` and prints the picked
threshold. Uses the real pyannote embedding model, so run it in the service
image (torch/pyannote present):

    docker compose -f docker-compose.gpu.yml run --rm \
        -v ${PWD}/ml:/app/ml transcribe-svc python -m ml.enroll.sweep

Honesty: synthetic TTS voices are cleanly separable; real voices (similar
demographics, shared acoustics, cross-talk) will separate less. Treat the picked
threshold as a starting point, re-sweep on consented real enrollments.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from app.embed import PyannoteEmbedding, average_embeddings, cosine
from ml.enroll.enroll import build_enrollment
from ml.synth.generate import generate_from_script

REPO_ROOT = Path(__file__).resolve().parents[2]
ENROLL_SCRIPT = REPO_ROOT / "ml" / "enroll" / "scripts" / "enroll_therapist.json"
# Multi-speaker eval clips used as probes; A is the genuine match, B/C impostors.
PROBE_SCRIPTS = [
    REPO_ROOT / "ml" / "synth" / "scripts" / "2spk_long.json",
    REPO_ROOT / "ml" / "synth" / "scripts" / "3spk_standup.json",
]
DEFAULT_OUT_DIR = REPO_ROOT / "ml" / "synth" / "out"
DEFAULT_REPORT_DIR = REPO_ROOT / "ml" / "enroll" / "reports"
ENROLLED_SPEAKER = "A"  # the truth label that should match the enrollment


def _ensure_clip(script_path: Path, out_base: Path) -> Path:
    out_dir = out_base / script_path.stem
    if (out_dir / "audio.mp3").is_file() and (out_dir / "truth.json").is_file():
        return out_dir
    print(f"  generating {script_path.stem} ...", flush=True)
    generate_from_script(script_path, out_dir)
    return out_dir


def _speaker_embeddings(
    audio: Path, truth: dict[str, Any], embedder: PyannoteEmbedding
) -> dict[str, Any]:
    """Average embedding per truth speaker, from that speaker's turn regions."""
    from collections import defaultdict

    regions: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for turn in truth["turns"]:
        regions[turn["speaker"]].append((float(turn["start"]), float(turn["end"])))
    out: dict[str, Any] = {}
    for spk, spans in regions.items():
        vecs = [embedder.embed(audio, s, e) for s, e in spans]
        out[spk] = average_embeddings(vecs)
    return out


def run_sweep(out_base: Path, report_dir: Path, report_date: str,
              device: str, hf_token: str | None, hf_home: str | None,
              model: str) -> float:
    embedder = PyannoteEmbedding(model_name=model, device=device,
                                 hf_token=hf_token, hf_home=hf_home)

    with tempfile.TemporaryDirectory(prefix="enroll-") as tmp:
        enroll_dir = _ensure_clip(ENROLL_SCRIPT, out_base)
        enroll_path = build_enrollment(
            "Therapist", [enroll_dir / "audio.mp3"], Path(tmp),
            model_name=model, device=device, hf_token=hf_token, hf_home=hf_home,
        )
        import numpy as np
        enroll_vec = np.load(enroll_path)

        rows: list[tuple[str, str, float, bool]] = []  # clip, speaker, sim, is_positive
        for script in PROBE_SCRIPTS:
            clip_dir = _ensure_clip(script, out_base)
            truth = json.loads((clip_dir / "truth.json").read_text(encoding="utf-8"))
            spk_vecs = _speaker_embeddings(clip_dir / "audio.mp3", truth, embedder)
            for spk, vec in sorted(spk_vecs.items()):
                sim = cosine(enroll_vec, vec)
                rows.append((script.stem, spk, sim, spk == ENROLLED_SPEAKER))
                print(f"  {script.stem:16s} {spk}: cos={sim:.3f}"
                      f"{'  <- genuine' if spk == ENROLLED_SPEAKER else ''}", flush=True)

    positives = [s for _, _, s, p in rows if p]
    negatives = [s for _, _, s, p in rows if not p]
    min_pos = min(positives) if positives else 0.0
    max_neg = max(negatives) if negatives else 0.0
    # Recommended threshold: midpoint of the separating gap, clamped to [0.2, 0.8].
    recommended = round(min(0.8, max(0.2, (min_pos + max_neg) / 2)), 3)
    separated = min_pos > max_neg

    _write_report(report_dir, report_date, model, rows, min_pos, max_neg,
                  recommended, separated)
    return recommended


def _sweep_table(rows: list[tuple[str, str, float, bool]]) -> list[str]:
    thresholds = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    lines = ["| Threshold | Genuine accepted | Impostors accepted (false +) |",
             "|---:|:---:|:---:|"]
    pos = [(c, s, sim) for c, s, sim, p in rows if p]
    neg = [(c, s, sim) for c, s, sim, p in rows if not p]
    for t in thresholds:
        acc_pos = sum(1 for *_x, sim in pos if sim >= t)
        acc_neg = sum(1 for *_x, sim in neg if sim >= t)
        flag = " ✅" if acc_pos == len(pos) and acc_neg == 0 else ""
        lines.append(f"| {t:.2f} | {acc_pos}/{len(pos)} | {acc_neg}/{len(neg)} |{flag}")
    return lines


def _write_report(report_dir: Path, report_date: str, model: str,
                  rows: list[tuple[str, str, float, bool]],
                  min_pos: float, max_neg: float, recommended: float,
                  separated: bool) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append(f"# Voice-enrollment threshold sweep — {report_date}")
    lines.append("")
    lines.append("**Synthetic voices — clean and separable; real voices separate "
                 "less. Re-sweep on consented real enrollments before trusting a "
                 "threshold in production.**")
    lines.append("")
    lines.append(f"- Embedding model: `{model}`")
    lines.append("- Enrollment: voice A (`en-US-AriaNeural`) → `Therapist`, from "
                 "`ml/enroll/scripts/enroll_therapist.json` (distinct sentences).")
    lines.append("- Probes: each truth speaker's regions in the eval clips; A is "
                 "the genuine match, B/C are impostors.")
    lines.append("")
    lines.append("## Similarity to the enrolled voice")
    lines.append("")
    lines.append("| Clip | Speaker | Cosine | Genuine? |")
    lines.append("|---|:---:|---:|:---:|")
    for clip, spk, sim, pos in rows:
        lines.append(f"| {clip} | {spk} | {sim:.3f} | {'✅ yes' if pos else 'no'} |")
    lines.append("")
    lines.append("## Separation")
    lines.append("")
    lines.append(f"- Lowest genuine (A) similarity: **{min_pos:.3f}**")
    lines.append(f"- Highest impostor (B/C) similarity: **{max_neg:.3f}**")
    lines.append(f"- Cleanly separated: **{'yes' if separated else 'NO — overlap!'}**")
    lines.append(f"- **Recommended `ROLE_MATCH_THRESHOLD`: {recommended}** "
                 "(midpoint of the gap, clamped to [0.2, 0.8]).")
    lines.append("")
    lines.append("## Threshold sweep")
    lines.append("")
    lines.extend(_sweep_table(rows))
    lines.append("")
    lines.append("> A clean operating point accepts every genuine A and zero "
                 "impostors. Set `ROLE_MATCH_THRESHOLD` in `.env` (or accept the "
                 "config default) and enable with `ENABLE_ROLE_LABELS=1`.")
    lines.append("")
    path = report_dir / f"{report_date}-threshold-sweep.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {path}")


def main(argv: list[str] | None = None) -> int:
    from app.config import settings

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    ap.add_argument("--report-date", default=date.today().isoformat())
    ap.add_argument("--device", default=settings.whisperx_device)
    ap.add_argument("--model", default=settings.embedding_model)
    args = ap.parse_args(argv)

    print(f"Enrollment threshold sweep (model={args.model}, device={args.device})")
    recommended = run_sweep(
        args.out_dir, args.report_dir, args.report_date,
        args.device, settings.hf_token or None, settings.hf_home_str, args.model,
    )
    print(f"\nRecommended ROLE_MATCH_THRESHOLD = {recommended}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
