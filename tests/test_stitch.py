from __future__ import annotations

from app.stitch import stitch_speakers


def test_single_turn_covers_single_segment() -> None:
    segments = [{"start": 0.0, "end": 2.0, "text": "hello"}]
    turns = [{"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"}]
    out = stitch_speakers(segments, turns)
    assert out[0]["speaker"] == "SPEAKER_00"


def test_two_turns_split_two_segments() -> None:
    segments = [
        {"start": 0.0, "end": 2.0, "text": "hi"},
        {"start": 3.0, "end": 5.0, "text": "how are you"},
    ]
    turns = [
        {"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"},
        {"start": 2.5, "end": 6.0, "speaker": "SPEAKER_01"},
    ]
    out = stitch_speakers(segments, turns)
    assert out[0]["speaker"] == "SPEAKER_00"
    assert out[1]["speaker"] == "SPEAKER_01"


def test_segment_overlaps_two_turns_picks_dominant() -> None:
    # Segment 0.0-4.0. Turn A (SPEAKER_00) 0.0-1.0 (1s overlap).
    # Turn B (SPEAKER_01) 1.0-4.0 (3s overlap). Dominant = SPEAKER_01.
    segments = [{"start": 0.0, "end": 4.0, "text": "long segment"}]
    turns = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 4.0, "speaker": "SPEAKER_01"},
    ]
    out = stitch_speakers(segments, turns)
    assert out[0]["speaker"] == "SPEAKER_01"


def test_segment_before_all_turns_falls_back_to_unknown() -> None:
    segments = [{"start": 0.0, "end": 1.0, "text": "pre-audio glitch"}]
    turns = [{"start": 5.0, "end": 10.0, "speaker": "SPEAKER_00"}]
    out = stitch_speakers(segments, turns)
    assert out[0]["speaker"] == "SPEAKER_??"


def test_empty_turns_all_unknown() -> None:
    segments = [{"start": 0.0, "end": 2.0, "text": "hello"}]
    out = stitch_speakers(segments, [])
    assert out[0]["speaker"] == "SPEAKER_??"


def test_empty_segments_returns_empty() -> None:
    assert stitch_speakers([], [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]) == []


def test_segments_preserve_extra_fields() -> None:
    segments = [{"start": 0.0, "end": 2.0, "text": "hi", "words": [{"w": "hi"}]}]
    turns = [{"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"}]
    out = stitch_speakers(segments, turns)
    assert out[0]["words"] == [{"w": "hi"}]
    assert out[0]["text"] == "hi"
