"""Manual speaker relabeling of a completed transcript.

The service's automatic labels (anonymous `SPEAKER_00/01`, or Track B's enrolled
`Therapist`/`Client`) are best-effort. This provides the always-available manual
override: the client sends the final speaker label for every segment, and we
persist it back to the stored `.txt` / `.json`.

Pure function (`apply_speaker_labels`) so it unit-tests without the app; the
endpoint in `app/main.py` handles IO.
"""
from __future__ import annotations

from typing import Any

from app.stitch import UNKNOWN_SPEAKER


def count_speakers(segments: list[dict[str, Any]]) -> int:
    labels = {
        s.get("speaker") for s in segments
        if s.get("speaker") and s.get("speaker") != UNKNOWN_SPEAKER
    }
    return len(labels)


def apply_speaker_labels(
    doc: dict[str, Any], speakers: list[str]
) -> dict[str, Any]:
    """Return a copy of ``doc`` with each segment's ``speaker`` replaced by the
    corresponding entry in ``speakers`` (one per segment, same order).

    Raises ValueError if the count doesn't match the number of segments — the
    client must send exactly one label per segment so there is no ambiguity
    about which turn each label belongs to.
    """
    segments = doc.get("segments", [])
    if len(speakers) != len(segments):
        raise ValueError(
            f"expected {len(segments)} speaker labels, got {len(speakers)}"
        )
    new_segments = []
    for seg, label in zip(segments, speakers):
        new_seg = dict(seg)
        new_seg["speaker"] = (label or UNKNOWN_SPEAKER).strip() or UNKNOWN_SPEAKER
        new_segments.append(new_seg)
    updated = {**doc, "segments": new_segments}
    updated["speakers_detected"] = count_speakers(new_segments)
    return updated
