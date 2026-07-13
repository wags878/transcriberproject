"""Manual speaker relabel — pure logic + endpoint. Torch-free."""
from __future__ import annotations

import pytest

from app.relabel import apply_speaker_labels, count_speakers


def _doc():
    return {
        "id": "job1",
        "language": "en",
        "duration_seconds": 4.5,
        "speakers_detected": 2,
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "Hello there.", "speaker": "SPEAKER_00"},
            {"start": 1.5, "end": 3.0, "text": "How are you?", "speaker": "SPEAKER_00"},
            {"start": 3.0, "end": 4.5, "text": "I am well.", "speaker": "SPEAKER_01"},
        ],
    }


# --- pure logic --------------------------------------------------------------

def test_apply_speaker_labels_rename_and_recount():
    out = apply_speaker_labels(_doc(), ["Therapist", "Therapist", "Client"])
    assert [s["speaker"] for s in out["segments"]] == ["Therapist", "Therapist", "Client"]
    assert out["speakers_detected"] == 2


def test_apply_speaker_labels_merge_reduces_count():
    out = apply_speaker_labels(_doc(), ["Therapist", "Therapist", "Therapist"])
    assert out["speakers_detected"] == 1


def test_apply_speaker_labels_blank_becomes_unknown():
    out = apply_speaker_labels(_doc(), ["Therapist", "", "  "])
    assert out["segments"][1]["speaker"] == "SPEAKER_??"
    assert out["speakers_detected"] == 1  # only Therapist counts


def test_apply_speaker_labels_length_mismatch_raises():
    with pytest.raises(ValueError):
        apply_speaker_labels(_doc(), ["Therapist"])


def test_apply_speaker_labels_does_not_mutate_input():
    doc = _doc()
    apply_speaker_labels(doc, ["A", "B", "C"])
    assert doc["segments"][0]["speaker"] == "SPEAKER_00"


def test_count_speakers_ignores_unknown():
    segs = [{"speaker": "A"}, {"speaker": "SPEAKER_??"}, {"speaker": "A"}]
    assert count_speakers(segs) == 1


# --- endpoint ----------------------------------------------------------------

def _make_job(client, auth_headers, fake_audio):
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("a.wav", fake_audio, "audio/wav")},
        data={"title": "relabel-test"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_relabel_endpoint_persists(client, auth_headers, fake_audio):
    job_id = _make_job(client, auth_headers, fake_audio)
    r = client.post(
        f"/v1/results/{job_id}/relabel",
        headers=auth_headers,
        json={"speakers": ["Therapist", "Therapist", "Client"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["speakers_detected"] == 2

    # Persisted: the stored .json reflects the edit.
    jr = client.get(f"/v1/results/{job_id}/transcript.json", headers=auth_headers)
    labels = [s["speaker"] for s in jr.json()["segments"]]
    assert labels == ["Therapist", "Therapist", "Client"]

    # ...and the .txt was re-rendered with the new label.
    tr = client.get(f"/v1/results/{job_id}/transcript.txt", headers=auth_headers)
    assert "Therapist:" in tr.text and "Client:" in tr.text


def test_relabel_endpoint_wrong_count_is_400(client, auth_headers, fake_audio):
    job_id = _make_job(client, auth_headers, fake_audio)
    r = client.post(
        f"/v1/results/{job_id}/relabel",
        headers=auth_headers,
        json={"speakers": ["OnlyOne"]},
    )
    assert r.status_code == 400


def test_relabel_endpoint_missing_job_is_404(client, auth_headers):
    r = client.post(
        "/v1/results/does-not-exist/relabel",
        headers=auth_headers,
        json={"speakers": []},
    )
    assert r.status_code == 404


def test_relabel_endpoint_requires_auth(client, fake_audio):
    r = client.post("/v1/results/x/relabel", json={"speakers": []})
    assert r.status_code == 401
