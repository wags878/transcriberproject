"""Baseline harness: generate synthetic clips → drive the live API → score →
write a scorecard.

    python -m ml.eval.run_baseline            # all scripts, localhost:8000
    python -m ml.eval.run_baseline --base-url http://transcribe-svc.<tailnet>.ts.net:8000

For each script under ``ml/synth/scripts/`` it:
  1. renders ``audio.mp3`` + ``truth.json`` (cached; ``--no-cache`` to force),
  2. POSTs the audio to ``/v1/transcribe`` and fetches the transcript JSON,
  3. scores WER + speaker attribution against the ground truth,
  4. appends a row to a markdown scorecard.

The scorecard lands in ``ml/eval/reports/<date>-baseline.md`` and records the
serving stack (model, backend, device) so the number is interpretable later.

Offline job: no PHI, synthetic audio only, talks to the service purely over its
public HTTP contract — nothing here imports app/.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from ml.eval.score import score
from ml.synth.generate import generate_from_script

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCRIPTS_DIR = REPO_ROOT / "ml" / "synth" / "scripts"
DEFAULT_OUT_DIR = REPO_ROOT / "ml" / "synth" / "out"
DEFAULT_REPORT_DIR = REPO_ROOT / "ml" / "eval" / "reports"

SYNTHETIC_CAVEAT = (
    "**Synthetic eval — not representative of real speech.** These clips are "
    "clean, uniform neural TTS (no PHI). They validate the pipeline and are "
    "meaningful for exact-label tasks (speaker attribution); WER here will be "
    "optimistic versus real recordings with noise, overlap, and disfluency."
)


def load_token(explicit: str | None, env_file: Path | None) -> str:
    """Resolve the API bearer token: explicit flag → API_TOKEN env → .env file."""
    if explicit:
        return explicit
    if os.getenv("API_TOKEN"):
        return os.environ["API_TOKEN"]
    env_path = env_file or (REPO_ROOT / ".env")
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("API_TOKEN=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    raise SystemExit(
        "No API token found. Set API_TOKEN in the environment, pass --token, "
        "or provide a .env with API_TOKEN=..."
    )


def ensure_clip(script_path: Path, out_base: Path, *, use_cache: bool) -> Path:
    """Return the per-script output dir, generating the clip if needed."""
    out_dir = out_base / script_path.stem
    audio = out_dir / "audio.mp3"
    truth = out_dir / "truth.json"
    if use_cache and audio.is_file() and truth.is_file():
        return out_dir
    print(f"  generating {script_path.stem} ...", flush=True)
    generate_from_script(script_path, out_dir)
    return out_dir


def transcribe(
    client: httpx.Client,
    base_url: str,
    token: str,
    audio_path: Path,
    title: str,
) -> dict[str, Any]:
    """POST the clip, then GET its transcript JSON. Returns the parsed JSON."""
    headers = {"Authorization": f"Bearer {token}"}
    with audio_path.open("rb") as fh:
        files = {"audio": (audio_path.name, fh, "audio/mpeg")}
        data = {"title": title}
        resp = client.post(
            f"{base_url}/v1/transcribe",
            headers=headers, files=files, data=data,
        )
    resp.raise_for_status()
    submitted = resp.json()
    json_url = submitted["transcript_json_url"]
    tr = client.get(f"{base_url}{json_url}", headers=headers)
    tr.raise_for_status()
    return tr.json()


def _mean(xs: list[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def build_report(
    rows: list[dict[str, Any]],
    stack: dict[str, Any],
    base_url: str,
    report_date: str,
) -> str:
    wers = [r["wer"] for r in rows]
    accs = [r["attribution_accuracy"] for r in rows]
    ref_words = [r["reference_words"] for r in rows]
    total_words = sum(ref_words) or 1
    word_weighted_wer = sum(r["wer"] * r["reference_words"] for r in rows) / total_words
    count_correct = sum(1 for r in rows if r["speaker_count_correct"])

    lines: list[str] = []
    lines.append(f"# Synthetic baseline scorecard — {report_date}")
    lines.append("")
    lines.append(SYNTHETIC_CAVEAT)
    lines.append("")
    lines.append("## Serving stack")
    lines.append("")
    lines.append(f"- Endpoint: `{base_url}`")
    for key in ("model", "asr_backend", "device", "compute_type", "diarization_model"):
        if stack.get(key):
            lines.append(f"- {key}: `{stack[key]}`")
    lines.append("")
    lines.append("## Results")
    lines.append("")
    lines.append("| Clip | Dur (s) | Spk exp/det | WER | Speaker attr. acc. |")
    lines.append("|---|---:|:---:|---:|---:|")
    for r in rows:
        spk = f"{r['speakers_expected']}/{r['speakers_detected']}"
        if not r["speaker_count_correct"]:
            spk += " ⚠"
        lines.append(
            f"| {r['name']} | {r['duration_seconds']:.1f} | {spk} | "
            f"{r['wer'] * 100:.1f}% | {r['attribution_accuracy'] * 100:.1f}% |"
        )
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- Clips scored: **{len(rows)}**")
    lines.append(f"- Mean WER (per-clip): **{_mean(wers) * 100:.1f}%**")
    lines.append(f"- Word-weighted WER: **{word_weighted_wer * 100:.1f}%**")
    lines.append(f"- Mean speaker-attribution accuracy: **{_mean(accs) * 100:.1f}%**")
    lines.append(
        f"- Speaker-count correct: **{count_correct}/{len(rows)}** clips"
    )
    lines.append("")
    lines.append("## Per-clip label mapping (predicted → truth)")
    lines.append("")
    for r in rows:
        mapping = ", ".join(f"{p}→{t}" for p, t in r["label_mapping"].items()) or "—"
        lines.append(f"- `{r['name']}`: {mapping}")
    lines.append("")
    lines.append(
        "> Regenerate with `python -m ml.eval.run_baseline`. WER is over "
        "case/punctuation-normalized text; attribution accuracy is the fraction "
        "of truth speech-time labeled with the correct speaker after optimal "
        "label assignment (`diarization_error = 1 − accuracy`)."
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default=os.getenv("BASE_URL", "http://localhost:8000"))
    ap.add_argument("--token", default=None, help="API bearer token (else env/.env)")
    ap.add_argument("--env-file", type=Path, default=None)
    ap.add_argument("--scripts-dir", type=Path, default=DEFAULT_SCRIPTS_DIR)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    ap.add_argument("--no-cache", action="store_true",
                    help="regenerate clips even if cached")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="per-request timeout seconds (CPU diarization is slow)")
    ap.add_argument("--report-date", default=date.today().isoformat(),
                    help="report filename date (default: today)")
    args = ap.parse_args(argv)

    base_url = args.base_url.rstrip("/")
    token = load_token(args.token, args.env_file)
    scripts = sorted(args.scripts_dir.glob("*.json"))
    if not scripts:
        raise SystemExit(f"no scripts found in {args.scripts_dir}")

    print(f"Baseline against {base_url} — {len(scripts)} clips")
    rows: list[dict[str, Any]] = []
    stack: dict[str, Any] = {}
    with httpx.Client(timeout=args.timeout) as client:
        for script_path in scripts:
            out_dir = ensure_clip(script_path, args.out_dir, use_cache=not args.no_cache)
            truth = json.loads((out_dir / "truth.json").read_text(encoding="utf-8"))
            print(f"  transcribing {script_path.stem} ...", flush=True)
            prediction = transcribe(
                client, base_url, token, out_dir / "audio.mp3", script_path.stem
            )
            if not stack:
                stack = {k: prediction.get(k) for k in
                         ("model", "asr_backend", "device", "compute_type",
                          "diarization_model")}
            row = score(truth, prediction)
            rows.append(row)
            print(
                f"    WER {row['wer'] * 100:.1f}%  "
                f"attr {row['attribution_accuracy'] * 100:.1f}%  "
                f"spk {row['speakers_detected']}/{row['speakers_expected']}",
                flush=True,
            )

    report = build_report(rows, stack, base_url, args.report_date)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"{args.report_date}-baseline.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nWrote {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
