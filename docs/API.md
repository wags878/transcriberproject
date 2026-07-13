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
| `language` | string | no | Source-audio ISO-639-1 code (`en`, `es`, …). Omit to auto-detect. |
| `task` | string | no | `transcribe` (default) → output in the source language; `translate` → output in **English**. Whisper only translates X→English; other output languages are not supported natively. |

**Response 200:**
```json
{
  "id": "5b2f2e1b-9c92-4a7d-b9b6-c0d2eb6c0a52",
  "transcript_txt_url": "/v1/results/5b2f2e1b-.../transcript.txt",
  "transcript_json_url": "/v1/results/5b2f2e1b-.../transcript.json",
  "speakers_detected": 2,
  "duration_seconds": 1834.5,
  "language": "en",
  "task": "transcribe",
  "output_language": "en"
}
```

`language` is the detected **source** language; `output_language` is the language
of the transcript text (`en` whenever `task=translate`).

**Errors:**
| Status | Meaning |
|---|---|
| 400 | Missing `audio` field, or `task` not in {`transcribe`,`translate`} |
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
  "task": "transcribe",
  "output_language": "en",
  "speakers_detected": 2,
  "model": "large-v3",
  "compute_type": "int8",
  "device": "cpu",
  "diarization_model": "pyannote/speaker-diarization-3.1",
  "diarize_device": "cuda",
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

## `POST /v1/results/{id}/relabel`

Auth: required. Content-Type: `application/json`.

Manually overwrite speaker labels for a completed transcript — the
always-available override for imperfect auto-labeling (anonymous
`SPEAKER_00/01`, or the enrolled `Therapist`/`Client` from role labeling). The
client sends **one final speaker label per segment, in order**:

```json
{ "speakers": ["Therapist", "Therapist", "Client", "Therapist"] }
```

The server applies the labels, recomputes `speakers_detected`, and re-renders +
persists both the stored `.txt` and `.json`, so downloads and history reflect the
edit. A blank/whitespace label becomes `SPEAKER_??`.

**Response 200:** the full updated transcript JSON (same shape as
`GET .../transcript.json`).

**Errors:**
| Status | Meaning |
|---|---|
| 400 | `speakers` length ≠ number of segments |
| 401 | Missing / invalid bearer token |
| 404 | Unknown id |

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

---

## ASR backend selection

`transcribe-svc` supports two ASR execution modes, chosen at startup via env
`ASR_BACKEND`:

- **`ASR_BACKEND=whisperx`** (default) — in-container WhisperX. Runs on CPU.
  Phase 2 behavior. Used on the Proxmox VM.
- **`ASR_BACKEND=router`** — health-checked fallback chain over a priority list
  from `ASR_HOSTS`. URL entries are treated as OpenAI-compat servers
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

## Diarization backend selection

Diarization (pyannote) is the pipeline's dominant cost and is chosen at startup
via env `DIARIZE_BACKEND`:

- **`DIARIZE_BACKEND=local`** (default) — in-process pyannote on **CPU**. Phase
  2/3 behavior; the only option where transcribe-svc's frozen torch pin applies.
- **`DIARIZE_BACKEND=remote`** — offload to the **GPU `diarize-svc` sidecar**
  (`DIARIZE_URL`, e.g. `http://127.0.0.1:8002`), a separate container with a
  modern Blackwell-capable CUDA torch + pyannote. Per request it health-checks
  the sidecar and **falls back to in-process CPU pyannote** if it's unreachable,
  so a GPU/sidecar outage degrades speed but never fails a job.

The device that actually ran diarization is reported in the `.json` header's
`diarize_device` (`cuda`, `cpu`, or `cpu-fallback`). The GPU compose
(`docker-compose.gpu.yml`) wires `remote` automatically; the sidecar mirrors the
Speaches split (shared tailscale netns, binds `127.0.0.1:8002`, never
tailnet-published).

---

## Accepted audio formats

`POST /v1/transcribe` has **no format allowlist** — the upload is decoded by
ffmpeg (used by both ASR backends and the diarizer), so anything ffmpeg can
decode is accepted: WAV, MP3, M4A/AAC, FLAC, OGG/Vorbis, Opus, WMA, WebM/Opus,
and audio inside video containers (MP4/MOV/MKV). The only hard limits are a
required filename (`400` if missing) and `MAX_UPLOAD_MB` (default 500 → `413`).
An undecodable/corrupt file currently surfaces as a generic `500`.

---

## Web client (PWA)

An installable single-page client is served from the same origin at `/`
(`app/static/`), with synthetic demo clips at `/samples`. It holds the bearer
token in `localStorage` and calls the same `/v1/*` endpoints — the API stays
auth-gated. Reach it at `http://localhost:8000` (host loopback, published by the
GPU compose) or over the tailnet. See `docs/DEPLOY.md`.

Client features: an **Audio language** selector (defaults to English so an
unfiltered intro can't silently mis-detect the language — set it to *Auto-detect*
for non-English audio), an **Output** selector (*Same as audio* or *English
(translate)*; arbitrary target languages are a planned translation-model
follow-up), and **✎ Edit speakers** on a result — rename a speaker across
the whole transcript or reassign an individual turn, saved via
`POST /v1/results/{id}/relabel`. The service worker is network-first **and**
fetches shell assets with `cache: "no-store"`, so a redeploy is picked up on the
next load without a manual hard-refresh.
