from __future__ import annotations

from typing import Any

UNKNOWN_SPEAKER = "SPEAKER_??"


def stitch_speakers(
    segments: list[dict[str, Any]],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach a speaker label to each ASR segment based on which pyannote
    turn has the largest overlap with the segment.

    - segments: list of {'start', 'end', 'text', ...} — additional keys
      preserved verbatim on output.
    - turns: list of {'start', 'end', 'speaker'} — the pyannote output.
      Not required to be sorted.
    - Returns: a new list of segment dicts with a 'speaker' key added.
      Segments with no overlap get 'SPEAKER_??'.
    """
    out: list[dict[str, Any]] = []
    for seg in segments:
        seg_start = float(seg.get("start") or 0.0)
        seg_end = float(seg.get("end") or 0.0)
        best_speaker = UNKNOWN_SPEAKER
        best_overlap = 0.0
        for turn in turns:
            t_start = float(turn.get("start") or 0.0)
            t_end = float(turn.get("end") or 0.0)
            overlap = max(0.0, min(seg_end, t_end) - max(seg_start, t_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = str(turn.get("speaker") or UNKNOWN_SPEAKER)
        new_seg = dict(seg)
        new_seg["speaker"] = best_speaker
        out.append(new_seg)
    return out
