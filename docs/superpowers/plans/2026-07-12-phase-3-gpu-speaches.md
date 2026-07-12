# Phase 3 GPU acceleration via Speaches sidecar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split ASR out of the transcribe-svc container into a Speaches sidecar so the RTX 5090 (Blackwell sm_120) can run large-v3 without rebuilding transcribe-svc's whisperx/torch pin chain. Pyannote diarization stays CPU-side, in-process. ASR and diarization run concurrently via `asyncio.gather`; a new stitcher joins the two.

**Architecture:** Three-container Docker stack running on Alienware (Windows 11 + WSL2 + Docker Desktop). `tailscale` sidecar owns the network identity; `transcribe-svc` and `speaches` share its netns via `network_mode: service:tailscale`. `speaches` binds `127.0.0.1:8001` so it's not tailnet-visible. Transcribe-svc calls Speaches over `http://localhost:8001/v1/audio/transcriptions`. Fallback chain (env `ASR_HOSTS`) picks the first healthy ASR backend: Speaches → MBP whisper.cpp → local WhisperX (in-container CPU).

**Tech Stack:** FastAPI, asyncio, httpx (new), Speaches (`ghcr.io/speaches-ai/speaches:latest-cuda`), Tailscale sidecar container, WhisperX 3.2.0 (retained for local fallback), pyannote.audio 3.1.1, faster-whisper large-v3.

**Source of truth:** `docs/superpowers/specs/2026-07-12-phase-3-gpu-speaches-design.md`.

---

## File map (what gets created / modified)

**Created:**
- `app/asr.py` — `ASRBackend` protocol, `LocalWhisperXASR` (extracted from existing pipeline), `SpeachesASR` (OpenAI-compat client), `ASRRouter` (fallback chain).
- `app/diarize.py` — `Diarizer` class wrapping pyannote model load + inference (extracted from existing pipeline).
- `app/stitch.py` — pure function `stitch_speakers()` that maps pyannote turns onto ASR segments.
- `tests/test_stitch.py`, `tests/test_asr_speaches.py`, `tests/test_asr_router.py` — unit tests for the new modules.
- `docs/superpowers/plans/2026-07-12-phase-3-gpu-speaches.md` — this file.

**Modified:**
- `app/pipeline.py` — replace the monolithic `_transcribe_blocking` with composition: `asyncio.gather(asr.transcribe(), diarize.turns())`, then `stitch_speakers()`.
- `app/config.py` — add `ASR_BACKEND`, `ASR_HOSTS`, `ASR_MODEL_ID`, `ASR_HEALTHCHECK_TIMEOUT_S`, `TS_AUTHKEY` settings.
- `.env.example` — document new settings.
- `requirements.txt` — add `httpx==0.27.2`.
- `docker-compose.gpu.yml` — replace scaffold with three-service topology (tailscale + speaches + transcribe-svc).
- `tests/conftest.py` — stub the new `asr` + `diarize` layer if needed (existing stubs of `pipeline.transcribe` still work).
- `docs/DEPLOY.md` — Windows 11 + WSL2 + Docker Desktop section.
- `docs/API.md` — brief note on the new `ASR_BACKEND` behavior.

**Untouched (contract stability for TesterClaw T4.0 + iPhone client):**
- `app/main.py` (all routes), `app/auth.py`, `app/storage.py`, `app/schemas.py`
- Request/response shapes
- URL patterns
- `.txt` / `.json` output formats
- Bearer-token auth
- Retention semantics

---

## Task 0: Setup — add `httpx` to requirements

**Files:**
- Modify: `requirements.txt` (append after `python-dateutil`)

- [ ] **Step 1: Add httpx to requirements.txt**

Append to `requirements.txt`:

```
# --- Phase 3 additions ---
# OpenAI-compat ASR client (Speaches, whisper.cpp server). Async-friendly HTTP.
httpx==0.27.2
```

