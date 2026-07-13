# transcribe-svc

A privately-hosted HTTP service that accepts audio recordings, transcribes them (WhisperX / faster-whisper), identifies speakers via pyannote diarization, and writes labeled `.txt` + structured `.json` to disk. Output is intended for upload into a Claude Project for downstream analysis. A small installable web client (PWA) is served from the same origin.

**This is a proprietary project.** See `LICENSE`. Upstream component attributions are in `NOTICE`.

## Status

Phase 3 complete and **deployed on GPU**. Runs as a three-container stack on the Alienware host (Windows 11 + WSL2 + Docker Desktop, RTX 5090): a Tailscale sidecar, **Speaches** serving `faster-whisper-large-v3` on the GPU, and the FastAPI service doing diarization + orchestration on CPU. ASR is health-checked with a fallback to in-container WhisperX (CPU). See `docs/STATUS.md` for the running phase log and `docs/BLOCKERS.md` for open issues.

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
3. Open the **web client** at <http://localhost:8000> (published on the host loopback), or over the tailnet at `http://transcribe-svc.<your-tailnet>.ts.net:8000`. Paste your token in ⚙ Settings, then drop in an audio file / record / click a sample.
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

`transcribe()` runs ASR (any `ASRBackend`) and pyannote diarization concurrently (`asyncio.gather`), then `stitch_speakers()` joins turns onto ASR segments — see `app/pipeline.py`, `app/asr.py`, `app/diarize.py`, `app/stitch.py`. Backend selection is `ASR_BACKEND` / `ASR_HOSTS` (see `docs/API.md`).

Designs and execution plans live in `docs/superpowers/specs/` and `docs/superpowers/plans/`; the running log is `docs/STATUS.md`.

## Project layout

```
transcribe-svc/
├── README.md                 (this file)
├── NOTICE                    upstream component attributions
├── LICENSE                   proprietary, all rights reserved
├── .env.example              copy to .env; never commit .env
├── docker-compose.yml        default = CPU profile
├── docker-compose.cpu.yml    explicit CPU overlay
├── docker-compose.gpu.yml    GPU stack (tailscale + speaches + transcribe-svc)
├── docker/
│   ├── Dockerfile.cpu        the image (used by all profiles)
│   └── entrypoint.sh
├── app/                      FastAPI service code
│   └── static/               installable PWA web client (served at /)
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
