# transcribe-svc

A privately-hosted HTTP service that accepts audio recordings, transcribes them (WhisperX / faster-whisper), identifies speakers via pyannote diarization, and writes labeled `.txt` + structured `.json` to disk. Output is intended for upload into a Claude Project for downstream analysis. A small installable web client (PWA) is served from the same origin.

**This is a proprietary project.** See `LICENSE`. Upstream component attributions are in `NOTICE`.

## Status

**Picking up cold?** Start with **`docs/HANDOFF.md`** — current state, how to run, and the next task.

All work is **merged to `main`** and **deployed on GPU**. Runs as a **four-container** stack on the Alienware host (Windows 11 + WSL2 + Docker Desktop, RTX 5090): a Tailscale sidecar, **Speaches** (`faster-whisper-large-v3` ASR on GPU), a **`diarize-svc`** pyannote diarization sidecar (GPU), and the FastAPI orchestrator. ASR and diarization are each health-checked with **per-request CPU fallback**, so a GPU/sidecar outage degrades speed but never fails a job. Served over **HTTPS on the tailnet** (`tailscale serve`, valid Let's Encrypt cert). Throughput ~8.7–10.9× realtime. See `docs/STATUS.md` for the running phase log and `docs/BLOCKERS.md` for open issues.

The PWA client adds an **Audio language** selector (default English), an **Output** selector (*Same as audio* / *English translate*), and post-transcription **speaker editing** (rename / reassign). This is a **patient-owned** recorder for any patient↔provider interaction, not just therapy — role labels are generic.

## Quick start (GPU stack — current deployment)

1. Set up `.env`:
   ```sh
   cp .env.example .env
   # set: API_TOKEN (python3 -c "import secrets; print(secrets.token_urlsafe(32))"),
   #      HF_TOKEN (pyannote conditions accepted), TS_AUTHKEY (reusable, tag:transcribe-svc),
   #      DIARIZATION_MODEL=pyannote/speaker-diarization-3.1
   ```
2. Bring up the stack:
   ```sh
   docker compose -f docker-compose.gpu.yml up -d --build
   ```
3. Open the **web client** at <http://localhost:8000> (host loopback), or over the tailnet at **`https://transcribe-svc.<your-tailnet>.ts.net`** (HTTPS, no port — required for mic recording + PWA install on iPhone; enable once with `docker compose -f docker-compose.gpu.yml exec tailscale tailscale serve --bg 8000`). Paste your token in ⚙ Settings, then drop in an audio file / record / click a sample.
4. Or hit the API directly:
   ```sh
   TOKEN=$(grep ^API_TOKEN .env | cut -d= -f2-)
   curl -H "Authorization: Bearer $TOKEN" \
        -F "audio=@samples/friendly_conversation.mp3" \
        -F "title=demo" \
        http://localhost:8000/v1/transcribe
   ```

**Accepted audio:** anything ffmpeg can decode — WAV, MP3, M4A/AAC, FLAC, OGG, Opus, WebM, and audio inside MP4/MOV/MKV. Max upload 500 MB (`MAX_UPLOAD_MB`).

**CPU-only (no GPU):** `docker compose up -d` uses the CPU profile (`ASR_BACKEND=whisperx`, in-container WhisperX).

Full API contract: `docs/API.md`. Deployment (incl. the Windows/WSL2 GPU setup): `docs/DEPLOY.md`.

## Architecture

`transcribe()` runs ASR (any `ASRBackend`) and diarization (`Diarizer` local CPU, or `RemoteDiarizer` → the GPU `diarize-svc` sidecar) concurrently (`asyncio.gather`), then `stitch_speakers()` joins turns onto ASR segments — see `app/pipeline.py`, `app/asr.py`, `app/diarize.py`, `app/stitch.py`. Backend selection: `ASR_BACKEND`/`ASR_HOSTS` for speech-to-text, `DIARIZE_BACKEND`/`DIARIZE_URL` for diarization (both documented in `docs/API.md`). Offline ML tooling (eval harness, voice enrollment) lives under `ml/` and only touches the service over HTTP.

Designs and execution plans live in `docs/superpowers/specs/` and `docs/superpowers/plans/`; the running log is `docs/STATUS.md`.

## Project layout

```
transcribe-svc/
├── README.md                 (this file)
├── NOTICE                    upstream component attributions
├── LICENSE                   proprietary, all rights reserved
├── .env.example              copy to .env; never commit .env
├── docker-compose.yml        default = CPU profile (old-server fallback)
├── docker-compose.cpu.yml    explicit CPU overlay
├── docker-compose.gpu.yml    GPU stack (tailscale + speaches + diarize-svc + transcribe-svc)
├── docker/
│   ├── Dockerfile.cpu        the service image (used by all profiles)
│   └── entrypoint.sh
├── diarize-svc/              GPU diarization sidecar (FastAPI + pyannote, cu128)
├── app/                      FastAPI service code
│   └── static/               installable PWA web client (served at /)
├── ml/                       offline ML tooling (eval harness, voice enrollment)
├── samples/                  synthetic demo clips (served at /samples)
├── tests/                    pytest suite
└── docs/
    ├── HARDWARE.md           host facts
    ├── BLOCKERS.md           open issues
    ├── STATUS.md             phase-by-phase log
    ├── API.md                contract reference
    ├── DEPLOY.md             bring-up (VM + Alienware GPU)
    └── superpowers/          design specs + execution plans
```
