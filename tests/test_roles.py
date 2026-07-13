"""Track B role-labeling tests — torch-free.

The pyannote embedding model never loads here: matching / inference / relabeling
are pure functions over numpy vectors, and the end-to-end ``apply_role_labels``
path is exercised with a FakeEmbedder. This keeps the suite runnable in the
no-torch venv (see docs/DEPLOY.md) while still validating the real logic that
runs when ENABLE_ROLE_LABELS is on.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app import roles
from app.embed import Embedder, cosine

# Orthogonal "voiceprints": distinct speakers are perfectly dissimilar.
THERAPIST = np.array([1.0, 0.0, 0.0], dtype=np.float32)
CLIENT = np.array([0.0, 1.0, 0.0], dtype=np.float32)
THIRD = np.array([0.0, 0.0, 1.0], dtype=np.float32)


class FakeEmbedder:
    """Returns a fixed vector per segment, keyed by segment start time."""

    def __init__(self, by_start: dict[float, np.ndarray]) -> None:
        self.by_start = by_start
        self.calls = 0

    def embed(self, audio_path: Path, start: float | None = None,
              end: float | None = None) -> np.ndarray:
        self.calls += 1
        if start is None:
            return next(iter(self.by_start.values()))
        for s, vec in self.by_start.items():
            if abs(float(start) - s) < 1e-6:
                return vec
        raise KeyError(start)


def test_fake_embedder_satisfies_protocol():
    assert isinstance(FakeEmbedder({}), Embedder)


# --- pure matching -----------------------------------------------------------

def test_match_clusters_basic():
    mapping = roles.match_clusters(
        {"SPEAKER_00": THERAPIST, "SPEAKER_01": CLIENT},
        {"Therapist": THERAPIST},
        threshold=0.5,
    )
    assert mapping == {"SPEAKER_00": "Therapist"}


def test_match_clusters_below_threshold_matches_nothing():
    # CLIENT is orthogonal to the enrollment → cosine 0 < 0.5.
    mapping = roles.match_clusters(
        {"SPEAKER_01": CLIENT}, {"Therapist": THERAPIST}, threshold=0.5
    )
    assert mapping == {}


def test_match_clusters_one_to_one_no_double_assignment():
    # Two clusters both resemble the enrollment; only the closest wins.
    near = np.array([0.95, 0.31, 0.0], dtype=np.float32)  # slightly off THERAPIST
    mapping = roles.match_clusters(
        {"SPEAKER_00": THERAPIST, "SPEAKER_02": near},
        {"Therapist": THERAPIST},
        threshold=0.5,
    )
    assert mapping == {"SPEAKER_00": "Therapist"}
    assert cosine(THERAPIST, near) > 0.5  # sanity: it *would* have matched alone


# --- client inference --------------------------------------------------------

def test_infer_client_two_speaker():
    out = roles.infer_client_label(
        ["SPEAKER_00", "SPEAKER_01"], {"SPEAKER_00": "Therapist"}, "Client"
    )
    assert out == {"SPEAKER_00": "Therapist", "SPEAKER_01": "Client"}


def test_infer_client_three_speaker_is_noop():
    mapping = {"SPEAKER_00": "Therapist"}
    out = roles.infer_client_label(
        ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"], mapping, "Client"
    )
    assert out == mapping


def test_infer_client_no_match_is_noop():
    out = roles.infer_client_label(["SPEAKER_00", "SPEAKER_01"], {}, "Client")
    assert out == {}


# --- relabel -----------------------------------------------------------------

def test_relabel_segments_replaces_only_mapped():
    segs = [
        {"start": 0.0, "end": 1.0, "text": "a", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "b", "speaker": "SPEAKER_01"},
    ]
    out = roles.relabel_segments(segs, {"SPEAKER_00": "Therapist"})
    assert [s["speaker"] for s in out] == ["Therapist", "SPEAKER_01"]
    # original untouched (new dicts returned)
    assert segs[0]["speaker"] == "SPEAKER_00"


# --- end-to-end via FakeEmbedder --------------------------------------------

def _two_speaker_segments():
    return [
        {"start": 0.0, "end": 3.0, "text": "hello", "speaker": "SPEAKER_00"},
        {"start": 3.0, "end": 6.0, "text": "hi", "speaker": "SPEAKER_01"},
        {"start": 6.0, "end": 9.0, "text": "more", "speaker": "SPEAKER_00"},
    ]


def test_apply_role_labels_therapist_and_inferred_client():
    segs = _two_speaker_segments()
    embedder = FakeEmbedder({0.0: THERAPIST, 6.0: THERAPIST, 3.0: CLIENT})
    out = roles.apply_role_labels(
        Path("x.wav"), segs,
        embedder=embedder,
        enrollments={"Therapist": THERAPIST},
        threshold=0.5,
    )
    labels = {s["speaker"] for s in out}
    assert labels == {"Therapist", "Client"}
    # SPEAKER_00 (both therapist segments) → Therapist; SPEAKER_01 → Client
    assert out[0]["speaker"] == "Therapist"
    assert out[1]["speaker"] == "Client"
    assert out[2]["speaker"] == "Therapist"


def test_apply_role_labels_no_enrollments_is_noop():
    segs = _two_speaker_segments()
    embedder = FakeEmbedder({0.0: THERAPIST, 3.0: CLIENT, 6.0: THERAPIST})
    out = roles.apply_role_labels(
        Path("x.wav"), segs, embedder=embedder, enrollments={}, threshold=0.5
    )
    assert out == segs
    assert embedder.calls == 0  # short-circuits before embedding


def test_apply_role_labels_three_speakers_no_client_inference():
    segs = [
        {"start": 0.0, "end": 3.0, "text": "a", "speaker": "SPEAKER_00"},
        {"start": 3.0, "end": 6.0, "text": "b", "speaker": "SPEAKER_01"},
        {"start": 6.0, "end": 9.0, "text": "c", "speaker": "SPEAKER_02"},
    ]
    embedder = FakeEmbedder({0.0: THERAPIST, 3.0: CLIENT, 6.0: THIRD})
    out = roles.apply_role_labels(
        Path("x.wav"), segs,
        embedder=embedder, enrollments={"Therapist": THERAPIST}, threshold=0.5,
    )
    # Only the enrolled voice is relabeled; others stay anonymous.
    assert out[0]["speaker"] == "Therapist"
    assert out[1]["speaker"] == "SPEAKER_01"
    assert out[2]["speaker"] == "SPEAKER_02"


def test_apply_role_labels_no_cluster_clears_threshold_is_noop():
    segs = _two_speaker_segments()
    embedder = FakeEmbedder({0.0: CLIENT, 6.0: CLIENT, 3.0: THIRD})
    out = roles.apply_role_labels(
        Path("x.wav"), segs,
        embedder=embedder, enrollments={"Therapist": THERAPIST}, threshold=0.5,
    )
    assert [s["speaker"] for s in out] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]


# --- enrollment IO -----------------------------------------------------------

def test_load_enrollments_roundtrip(tmp_path):
    np.save(tmp_path / "Therapist.npy", THERAPIST)
    np.save(tmp_path / "Alex.npy", CLIENT)
    loaded = roles.load_enrollments(tmp_path)
    assert set(loaded) == {"Therapist", "Alex"}
    assert np.allclose(loaded["Therapist"], THERAPIST)


def test_load_enrollments_missing_dir_returns_empty(tmp_path):
    assert roles.load_enrollments(tmp_path / "nope") == {}
