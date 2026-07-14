from __future__ import annotations

import os
import re
import time
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

    # TestClient uses the platform newline when decoding FileResponse on
    # Windows; normalize so this assertion remains cross-platform.
    paragraphs = txt.text.replace("\r\n", "\n").rstrip("\n").split("\n\n")
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


# ----- retention semantics -----

def _make_old_file(path: Path, days_old: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("dummy")
    cutoff = time.time() - days_old * 86400
    os.utime(path, (cutoff, cutoff))
    return path


def test_retention_deletes_old(outputs_dir, uploads_dir) -> None:
    old_out = _make_old_file(outputs_dir / "old_output.txt", 31)
    old_up = _make_old_file(uploads_dir / "old_upload.wav", 31)
    counts = storage.cleanup_old_files(30)
    assert not old_out.exists()
    assert not old_up.exists()
    assert counts["outputs"] >= 1
    assert counts["uploads"] >= 1


def test_retention_keeps_recent(outputs_dir) -> None:
    fresh = outputs_dir / "fresh.txt"
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_text("dummy")
    counts = storage.cleanup_old_files(30)
    assert fresh.exists()
    assert counts["outputs"] == 0
    fresh.unlink()


def test_retention_zero_deletes_anything_older_than_now(outputs_dir) -> None:
    p = outputs_dir / "second_old.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dummy")
    one_second_ago = time.time() - 1
    os.utime(p, (one_second_ago, one_second_ago))
    counts = storage.cleanup_old_files(0)
    assert not p.exists()
    assert counts["outputs"] >= 1


def test_retention_negative_disables(outputs_dir) -> None:
    p = _make_old_file(outputs_dir / "very_old.txt", 365)
    counts = storage.cleanup_old_files(-1)
    assert p.exists()
    assert counts == {"uploads": 0, "outputs": 0}
    p.unlink()


def test_retention_skips_models_dir(models_dir) -> None:
    p = _make_old_file(models_dir / "weights.bin", 365)
    storage.cleanup_old_files(0)
    assert p.exists(), "models_dir must never be swept"
    p.unlink()


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


# ----- pipeline.load() idempotency on partial failure (was task #10) -----

def test_load_retries_after_partial_init_failure() -> None:
    """If a component's load throws, the pipeline must remain unloaded so the
    next load() call retries instead of short-circuiting on stale state.

    Post-Phase-3 the pipeline composes an ASR backend + a Diarizer and loads
    them via asyncio.gather; the idempotency invariant is exercised here by
    injecting an ASR backend whose load() fails on the first attempt and
    succeeds on the second.
    """
    import asyncio

    from app.pipeline import TranscribePipeline

    attempts = {"n": 0}

    class _FlakyASR:
        def name(self) -> str:
            return "flaky-asr"

        async def load(self) -> None:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("simulated ASR init failure")
            return None

        async def health(self) -> bool:
            return True

        async def transcribe(self, audio_path, *, language=None):  # pragma: no cover
            raise NotImplementedError

    class _NoopDiarizer:
        async def load(self) -> None:
            return None

    pipe = TranscribePipeline(asr=_FlakyASR(), diarizer=_NoopDiarizer())  # type: ignore[arg-type]

    # First attempt: must propagate the exception, leave pipeline unloaded.
    try:
        asyncio.run(pipe.load())
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected RuntimeError on first load()")
    assert pipe.is_loaded() is False
    assert attempts["n"] == 1

    # Second attempt: must actually retry (not short-circuit), succeed,
    # and flip the loaded flag.
    asyncio.run(pipe.load())
    assert pipe.is_loaded() is True
    assert attempts["n"] == 2
