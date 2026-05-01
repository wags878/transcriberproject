from __future__ import annotations

import re
import uuid
from pathlib import Path

from app import storage


# ----- .txt paragraph-merge -----

def test_txt_paragraph_merge(client, auth_headers, fake_audio) -> None:
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
        data={"title": "merge-test", "language": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    txt = client.get(body["transcript_txt_url"], headers=auth_headers)
    assert txt.status_code == 200

    paragraphs = txt.text.rstrip("\n").split("\n\n")
    assert len(paragraphs) == 2, txt.text
    assert paragraphs[0] == "[00:00] SPEAKER_00: Hello there. How are you today?"
    assert paragraphs[1] == "[00:03] SPEAKER_01: I am well, thank you."


# ----- filename pattern -----

_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}_([a-z0-9-]+)_[0-9a-f]{8}\.(txt|json)$")


def test_filename_pattern_with_title(client, auth_headers, fake_audio, outputs_dir) -> None:
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
        data={"title": "Therapy Session 5"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    short = body["id"].replace("-", "")[:8]

    txts = list(outputs_dir.glob(f"*_therapy-session-5_{short}.txt"))
    jsons = list(outputs_dir.glob(f"*_therapy-session-5_{short}.json"))
    assert len(txts) == 1, list(outputs_dir.iterdir())
    assert len(jsons) == 1
    assert _PATTERN.match(txts[0].name)
    assert _PATTERN.match(jsons[0].name)


def test_filename_untitled(client, auth_headers, fake_audio, outputs_dir) -> None:
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    short = r.json()["id"].replace("-", "")[:8]
    matches = list(outputs_dir.glob(f"*_untitled_{short}.txt"))
    assert len(matches) == 1


def test_filename_special_chars(client, auth_headers, fake_audio, outputs_dir) -> None:
    r = client.post(
        "/v1/transcribe",
        headers=auth_headers,
        files={"audio": ("clip.wav", fake_audio, "audio/wav")},
        data={"title": "Dr. Smith / Patient #42!"},
    )
    assert r.status_code == 200, r.text
    short = r.json()["id"].replace("-", "")[:8]
    txts = list(outputs_dir.glob(f"*_dr-smith-patient-42_{short}.txt"))
    assert len(txts) == 1, list(outputs_dir.iterdir())
    m = _PATTERN.match(txts[0].name)
    assert m and len(m.group(1)) <= 40


def test_slug_truncation_at_40() -> None:
    long_title = "the quick brown fox jumps over the lazy dog and keeps running forever"
    s = storage.slugify(long_title)
    assert len(s) <= 40
    assert all(c.isalnum() or c == "-" for c in s)


# ----- resolver -----

def test_resolver_legacy_fallback(client, auth_headers, outputs_dir) -> None:
    legacy_id = str(uuid.uuid4())
    txt = outputs_dir / f"{legacy_id}.txt"
    js = outputs_dir / f"{legacy_id}.json"
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("[00:00] SPEAKER_00: legacy content\n")
    js.write_text('{"id": "%s"}' % legacy_id)

    r = client.get(f"/v1/results/{legacy_id}/transcript.txt", headers=auth_headers)
    assert r.status_code == 200
    assert "legacy content" in r.text

    r2 = client.get(f"/v1/results/{legacy_id}/transcript.json", headers=auth_headers)
    assert r2.status_code == 200


def test_resolver_404_when_neither(client, auth_headers) -> None:
    missing = str(uuid.uuid4())
    r = client.get(f"/v1/results/{missing}/transcript.txt", headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {"error": "transcript not found"}
