# API contract

Locked early so all clients (PWA today, M5 device tomorrow) can rely on it.
Source-of-truth: `PROJECT_PLAN.md` §4. This file is the operator-facing reference.

Base URL inside the tailnet (Phase 1): `http://<vm-tailnet-ip>:8000`

All authenticated routes require:

```
Authorization: Bearer <API_TOKEN>
```

`/v1/health` is intentionally **unauthenticated** so monitoring and the Docker `HEALTHCHECK` work without a token.

---

## `GET /v1/health`

No auth.

```json
{
  "status": "ok",
  "device": "cpu",
  "compute_type": "int8",
  "gpu": false
}
```

`device` is whatever `WHISPERX_DEVICE` is set to. `gpu` is `true` iff `device == "cuda"`.

---

## `POST /v1/transcribe`

Auth: required.

Content-Type: `multipart/form-data`.

| Field | Type | Required | Description |
|---|---|---|---|
| `audio` | file | yes | WAV, MP3, M4A, or FLAC. Max size = `MAX_UPLOAD_MB` (default 500). |
| `title` | string | no | Used to build the on-disk filename (slugified). Default is `untitled`. |
| `num_speakers` | int | no | Hint for diarization. Omit to auto-detect. |
| `language` | string | no | ISO-639-1 code (`en`, `es`, …). Omit to auto-detect. |

**Response 200:**
```json
{
  "id": "5b2f2e1b-9c92-4a7d-b9b6-c0d2eb6c0a52",
  "transcript_txt_url": "/v1/results/5b2f2e1b-.../transcript.txt",
  "transcript_json_url": "/v1/results/5b2f2e1b-.../transcript.json",
  "speakers_detected": 2,
  "duration_seconds": 1834.5,
  "language": "en"
}
```

**Errors:**
| Status | Meaning |
|---|---|
| 400 | Missing `audio` field |
| 401 | Missing / invalid bearer token |
| 413 | Upload exceeds `MAX_UPLOAD_MB` |
| 500 | Pipeline failure (see container logs) |

> **Note on long requests.** Transcription is synchronous — the response is
> sent only when processing completes. On the current 16-vCPU / 15.6 GiB
> Xeon-Silver-passthrough VM, a 60-minute recording stays open for ~2–3 hours.
> Set generous client timeouts.

---

## `GET /v1/results/{id}/transcript.txt`

Auth: required.

Plain text, UTF-8. One paragraph per speaker turn (consecutive same-speaker
WhisperX segments are merged into a single paragraph). Two newlines between
turns. The `[mm:ss]` prefix is the start time of the turn.

```
[00:00] SPEAKER_00: Welcome, please come in. Have a seat — how have you been since last week?

[00:09] SPEAKER_01: Thanks. So this week was hard. I had a really rough Tuesday.
```

Files are stored on disk as `<YYYY-MM-DD>_<HHMM>_<title-slug>_<short-uuid>.{txt,json}`
for human readability when browsing the outputs volume directly. URLs remain
`/v1/results/{id}/transcript.{txt,json}` and are stable regardless of on-disk
filename.

`404` if the id is unknown.

---

## `GET /v1/results/{id}/transcript.json`

Auth: required.

```json
{
  "id": "...",
  "created_at": "2026-04-30T18:42:11+00:00",
  "duration_seconds": 1834.5,
  "language": "en",
  "speakers_detected": 2,
  "model": "large-v3",
  "compute_type": "int8",
  "device": "cpu",
  "diarization_model": "pyannote/speaker-diarization-3.1",
  "segments": [
    {
      "start": 0.0,
      "end": 3.2,
      "text": "Welcome, please come in.",
      "speaker": "SPEAKER_00",
      "words": [ ... per-word timings ... ]
    }
  ]
}
```

`404` if the id is unknown.

---

## `GET /v1/admin/storage`

Auth: required.

```json
{
  "uploads_mb": 421.3,
  "outputs_mb": 12.7,
  "models_mb": 1583.4
}
```

Sizes of the corresponding container volumes.