- [ ] **Step 2: Verify pip-resolvability inside a fresh venv (dry-run — do not activate the running container's env)**

Run:

```bash
python3 -m venv /tmp/phase3-req-check
/tmp/phase3-req-check/bin/pip install --dry-run -r requirements.txt 2>&1 | tail -5
```

Expected: `Would install ...` with no `Could not find a version` errors. If it errors, fix the pin.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "$(cat <<'EOF'
Phase 3: add httpx to requirements

Async HTTP client for the new OpenAI-compat ASR backend (Speaches on
the Alienware, whisper.cpp server on the Mac).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 1: Extract `Diarizer` to `app/diarize.py`

Pure refactor. Move pyannote model load + inference into a small self-contained class. `TranscribePipeline` will still work identically because it now delegates to `Diarizer` internally (done in Task 4).

**Files:**
- Create: `app/diarize.py`
- Test: (integration only — pyannote is stubbed at pipeline level in tests)

- [ ] **Step 1: Write `app/diarize.py`**

```python
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger("transcribe-svc.diarize")


class Diarizer:
    """Wraps pyannote diarization. Loads the model lazily on first use.

    Heavy imports (whisperx / pyannote / torch) are deferred so test code can
    instantiate this without pulling them in.
    """

    def __init__(self) -> None:
        self._diarizer: Any | None = None
        self._whisperx: Any | None = None
        self._loaded: bool = False
        self._load_lock = asyncio.Lock()

    def is_loaded(self) -> bool:
        return self._loaded

    async def load(self) -> None:
        async with self._load_lock:
            if self._loaded:
                return
            log.info(
                "Loading diarization model (name=%s, device=%s)",
                settings.diarization_model,
                settings.whisperx_device,
            )
            await asyncio.to_thread(self._load_blocking)
            self._loaded = True
            log.info("Diarization model loaded.")

    def _load_blocking(self) -> None:
        os.environ.setdefault("HF_HOME", str(settings.hf_home))
        import whisperx  # type: ignore

        kwargs: dict[str, Any] = {"device": settings.whisperx_device}
        if settings.hf_token:
            kwargs["use_auth_token"] = settings.hf_token
        diarizer = whisperx.DiarizationPipeline(
            model_name=settings.diarization_model,
            **kwargs,
        )
        self._whisperx = whisperx
        self._diarizer = diarizer

    async def turns(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run diarization on the given audio file. Returns a list of
        {'start': float, 'end': float, 'speaker': str} turns, sorted by start.
        """
        if not self._loaded:
            await self.load()
        return await asyncio.to_thread(self._turns_blocking, audio_path, num_speakers)

    def _turns_blocking(
        self,
        audio_path: Path,
        num_speakers: int | None,
    ) -> list[dict[str, Any]]:
        assert self._diarizer is not None and self._whisperx is not None
        audio = self._whisperx.load_audio(str(audio_path))
        kwargs: dict[str, Any] = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers
        diarize_segments = self._diarizer(audio, **kwargs)
        # whisperx returns a pandas DataFrame with columns ['start','end','speaker'].
        turns: list[dict[str, Any]] = []
        for _, row in diarize_segments.iterrows():
            turns.append({
                "start": float(row["start"]),
                "end": float(row["end"]),
                "speaker": str(row["speaker"]),
            })
        turns.sort(key=lambda t: t["start"])
        return turns


diarizer = Diarizer()
```

- [ ] **Step 2: Verify the file compiles**

Run:

```bash
python3 -m py_compile app/diarize.py
```

Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add app/diarize.py
git commit -m "$(cat <<'EOF'
Phase 3: extract Diarizer to app/diarize.py

Wraps pyannote model load + inference in a small class with lazy loading
and an async turns() surface. TranscribePipeline will delegate here in
Task 4. Behavior unchanged; this is pure extraction to make the pipeline
composable with a swappable ASR backend.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Extract `LocalWhisperXASR` and define `ASRBackend` protocol in `app/asr.py`

Refactors the WhisperX ASR (load-model + transcribe + align) out of `TranscribePipeline` into `LocalWhisperXASR`. Defines the `ASRBackend` protocol that both `LocalWhisperXASR` (this task) and `SpeachesASR` (Task 5) will implement.

**Files:**
- Create: `app/asr.py`

- [ ] **Step 1: Write `app/asr.py` with `ASRBackend` protocol + `LocalWhisperXASR`**

```python
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Protocol

from app.config import settings

log = logging.getLogger("transcribe-svc.asr")


class ASRResult:
    """Simple container for ASR output. Fields:
    - segments: list of {'start': float, 'end': float, 'text': str, optional 'words': [...]}
    - language: detected language code (e.g. 'en')
    - duration_seconds: total audio duration
    """

    def __init__(
        self,
        segments: list[dict[str, Any]],
        language: str,
        duration_seconds: float,
    ) -> None:
        self.segments = segments
        self.language = language
        self.duration_seconds = duration_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": self.segments,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
        }


class ASRBackend(Protocol):
    """Common surface for every ASR backend (local WhisperX, Speaches,
    whisper.cpp server, future engines).
    """

    async def load(self) -> None: ...
    async def health(self) -> bool: ...
    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRResult: ...
    def name(self) -> str: ...


class LocalWhisperXASR:
    """In-process WhisperX ASR. Fallback tier; also the current Phase 2 path
    when ASR_HOSTS is unset. Loads the model lazily.
    """

    def __init__(self) -> None:
        self._model: Any | None = None
        self._whisperx: Any | None = None
        self._align_models: dict[str, tuple[Any, Any]] = {}
        self._loaded: bool = False
        self._load_lock = asyncio.Lock()

    def name(self) -> str:
        return "local-whisperx"

    async def load(self) -> None:
        async with self._load_lock:
            if self._loaded:
                return
            log.info(
                "Loading whisperx (model=%s, device=%s, compute_type=%s)",
                settings.whisper_model,
                settings.whisperx_device,
                settings.whisperx_compute_type,
            )
            await asyncio.to_thread(self._load_blocking)
            self._loaded = True

    def _load_blocking(self) -> None:
        os.environ.setdefault("HF_HOME", str(settings.hf_home))
        import whisperx  # type: ignore

        model = whisperx.load_model(
            settings.whisper_model,
            device=settings.whisperx_device,
            compute_type=settings.whisperx_compute_type,
        )
        self._whisperx = whisperx
        self._model = model

    async def health(self) -> bool:
        # Local backend is always available; loading may be slow but never
        # unreachable. Return True even before load — the pipeline will
        # trigger load() on first use.
        return True

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRResult:
        if not self._loaded:
            await self.load()
        return await asyncio.to_thread(self._transcribe_blocking, audio_path, language)

    def _transcribe_blocking(
        self,
        audio_path: Path,
        language: str | None,
    ) -> ASRResult:
        assert self._model is not None and self._whisperx is not None
        whisperx = self._whisperx
        audio = whisperx.load_audio(str(audio_path))
        duration_seconds = float(len(audio)) / 16000.0

        transcribe_kwargs: dict[str, Any] = {}
        if language:
            transcribe_kwargs["language"] = language
        result = self._model.transcribe(audio, **transcribe_kwargs)
        detected_language = result.get("language", language or "en")

        try:
            align_model, align_meta = self._get_align_model(detected_language)
            aligned = whisperx.align(
                result["segments"],
                align_model,
                align_meta,
                audio,
                settings.whisperx_device,
                return_char_alignments=False,
            )
            segments = aligned.get("segments", [])
        except Exception as e:
            log.warning(
                "Alignment failed for language=%s: %s; returning coarse segments.",
                detected_language, e,
            )
            segments = result.get("segments", [])

        return ASRResult(
            segments=segments,
            language=detected_language,
            duration_seconds=duration_seconds,
        )

    def _get_align_model(self, language: str) -> tuple[Any, Any]:
        assert self._whisperx is not None
        if language not in self._align_models:
            self._align_models[language] = self._whisperx.load_align_model(
                language_code=language,
                device=settings.whisperx_device,
            )
        return self._align_models[language]
```

- [ ] **Step 2: Verify the file compiles**

Run:

```bash
python3 -m py_compile app/asr.py
```

Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add app/asr.py
git commit -m "$(cat <<'EOF'
Phase 3: extract LocalWhisperXASR and define ASRBackend protocol

Pulls the WhisperX ASR path out of TranscribePipeline into a class that
implements the ASRBackend protocol. Preserves existing behavior; also
creates the surface that SpeachesASR will implement in Task 5.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Write `stitch_speakers()` in `app/stitch.py`

Pure function. Assigns a speaker to each ASR segment based on which pyannote turn(s) overlap it. TDD-friendly.

**Files:**
- Create: `app/stitch.py`
- Test: `tests/test_stitch.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_stitch.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
cd /home/transcriber/Github/transcriberproject && python3 -m pytest tests/test_stitch.py -v 2>&1 | tail -20
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.stitch'`. If pytest itself is not installed, the container path works: `docker compose run --rm transcribe-svc pytest tests/test_stitch.py -v`.

- [ ] **Step 3: Write minimal implementation**

Create `app/stitch.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:

```bash
cd /home/transcriber/Github/transcriberproject && python3 -m pytest tests/test_stitch.py -v 2>&1 | tail -20
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/stitch.py tests/test_stitch.py
git commit -m "$(cat <<'EOF'
Phase 3: add stitch_speakers() with unit tests

Pure function that assigns a speaker label to each ASR segment based on
which pyannote turn has the largest overlap. Handles empty inputs,
missing turns (SPEAKER_??), multi-turn segments (dominant speaker wins),
and preserves segment fields verbatim.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Refactor `TranscribePipeline` to compose Diarizer + ASRBackend + stitch

Rewrite `app/pipeline.py` so `transcribe()` runs ASR and diarization concurrently, then stitches. Keeps the exact same public surface (`pipeline.transcribe()` and the dict shape it returns) so `app/main.py` and existing tests don't need to change. Backend selection defaults to LocalWhisperXASR for this task; SpeachesASR + ASRRouter arrive in Tasks 5–6.

**Files:**
- Modify: `app/pipeline.py` (full rewrite)

- [ ] **Step 1: Overwrite `app/pipeline.py`**

```python
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.asr import ASRBackend, ASRResult, LocalWhisperXASR
from app.config import settings
from app.diarize import Diarizer
from app.stitch import stitch_speakers

log = logging.getLogger("transcribe-svc.pipeline")


class TranscribePipeline:
    """Orchestrates: ASR (any ASRBackend) + diarization (Diarizer) run
    concurrently on the same audio; then stitch_speakers() joins them.

    Concurrency across requests is gated by a semaphore sized to
    settings.max_concurrent_jobs.
    """

    def __init__(
        self,
        asr: ASRBackend | None = None,
        diarizer: Diarizer | None = None,
    ) -> None:
        self._asr: ASRBackend = asr or LocalWhisperXASR()
        self._diarizer: Diarizer = diarizer or Diarizer()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._loaded = False
        self._load_lock = asyncio.Lock()

    def is_loaded(self) -> bool:
        return self._loaded

    async def load(self) -> None:
        async with self._load_lock:
            if self._loaded:
                return
            # Load in parallel — ASR load may be a no-op for HTTP-backed
            # backends, and diarization can pull from HF concurrently.
            await asyncio.gather(self._asr.load(), self._diarizer.load())
            self._loaded = True
            log.info("Pipeline loaded (asr=%s, diarize_device=%s)",
                     self._asr.name(), settings.whisperx_device)

    async def transcribe(
        self,
        audio_path: Path,
        *,
        num_speakers: int | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        if not self._loaded:
            await self.load()
        async with self._semaphore:
            t_start = time.monotonic()
            asr_result, turns = await asyncio.gather(
                self._asr.transcribe(audio_path, language=language),
                self._diarizer.turns(audio_path, num_speakers=num_speakers),
            )
            segments_with_speakers = stitch_speakers(asr_result.segments, turns)
            speakers = self._count_speakers(segments_with_speakers)
            elapsed = time.monotonic() - t_start
            log.info(
                "Transcribed %.1fs of audio in %.1fs (%.2fx realtime); "
                "asr=%s language=%s speakers=%d",
                asr_result.duration_seconds,
                elapsed,
                (asr_result.duration_seconds / elapsed) if elapsed > 0 else 0.0,
                self._asr.name(),
                asr_result.language,
                speakers,
            )
            return {
                "segments": segments_with_speakers,
                "language": asr_result.language,
                "duration_seconds": asr_result.duration_seconds,
                "speakers_detected": speakers,
                "elapsed_seconds": elapsed,
                "asr_backend": self._asr.name(),
            }

    @staticmethod
    def _count_speakers(segments: list[dict[str, Any]]) -> int:
        speakers: set[str] = set()
        for seg in segments:
            spk = seg.get("speaker")
            if spk and spk != "SPEAKER_??":
                speakers.add(spk)
        return len(speakers)


def render_txt(result: dict[str, Any]) -> str:
    """Render pipeline result to a speaker-labeled .txt.

    One paragraph per speaker turn (consecutive same-speaker segments merged),
    '[mm:ss] SPEAKER_XX: text' prefix using the start time of the turn.
    """
    paragraphs: list[tuple[float, str, list[str]]] = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker") or "SPEAKER_??"
        if paragraphs and paragraphs[-1][1] == speaker:
            paragraphs[-1][2].append(text)
        else:
            paragraphs.append((float(seg.get("start") or 0.0), speaker, [text]))
    lines: list[str] = []
    for start, speaker, parts in paragraphs:
        mm = int(start // 60)
        ss = int(start % 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {speaker}: {' '.join(parts)}")
    return ("\n\n".join(lines) + "\n") if lines else ""


def render_json(job_id: str, result: dict[str, Any]) -> str:
    """Render pipeline result + a header to JSON string."""
    payload = {
        "id": job_id,
        "created_at": result.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "duration_seconds": result.get("duration_seconds"),
        "language": result.get("language"),
        "speakers_detected": result.get("speakers_detected"),
        "model": settings.whisper_model,
        "compute_type": settings.whisperx_compute_type,
        "device": settings.whisperx_device,
        "diarization_model": settings.diarization_model,
        "asr_backend": result.get("asr_backend"),
        "segments": result.get("segments", []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)


def _json_default(obj: Any) -> Any:
    try:
        return float(obj)
    except (TypeError, ValueError):
        return str(obj)


pipeline = TranscribePipeline()
```

- [ ] **Step 2: Verify the module compiles**

Run:

```bash
python3 -m py_compile app/pipeline.py
```

Expected: no output, exit 0.

- [ ] **Step 3: Run the existing test suite to verify unchanged surface**

Run:

```bash
docker compose run --rm transcribe-svc pytest tests/ -v 2>&1 | tail -40
```

Expected: all 22 existing Phase 1/2 tests pass. The `pipeline.transcribe` stub in `conftest.py` still catches at the same level (module-level `pipeline` instance's method is monkey-patched).

- [ ] **Step 4: Commit**

```bash
git add app/pipeline.py
git commit -m "$(cat <<'EOF'
Phase 3: refactor TranscribePipeline to compose ASR + Diarizer + stitch

transcribe() now runs asr.transcribe() and diarizer.turns() concurrently
via asyncio.gather, then stitch_speakers() joins them. Public surface
(pipeline.transcribe returns the same dict shape) is unchanged so
app/main.py and existing tests are untouched.

Adds asr_backend to the returned dict and the .json header so it's
visible which backend served each request.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add `SpeachesASR` (OpenAI-compat ASR client) to `app/asr.py`

**Files:**
- Modify: `app/asr.py` (append `SpeachesASR` class)
- Test: `tests/test_asr_speaches.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_asr_speaches.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.asr import SpeachesASR


@pytest.fixture
def tmp_audio(tmp_path: Path) -> Path:
    p = tmp_path / "clip.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 100)
    return p


@pytest.mark.asyncio
async def test_speaches_parses_verbose_json(tmp_audio: Path) -> None:
    fake_body = {
        "text": "hello world how are you",
        "language": "en",
        "duration": 3.5,
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "hello world"},
            {"start": 1.5, "end": 3.5, "text": "how are you"},
        ],
    }
    backend = SpeachesASR(
        base_url="http://localhost:8001",
        model_id="Systran/faster-whisper-large-v3",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = AsyncMock()
    mock_response.json = lambda: fake_body
    mock_response.raise_for_status = lambda: None
    mock_client.post = AsyncMock(return_value=mock_response)
    with patch.object(backend, "_client", mock_client):
        result = await backend.transcribe(tmp_audio)
    assert len(result.segments) == 2
    assert result.segments[0]["text"] == "hello world"
    assert result.language == "en"
    assert result.duration_seconds == 3.5


@pytest.mark.asyncio
async def test_speaches_health_ok_on_200(tmp_audio: Path) -> None:
    backend = SpeachesASR(
        base_url="http://localhost:8001",
        model_id="Systran/faster-whisper-large-v3",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client.get = AsyncMock(return_value=mock_response)
    with patch.object(backend, "_client", mock_client):
        assert await backend.health() is True


@pytest.mark.asyncio
async def test_speaches_health_false_on_connect_error() -> None:
    backend = SpeachesASR(
        base_url="http://localhost:8001",
        model_id="Systran/faster-whisper-large-v3",
    )
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("nope"))
    with patch.object(backend, "_client", mock_client):
        assert await backend.health() is False


@pytest.mark.asyncio
async def test_speaches_name() -> None:
    backend = SpeachesASR(
        base_url="http://localhost:8001",
        model_id="whatever",
    )
    assert backend.name() == "speaches@http://localhost:8001"
```

Ensure `pytest-asyncio` is available. If not, add to requirements:

```bash
grep -q pytest-asyncio requirements.txt || echo 'pytest-asyncio==0.24.0' >> requirements.txt
```

And add pytest config so `@pytest.mark.asyncio` auto-mode works. Append to `pyproject.toml` (create if missing) or add to a new `pytest.ini`:

Create `pytest.ini`:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 2: Append `SpeachesASR` class to `app/asr.py`**

Add at the bottom of `app/asr.py`:

```python
import httpx


class SpeachesASR:
    """OpenAI-compatible ASR client. Works against Speaches, whisper.cpp
    server (with --inference-path /v1/audio/transcriptions), or any other
    server exposing that endpoint.

    base_url: e.g. 'http://localhost:8001'
    model_id: HuggingFace model ID passed as the 'model' form field.
    """

    def __init__(
        self,
        base_url: str,
        model_id: str,
        *,
        timeout_s: float = 300.0,
        response_format: str = "verbose_json",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._timeout = timeout_s
        self._response_format = response_format
        self._client = httpx.AsyncClient(timeout=timeout_s)

    def name(self) -> str:
        return f"speaches@{self._base_url}"

    async def load(self) -> None:
        # No-op: model lives on the remote host, loaded on its first call.
        return None

    async def health(self) -> bool:
        try:
            resp = await self._client.get(
                f"{self._base_url}/v1/models",
                timeout=settings.asr_healthcheck_timeout_s,
            )
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            return False
        except httpx.HTTPError:
            return False

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRResult:
        with audio_path.open("rb") as fh:
            data = fh.read()
        files = {"file": (audio_path.name, data, "audio/wav")}
        form: dict[str, str] = {
            "model": self._model_id,
            "response_format": self._response_format,
        }
        if language:
            form["language"] = language
        resp = await self._client.post(
            f"{self._base_url}/v1/audio/transcriptions",
            files=files,
            data=form,
        )
        resp.raise_for_status()
        body = resp.json()
        segments = [
            {
                "start": float(s.get("start") or 0.0),
                "end": float(s.get("end") or 0.0),
                "text": str(s.get("text") or ""),
            }
            for s in body.get("segments", [])
        ]
        duration = float(body.get("duration") or 0.0)
        detected_language = str(body.get("language") or language or "en")
        return ASRResult(
            segments=segments,
            language=detected_language,
            duration_seconds=duration,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
```

- [ ] **Step 3: Run the tests to verify they pass**

Run:

```bash
docker compose run --rm transcribe-svc pytest tests/test_asr_speaches.py -v 2>&1 | tail -20
```

Expected: all 4 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add app/asr.py tests/test_asr_speaches.py requirements.txt pytest.ini
git commit -m "$(cat <<'EOF'
Phase 3: add SpeachesASR OpenAI-compat ASR client

Async httpx-based client that talks to any OpenAI-compat
/v1/audio/transcriptions endpoint. Health check hits /v1/models. Same
client works against Speaches on the Alienware and whisper.cpp server
on the Mac (with --inference-path /v1/audio/transcriptions).

Adds pytest.ini with asyncio_mode=auto and pytest-asyncio to
requirements so async tests run cleanly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add `ASRRouter` with fallback chain

Router picks the first healthy backend from a priority list. Backend list is built from `settings.asr_hosts` (comma-separated).

**Files:**
- Modify: `app/asr.py` (append `ASRRouter`)
- Test: `tests/test_asr_router.py`

- [ ] **Step 1: Write the failing tests first**

Create `tests/test_asr_router.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.asr import ASRBackend, ASRResult, ASRRouter


class _StubBackend:
    def __init__(self, name: str, *, healthy: bool = True, segments: list[dict[str, Any]] | None = None) -> None:
        self._name = name
        self._healthy = healthy
        self._segments = segments if segments is not None else [{"start": 0.0, "end": 1.0, "text": name}]
        self.transcribe_called = False

    def name(self) -> str:
        return self._name

    async def load(self) -> None:
        return None

    async def health(self) -> bool:
        return self._healthy

    async def transcribe(self, audio_path: Path, *, language: str | None = None) -> ASRResult:
        self.transcribe_called = True
        return ASRResult(segments=self._segments, language="en", duration_seconds=1.0)


@pytest.fixture
def tmp_audio(tmp_path: Path) -> Path:
    p = tmp_path / "clip.wav"
    p.write_bytes(b"RIFF" + b"\x00" * 32)
    return p


async def test_router_first_healthy_wins(tmp_audio: Path) -> None:
    a = _StubBackend("A", healthy=True)
    b = _StubBackend("B", healthy=True)
    router = ASRRouter([a, b])
    result = await router.transcribe(tmp_audio)
    assert a.transcribe_called is True
    assert b.transcribe_called is False
    assert result.segments[0]["text"] == "A"


async def test_router_falls_through_when_first_unhealthy(tmp_audio: Path) -> None:
    a = _StubBackend("A", healthy=False)
    b = _StubBackend("B", healthy=True)
    router = ASRRouter([a, b])
    result = await router.transcribe(tmp_audio)
    assert a.transcribe_called is False
    assert b.transcribe_called is True
    assert result.segments[0]["text"] == "B"


async def test_router_raises_when_all_unhealthy(tmp_audio: Path) -> None:
    a = _StubBackend("A", healthy=False)
    b = _StubBackend("B", healthy=False)
    router = ASRRouter([a, b])
    with pytest.raises(RuntimeError, match="no healthy ASR backend"):
        await router.transcribe(tmp_audio)


async def test_router_falls_through_on_transcribe_exception(tmp_audio: Path) -> None:
    class _Boom(_StubBackend):
        async def transcribe(self, audio_path: Path, *, language: str | None = None) -> ASRResult:
            raise RuntimeError("kaboom")

    a = _Boom("A", healthy=True)
    b = _StubBackend("B", healthy=True)
    router = ASRRouter([a, b])
    result = await router.transcribe(tmp_audio)
    assert b.transcribe_called is True
    assert result.segments[0]["text"] == "B"


def test_router_name_lists_backends() -> None:
    a = _StubBackend("A")
    b = _StubBackend("B")
    router = ASRRouter([a, b])
    assert router.name() == "router[A,B]"
```

- [ ] **Step 2: Append `ASRRouter` to `app/asr.py`**

```python
class ASRRouter:
    """Tries backends in priority order. First one that passes a health
    check gets the request. If its transcribe() raises, falls through to
    the next healthy backend. If none respond, raises RuntimeError.
    """

    def __init__(self, backends: list[ASRBackend]) -> None:
        if not backends:
            raise ValueError("ASRRouter requires at least one backend")
        self._backends = backends

    def name(self) -> str:
        return "router[" + ",".join(b.name() for b in self._backends) + "]"

    async def load(self) -> None:
        # Backends load lazily on first call; nothing to do here.
        return None

    async def health(self) -> bool:
        for b in self._backends:
            if await b.health():
                return True
        return False

    async def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRResult:
        last_exc: Exception | None = None
        for b in self._backends:
            if not await b.health():
                log.info("ASRRouter: backend %s unhealthy, skipping", b.name())
                continue
            try:
                log.info("ASRRouter: routing to %s", b.name())
                return await b.transcribe(audio_path, language=language)
            except Exception as e:
                log.warning(
                    "ASRRouter: backend %s failed mid-request (%s); falling through",
                    b.name(), e,
                )
                last_exc = e
        if last_exc is not None:
            raise RuntimeError(f"no healthy ASR backend responded successfully: {last_exc}") from last_exc
        raise RuntimeError("no healthy ASR backend responded successfully")
```

- [ ] **Step 3: Run the tests to verify they pass**

Run:

```bash
docker compose run --rm transcribe-svc pytest tests/test_asr_router.py -v 2>&1 | tail -20
```

Expected: all 5 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add app/asr.py tests/test_asr_router.py
git commit -m "$(cat <<'EOF'
Phase 3: add ASRRouter with fallback chain

Iterates backends in priority order, health-checks each with a short
timeout, routes to the first healthy responder. Falls through on
transcribe exceptions too so a mid-request failure gets retried against
the next tier (Speaches → whisper.cpp on Mac → local WhisperX CPU).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire `ASR_BACKEND` / `ASR_HOSTS` env into config + pipeline factory

Add settings and a factory that builds either `LocalWhisperXASR` alone (default, preserves Phase 2 behavior) or an `ASRRouter` of `SpeachesASR` instances + `LocalWhisperXASR` as final fallback.

**Files:**
- Modify: `app/config.py`
- Modify: `app/pipeline.py` (add factory + adjust module-level singleton)
- Modify: `.env.example`
- Modify: `tests/conftest.py` (default backend env for tests)

- [ ] **Step 1: Add new settings to `app/config.py`**

After `max_upload_mb`, add:

```python
    # --- Phase 3: ASR routing ---
    # 'whisperx' — in-container WhisperX only (Phase 2 behavior; default).
    # 'router'   — try backends in ASR_HOSTS order; sentinel 'local-whisperx'
    #              means "fall back to in-container WhisperX".
    asr_backend: str = Field(default="whisperx", alias="ASR_BACKEND")

    # Comma-separated priority list. URL entries are OpenAI-compat backends
    # (Speaches, whisper.cpp server). The sentinel 'local-whisperx' means
    # in-container WhisperX. Example:
    #   ASR_HOSTS=http://localhost:8001,http://mbp.tailnet.ts.net:8001,local-whisperx
    asr_hosts: str = Field(default="", alias="ASR_HOSTS")

    # HuggingFace model ID to pass to OpenAI-compat backends. Ignored for
    # local-whisperx (which uses WHISPER_MODEL instead).
    asr_model_id: str = Field(
        default="Systran/faster-whisper-large-v3",
        alias="ASR_MODEL_ID",
    )

    # Health-check timeout per backend in seconds.
    asr_healthcheck_timeout_s: float = Field(default=2.0, alias="ASR_HEALTHCHECK_TIMEOUT_S")

    # Tailscale sidecar reusable auth key. Only consumed by the tailscale
    # container itself; the app never reads it. Kept in Settings so a
    # missing value is a clear config error at startup.
    ts_authkey: str = Field(default="", alias="TS_AUTHKEY")
```

- [ ] **Step 2: Add factory to `app/pipeline.py` and swap the module-level singleton**

At the bottom of `app/pipeline.py`, replace:

```python
pipeline = TranscribePipeline()
```

with:

```python
def _build_asr_backend() -> ASRBackend:
    if settings.asr_backend == "whisperx":
        return LocalWhisperXASR()
    if settings.asr_backend == "router":
        from app.asr import ASRRouter, SpeachesASR
        hosts = [h.strip() for h in settings.asr_hosts.split(",") if h.strip()]
        if not hosts:
            log.warning("ASR_BACKEND=router but ASR_HOSTS is empty; using LocalWhisperXASR")
            return LocalWhisperXASR()
        backends: list[ASRBackend] = []
        for h in hosts:
            if h == "local-whisperx":
                backends.append(LocalWhisperXASR())
            elif h.startswith("http://") or h.startswith("https://"):
                backends.append(SpeachesASR(base_url=h, model_id=settings.asr_model_id))
            else:
                log.warning("Ignoring unrecognized ASR_HOSTS entry: %r", h)
        if not backends:
            return LocalWhisperXASR()
        return ASRRouter(backends)
    raise ValueError(f"Unknown ASR_BACKEND={settings.asr_backend!r}; expected 'whisperx' or 'router'")


pipeline = TranscribePipeline(asr=_build_asr_backend())
```

Also add these imports near the top:

```python
from app.asr import ASRBackend, LocalWhisperXASR
```

(Make sure the earlier `from app.asr import ASRBackend, ASRResult, LocalWhisperXASR` is still present; if so, this is a no-op.)

- [ ] **Step 3: Update `.env.example`**

Append at end (before the `# --- Reserved / not yet wired in ---` block):

```
# --- Phase 3: ASR routing ---
# 'whisperx' — in-container WhisperX only (Phase 2 default; use on CPU host).
# 'router'   — try backends in ASR_HOSTS order; sentinel 'local-whisperx'
#              means fall back to in-container WhisperX.
ASR_BACKEND=whisperx

# Comma-separated priority list of OpenAI-compat backends (Speaches on
# Alienware, whisper.cpp server on Mac) plus the local-whisperx sentinel.
# Only consumed when ASR_BACKEND=router.
# Example:
#   ASR_HOSTS=http://localhost:8001,http://mbp.tailnet.ts.net:8001,local-whisperx
ASR_HOSTS=

# HuggingFace model ID passed to OpenAI-compat backends. Ignored for
# local-whisperx (which uses WHISPER_MODEL).
ASR_MODEL_ID=Systran/faster-whisper-large-v3

# Health-check timeout per backend (seconds).
ASR_HEALTHCHECK_TIMEOUT_S=2.0

# --- Tailscale sidecar (docker-compose.gpu.yml only) ---
# Reusable auth key with tag:transcribe-svc pre-approved. Generate at
# https://login.tailscale.com/admin/settings/keys — check "Reusable" and
# "Pre-approved" and add tag:transcribe-svc.
TS_AUTHKEY=
```

- [ ] **Step 4: Update `tests/conftest.py`** (add near the other env defaults, before app imports)

```python
os.environ.setdefault("ASR_BACKEND", "whisperx")
```

- [ ] **Step 5: Verify existing tests still pass**

Run:

```bash
docker compose run --rm transcribe-svc pytest tests/ -v 2>&1 | tail -30
```

Expected: all existing tests + the 12 new ones from Tasks 3, 5, 6 pass. Total ≈ 34 tests.

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/pipeline.py .env.example tests/conftest.py
git commit -m "$(cat <<'EOF'
Phase 3: wire ASR_BACKEND / ASR_HOSTS into pipeline factory

ASR_BACKEND=whisperx keeps Phase 2 behavior (LocalWhisperXASR alone,
default). ASR_BACKEND=router parses ASR_HOSTS as a priority list of
OpenAI-compat URLs plus the local-whisperx sentinel and wraps them in
an ASRRouter with health-checked fallback.

Also carves out TS_AUTHKEY as a first-class setting so the tailscale
sidecar's auth key sits alongside the API_TOKEN in .env.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Rewrite `docker-compose.gpu.yml` with three-service topology

**Files:**
- Modify: `docker-compose.gpu.yml` (full rewrite)

- [ ] **Step 1: Replace `docker-compose.gpu.yml`**

```yaml
# GPU deployment for Alienware (Windows 11 + WSL2 + Docker Desktop + RTX 5090).
#
# Three-container stack. All three share the tailscale sidecar's network
# namespace, so:
#   - transcribe-svc is reachable at https://transcribe-svc.<tailnet>.ts.net
#   - speaches binds 127.0.0.1:8001 (transcribe-svc only; not tailnet-visible)
#   - No host port bindings anywhere.
#
# Bring up: docker compose -f docker-compose.gpu.yml up -d
#
# Prereqs (docs/DEPLOY.md, Windows section):
#   - NVIDIA driver v566+ on the Windows host (Blackwell / WSL2 support)
#   - Windows power settings: never sleep, lid close = do nothing
#   - Docker Desktop with WSL2 backend + Ubuntu-22.04 integration
#   - GPU verify: `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi`
#   - Tailscale reusable auth key with tag:transcribe-svc → TS_AUTHKEY in .env

name: transcribe-svc

services:
  tailscale:
    image: tailscale/tailscale:latest
    container_name: transcribe-svc-tailscale
    hostname: transcribe-svc
    environment:
      TS_AUTHKEY: ${TS_AUTHKEY}
      TS_STATE_DIR: /var/lib/tailscale
      TS_EXTRA_ARGS: --advertise-tags=tag:transcribe-svc --accept-dns=true
      TS_USERSPACE: "false"
    volumes:
      - tailscale_state:/var/lib/tailscale
      - /dev/net/tun:/dev/net/tun
    cap_add:
      - NET_ADMIN
      - NET_RAW
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "tailscale", "status", "--json"]
      interval: 30s
      timeout: 5s
      start_period: 30s
      retries: 3

  speaches:
    image: ghcr.io/speaches-ai/speaches:latest-cuda
    container_name: transcribe-svc-speaches
    depends_on:
      tailscale:
        condition: service_healthy
    network_mode: service:tailscale
    environment:
      # Speaches binds to 8001 inside the shared netns; only transcribe-svc
      # reaches it via localhost. Do NOT expose on 0.0.0.0.
      UVICORN_HOST: 127.0.0.1
      UVICORN_PORT: "8001"
      WHISPER__MODEL: ${ASR_MODEL_ID:-Systran/faster-whisper-large-v3}
      WHISPER__COMPUTE_TYPE: int8_float16
      # Turn off any usage telemetry that ships enabled. Verify at deploy.
      SPEACHES_TELEMETRY: "false"
    volumes:
      - speaches_models:/home/ubuntu/.cache/huggingface
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8001/v1/models"]
      interval: 30s
      timeout: 5s
      start_period: 120s
      retries: 3

  transcribe-svc:
    container_name: transcribe-svc
    build:
      context: .
      dockerfile: docker/Dockerfile.cpu
    image: transcribe-svc:cpu
    depends_on:
      tailscale:
        condition: service_healthy
      speaches:
        condition: service_healthy
    network_mode: service:tailscale
    env_file:
      - .env
    environment:
      ASR_BACKEND: router
      ASR_HOSTS: "http://127.0.0.1:8001,local-whisperx"
    volumes:
      - models:/data/models
      - uploads:/data/uploads
      - outputs:/data/outputs
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8000/v1/health"]
      interval: 30s
      timeout: 5s
      start_period: 120s
      retries: 3

volumes:
  tailscale_state:
  speaches_models:
  models:
  uploads:
  outputs:
```

- [ ] **Step 2: Validate compose syntax**

Run:

```bash
cd /home/transcriber/Github/transcriberproject && docker compose -f docker-compose.gpu.yml config --quiet
```

Expected: no output, exit 0. If it errors, fix and retry.

- [ ] **Step 3: Retire the unused `docker/Dockerfile.gpu` scaffold**

The GPU story is now "Speaches sidecar owns CUDA; transcribe-svc uses its existing CPU image." The old `Dockerfile.gpu` cuda:11.8 scaffold is no longer part of the plan.

```bash
git rm docker/Dockerfile.gpu
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.gpu.yml
git commit -m "$(cat <<'EOF'
Phase 3: rewrite docker-compose.gpu.yml with tailscale + speaches sidecars

Three services share the tailscale container's netns so the whole stack
appears as a single tailnet node (transcribe-svc.<tailnet>.ts.net) with
no host port bindings. Speaches binds 127.0.0.1:8001 so it's only
reachable from transcribe-svc, not on the tailnet.

Retires docker/Dockerfile.gpu (cuda:11.8 scaffold) — Speaches owns the
CUDA stack now, transcribe-svc keeps its existing CPU image.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Update `docs/DEPLOY.md` and `docs/API.md`

**Files:**
- Modify: `docs/DEPLOY.md`
- Modify: `docs/API.md`

- [ ] **Step 1: Add "Alienware GPU (Windows 11 + WSL2)" section to `docs/DEPLOY.md`**

Read the current `docs/DEPLOY.md`, then append (before any existing "See also" / footer):

```markdown
---

## Deploying to the Alienware (Windows 11 + WSL2 + Docker Desktop + RTX 5090)

This is the Phase 3 GPU path. Uses `docker-compose.gpu.yml` — three
containers (tailscale sidecar, Speaches ASR on the GPU, transcribe-svc
on CPU).

### Host prerequisites

1. **NVIDIA driver v566+** (Blackwell / WSL2 support). Install from
   `nvidia.com/drivers`, reboot.
2. **Windows 11 22H2+**, fully patched. Virtualization enabled in BIOS.
3. **WSL2 + Ubuntu 22.04.** In an admin PowerShell:
   ```powershell
   wsl --install -d Ubuntu-22.04
   wsl --set-default-version 2
   ```
4. **Docker Desktop** with WSL2 backend + Ubuntu-22.04 integration.
   Settings → Resources → WSL Integration → enable for Ubuntu-22.04.
5. **Power settings — this is the one that will bite you if you skip it:**
   - Settings → System → Power & battery → Screen and sleep:
     "Never" for both plugged-in options.
   - Advanced power → When I close the lid: "Do nothing" (battery + plugged in).
6. **Defender Firewall:** allow Docker Desktop on both private and public profiles.

### Verify GPU passthrough

In a WSL2 Ubuntu shell:

```bash
docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
```

Must show the RTX 5090. If this fails, stop — nothing downstream will work.
Common causes: driver too old (need v566+), WSL2 kernel out of date
(`wsl --update`), or Docker Desktop's GPU support not enabled.

### Tailscale prep (one-time, in the admin console)

1. Confirm `tag:transcribe-svc` and `tag:transcribe-client` are in the ACL policy.
2. Generate a **reusable, pre-authorized** auth key at
   [Settings → Keys](https://login.tailscale.com/admin/settings/keys):
   - "Reusable" checked
   - "Pre-approved" checked
   - Tag: `tag:transcribe-svc`
3. Save the key; you'll paste it into `.env` next.

### Deploy

```bash
# Inside WSL2 Ubuntu, in the repo root:
cp .env.example .env
# Edit .env — set:
#   API_TOKEN (generate: python3 -c "import secrets; print(secrets.token_urlsafe(32))")
#   TS_AUTHKEY (paste the reusable auth key from above)
#   HF_TOKEN (HuggingFace token with pyannote/speaker-diarization-3.1 accepted)

docker compose -f docker-compose.gpu.yml up -d
docker compose -f docker-compose.gpu.yml logs -f tailscale       # watch the node come up
docker compose -f docker-compose.gpu.yml logs -f speaches        # watch the model download
docker compose -f docker-compose.gpu.yml logs -f transcribe-svc  # watch the app come up
```

Once all three are healthy, from any tagged `transcribe-client` host:

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
     https://transcribe-svc.<your-tailnet>.ts.net/v1/health
# {"status":"ok","device":"cpu","compute_type":"int8","gpu":false}
```

(That `device: cpu` refers to the **local** WhisperX fallback backend
inside transcribe-svc; Speaches is doing the actual GPU inference and
reports separately in `asr_backend` on each transcription response.)

### First-transcribe smoke test

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
     -F "audio=@tests/fixtures/short_two_speaker.wav" \
     -F "title=alienware-smoke" \
     https://transcribe-svc.<your-tailnet>.ts.net/v1/transcribe
```

Expected: HTTP 200 with `transcript_txt_url` and `transcript_json_url`.
Fetch the `.json` and check `"asr_backend": "speaches@http://127.0.0.1:8001"`.

### Troubleshooting

- **Speaches container OOMs on model download** — first run downloads
  ~3 GB of weights into the `speaches_models` volume. If the WSL2
  Ubuntu instance is memory-capped (`.wslconfig`), raise the memory
  cap and restart WSL: `wsl --shutdown`, reopen.
- **Speaches health check keeps timing out** — model load takes 30–90 s
  on first startup. Increase the `start_period` in `docker-compose.gpu.yml`.
- **Requests hang for minutes** — likely the fallback is silently
  running local-whisperx on CPU. Check `docker compose logs speaches` — if
  Speaches isn't ready, transcribe-svc will fall through to the CPU tier.
- **Windows updates rebooted the machine** — Docker Desktop will restart
  and containers with `restart: unless-stopped` come back automatically.
  If they don't, `docker compose -f docker-compose.gpu.yml up -d` again.
```

- [ ] **Step 2: Add ASR backend note to `docs/API.md`**

Add a short section (place after any existing "Environment" or "Deployment" section, or near the end):

```markdown
## ASR backend selection

`transcribe-svc` supports two ASR execution modes, chosen at startup via
env `ASR_BACKEND`:

- **`ASR_BACKEND=whisperx`** (default) — in-container WhisperX. Runs on
  CPU. Phase 2 behavior. Used on the Proxmox VM.
- **`ASR_BACKEND=router`** — health-checked fallback chain over a priority
  list from `ASR_HOSTS`. URL entries are treated as OpenAI-compat servers
  (`POST {base}/v1/audio/transcriptions`); the sentinel `local-whisperx`
  routes to in-container WhisperX as the last-resort tier.

Example (Alienware GPU deployment):

```
ASR_BACKEND=router
ASR_HOSTS=http://127.0.0.1:8001,http://mbp.tailnet.ts.net:8001,local-whisperx
ASR_MODEL_ID=Systran/faster-whisper-large-v3
```

The backend that served each request is reported in the response body's
`asr_backend` field and in the `.json` transcript header — useful when
diagnosing fallback drift.

Diarization always runs in-container via pyannote; the ASR backend only
affects speech-to-text.
```

- [ ] **Step 3: Commit**

```bash
git add docs/DEPLOY.md docs/API.md
git commit -m "$(cat <<'EOF'
Phase 3: docs — Alienware/Windows/WSL2 deploy section + ASR_BACKEND note

DEPLOY.md gets a full step-by-step for standing up the three-container
GPU stack on Windows 11 + WSL2, including the power-settings gotcha,
GPU passthrough verify, Tailscale auth-key prep, and troubleshooting.

API.md documents the ASR_BACKEND toggle and how asr_backend surfaces in
transcript responses so consumers can tell which tier answered.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Operator acceptance run on the Alienware

This is the point where the operator pulls down the branch on the Alienware and runs the deploy. Everything on my side is code-complete and unit-tested inside the container.

- [ ] **Step 1: Operator pulls `phase-3-gpu` on the Alienware (in WSL2 Ubuntu shell)**

```bash
git clone https://github.com/wags878/transcriberproject.git
cd transcriberproject
git checkout phase-3-gpu
```

(Or, if the repo is already cloned there: `git fetch origin && git checkout phase-3-gpu && git pull`.)

- [ ] **Step 2: Operator completes Windows/WSL2/driver/Tailscale prereqs**

Follows `docs/DEPLOY.md` "Deploying to the Alienware" section end to end.
Explicitly:

- NVIDIA driver v566+
- Windows power settings (never sleep, lid = do nothing)
- WSL2 + Ubuntu-22.04
- Docker Desktop with WSL2 backend
- GPU verify: `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi`
- Tailscale reusable auth key with tag:transcribe-svc

- [ ] **Step 3: Operator sets `.env`**

```bash
cp .env.example .env
```

Edit `.env`:
- `API_TOKEN` — fresh strong token
- `TS_AUTHKEY` — reusable pre-authorized key from Tailscale admin console
- `HF_TOKEN` — HuggingFace token with `pyannote/speaker-diarization-3.1`
  and `pyannote/segmentation-3.0` conditions accepted (see B-004)
- `DIARIZATION_MODEL=pyannote/speaker-diarization-3.1` (matches B-004
  resolution — community-1 doesn't load in pyannote.audio 3.1.1)
- `ASR_BACKEND=router` is already the default in `docker-compose.gpu.yml`

- [ ] **Step 4: Operator brings the stack up**

```bash
docker compose -f docker-compose.gpu.yml up -d
```

Watch each log until healthy:

```bash
docker compose -f docker-compose.gpu.yml logs -f tailscale       # look for "Success."
docker compose -f docker-compose.gpu.yml logs -f speaches        # look for model download + "Uvicorn running"
docker compose -f docker-compose.gpu.yml logs -f transcribe-svc  # look for "Pipeline loaded"
```

Expected first-run duration: 5–15 min while Speaches downloads
`Systran/faster-whisper-large-v3` (~3 GB) into `speaches_models`.

- [ ] **Step 5: Acceptance criteria — run each check, report which pass**

Adapted from `docs/superpowers/specs/2026-07-12-phase-3-gpu-speaches-design.md` §8:

- [ ] `docker compose -f docker-compose.gpu.yml ps` shows all three containers `healthy`.
- [ ] From a `transcribe-client`-tagged host on the tailnet:
      `curl -H "Authorization: Bearer $API_TOKEN" https://transcribe-svc.<tailnet>.ts.net/v1/health`
      returns HTTP 200 with the documented JSON.
- [ ] `nvidia-smi` on the Alienware host shows the `speaches` process holding <4 GB VRAM after model load.
- [ ] `curl -H "Authorization: Bearer $API_TOKEN" -F "audio=@tests/fixtures/short_two_speaker.wav" -F "title=alienware-smoke" https://transcribe-svc.<tailnet>.ts.net/v1/transcribe`
      returns HTTP 200; fetched `.json` shows `"asr_backend": "speaches@http://127.0.0.1:8001"`.
- [ ] Same on `tests/fixtures/five_minute.wav`. Wall clock ≤ 90 s (target ≤ 60 s).
- [ ] Kill Speaches (`docker stop transcribe-svc-speaches`) and re-run the
      short fixture. Response still HTTP 200; `.json` shows
      `"asr_backend": "local-whisperx"`. Restart Speaches after test.
- [ ] Speaker labels attributed to two distinct speakers on the 39.5 s fixture.
- [ ] `/v1/admin/storage` returns the documented shape unchanged.

- [ ] **Step 6: Operator or I write the Phase 3 close-out entry to `docs/STATUS.md`**

Once acceptance criteria pass, append a Phase 3 close-out block to
`docs/STATUS.md` with:

- What shipped (Speaches sidecar, tailscale-in-container, router,
  stitch, backend attribution in outputs)
- Perf numbers observed (fixture wall times, VRAM peak)
- Deferred items (pyannote-on-GPU, on-device iPhone, Jetson edge)
- Any blockers hit and how they were resolved (log B-005+ if applicable)

Then commit and open a PR from `phase-3-gpu` → `main`.

```bash
git add docs/STATUS.md
git commit -m "$(cat <<'EOF'
Phase 3: close-out status — acceptance met on Alienware

<details on perf, VRAM, and any observed detours>

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin phase-3-gpu
gh pr create --base main --head phase-3-gpu --title "Phase 3: GPU acceleration via Speaches sidecar" --body "See docs/superpowers/specs/2026-07-12-phase-3-gpu-speaches-design.md"
```

---

## Post-plan review — spec ↔ plan coverage

Quick check against `docs/superpowers/specs/2026-07-12-phase-3-gpu-speaches-design.md`:

| Spec §  | Requirement                                        | Task(s)    |
|---------|----------------------------------------------------|------------|
| 3       | Three-service topology, shared netns                | 8          |
| 3       | Speaches binds 127.0.0.1:8001                       | 8          |
| 4       | Concurrent pyannote + ASR via asyncio.gather       | 4          |
| 4       | Stitcher joins turns onto ASR segments              | 3, 4       |
| 5       | ASR_HOSTS fallback chain (Speaches → MBP → local)  | 5, 6, 7    |
| 5       | Health-checked first-healthy-wins                   | 6          |
| 6       | Unchanged public HTTP surface                       | 4 (pipeline preserves dict shape) |
| 6       | asr_backend surfaced in .json header                | 4          |
| 7       | Windows/WSL2 prereqs, GPU verify, Tailscale prep    | 9 (docs), 10 (execution) |
| 8       | Acceptance criteria checklist                       | 10         |
| 9       | PHI constraint (SPEACHES_TELEMETRY=false, netns isolation) | 8 |
| 10      | Rollback path (all work on branch)                  | Entire plan lives on phase-3-gpu; main untouched |

No gaps.
