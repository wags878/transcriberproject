"""Scoring: WER + speaker-attribution accuracy for a synthetic clip.

Two metrics, both computed offline from ground truth (``truth.json`` produced by
ml/synth/generate.py) and a service prediction (the ``transcript.json`` the API
returns):

  - **WER** (word error rate) via jiwer, over case/punctuation-normalized text.
    Reference = truth turns concatenated in order; hypothesis = predicted
    segments concatenated in time order.

  - **Speaker-attribution accuracy** — the fraction of ground-truth speech time
    that the pipeline labels with the *right* speaker, after solving for the
    best predicted→truth label mapping. The service emits anonymous
    ``SPEAKER_00/01/...`` with no fixed correspondence to our ``A/B/C`` truth
    labels, so we first find the label permutation that maximizes overlapping
    speech time (optimal assignment), then report the resulting accuracy. This
    is the DER-complement: ``diarization_error = 1 - attribution_accuracy``.

Pure functions, no network, no service imports — unit-testable in isolation.

Honesty: a great score here reflects clean synthetic audio, not real-speech
performance. Always report the eval set's nature alongside the number.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import permutations
from pathlib import Path
from typing import Any

# Larger of (#pred labels, #truth labels) above which we fall back from exact
# brute-force assignment to a greedy one. 8! = 40320 iterations is trivial;
# real conversations here have 1–3 speakers.
_BRUTE_FORCE_MAX = 8

_NON_WORD = re.compile(r"[^\w'\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Lowercase, drop punctuation (keep intra-word apostrophes), collapse
    whitespace. Applied identically to reference and hypothesis so WER is fair."""
    s = s.lower()
    s = _NON_WORD.sub(" ", s)
    s = _WS.sub(" ", s)
    return s.strip()


def concat_truth_text(truth: dict[str, Any]) -> str:
    return " ".join(t["text"] for t in truth.get("turns", []))


def concat_pred_text(prediction: dict[str, Any]) -> str:
    segs = sorted(
        prediction.get("segments", []),
        key=lambda s: float(s.get("start") or 0.0),
    )
    return " ".join((s.get("text") or "").strip() for s in segs)


def compute_wer(reference: str, hypothesis: str) -> float:
    """WER over normalized text. Empty-reference edge cases handled explicitly
    so we never divide by zero inside jiwer."""
    import jiwer

    ref = normalize_text(reference)
    hyp = normalize_text(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return float(jiwer.wer(ref, hyp))


# --- Speaker attribution -----------------------------------------------------

def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _overlap_matrix(
    pred_segments: list[dict[str, Any]],
    truth_turns: list[dict[str, Any]],
) -> dict[tuple[str, str], float]:
    """Total overlapping speech time for each (predicted label, truth label)."""
    matrix: dict[tuple[str, str], float] = {}
    for seg in pred_segments:
        p_spk = str(seg.get("speaker") or "SPEAKER_??")
        p_start = float(seg.get("start") or 0.0)
        p_end = float(seg.get("end") or 0.0)
        if p_end <= p_start:
            continue
        for turn in truth_turns:
            ov = _overlap(p_start, p_end, float(turn["start"]), float(turn["end"]))
            if ov > 0.0:
                key = (p_spk, str(turn["speaker"]))
                matrix[key] = matrix.get(key, 0.0) + ov
    return matrix


def _best_assignment(
    matrix: dict[tuple[str, str], float],
    pred_labels: list[str],
    truth_labels: list[str],
) -> tuple[dict[str, str], float]:
    """Injective predicted→truth mapping maximizing total overlap.

    Exact (brute force over permutations) for small label sets; greedy fallback
    if either side is implausibly large.
    """
    if not pred_labels or not truth_labels:
        return {}, 0.0

    def total(pairs: list[tuple[str, str]]) -> float:
        return sum(matrix.get((p, t), 0.0) for p, t in pairs)

    if max(len(pred_labels), len(truth_labels)) <= _BRUTE_FORCE_MAX:
        best_map: dict[str, str] = {}
        best_total = -1.0
        if len(pred_labels) >= len(truth_labels):
            for perm in permutations(pred_labels, len(truth_labels)):
                pairs = list(zip(perm, truth_labels))
                tot = total(pairs)
                if tot > best_total:
                    best_total, best_map = tot, dict(pairs)
        else:
            for perm in permutations(truth_labels, len(pred_labels)):
                pairs = list(zip(pred_labels, perm))
                tot = total(pairs)
                if tot > best_total:
                    best_total, best_map = tot, dict(pairs)
        return best_map, max(best_total, 0.0)

    # Greedy fallback: repeatedly take the highest-overlap unused pair.
    mapping: dict[str, str] = {}
    used_truth: set[str] = set()
    for (p, t), _ov in sorted(matrix.items(), key=lambda kv: kv[1], reverse=True):
        if p in mapping or t in used_truth:
            continue
        mapping[p] = t
        used_truth.add(t)
    return mapping, sum(matrix.get((p, t), 0.0) for p, t in mapping.items())


def score_attribution(
    truth: dict[str, Any],
    prediction: dict[str, Any],
) -> dict[str, Any]:
    """Speaker-attribution accuracy + supporting counts."""
    truth_turns = truth.get("turns", [])
    truth_speech_time = sum(
        max(0.0, float(t["end"]) - float(t["start"])) for t in truth_turns
    )
    pred_segments = prediction.get("segments", [])

    matrix = _overlap_matrix(pred_segments, truth_turns)
    pred_labels = sorted({
        str(s.get("speaker") or "SPEAKER_??") for s in pred_segments
    } - {"SPEAKER_??"})
    truth_labels = sorted({str(t["speaker"]) for t in truth_turns})

    mapping, correct_time = _best_assignment(matrix, pred_labels, truth_labels)
    accuracy = (correct_time / truth_speech_time) if truth_speech_time > 0 else 0.0
    accuracy = max(0.0, min(1.0, accuracy))

    speakers_expected = int(truth.get("num_speakers") or len(truth_labels))
    speakers_detected = int(
        prediction.get("speakers_detected") or len(pred_labels)
    )

    return {
        "attribution_accuracy": round(accuracy, 4),
        "diarization_error": round(1.0 - accuracy, 4),
        "speakers_expected": speakers_expected,
        "speakers_detected": speakers_detected,
        "speaker_count_correct": speakers_detected == speakers_expected,
        "label_mapping": mapping,
        "truth_speech_seconds": round(truth_speech_time, 3),
        "correct_speech_seconds": round(correct_time, 3),
    }


def score(truth: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    """Full scorecard for one clip: WER + speaker attribution."""
    reference = concat_truth_text(truth)
    hypothesis = concat_pred_text(prediction)
    wer = compute_wer(reference, hypothesis)

    attribution = score_attribution(truth, prediction)
    return {
        "name": truth.get("name", "unknown"),
        "wer": round(wer, 4),
        "reference_words": len(normalize_text(reference).split()),
        "hypothesis_words": len(normalize_text(hypothesis).split()),
        "duration_seconds": truth.get("duration_seconds"),
        **attribution,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--truth", required=True, type=Path)
    ap.add_argument("--pred", required=True, type=Path,
                    help="service transcript.json")
    args = ap.parse_args(argv)

    truth = json.loads(args.truth.read_text(encoding="utf-8"))
    prediction = json.loads(args.pred.read_text(encoding="utf-8"))
    result = score(truth, prediction)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
