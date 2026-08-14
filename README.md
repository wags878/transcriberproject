# transcribe-svc

A privately-hosted HTTP service that accepts audio recordings, transcribes them (WhisperX / faster-whisper), identifies speakers via pyannote diarization, and writes labeled `.txt` + structured `.json` to disk. Output is intended for upload into a Claude Project for downstream analysis. A small installable web client (PWA) is served from the same origin.

**This is a proprietary project.** See `LICENSE`. Upstream component attributions are in `NOTICE`.

## Status

**Picking up cold?** Start with **`docs/HANDOFF.md`** — current state, how to run, and the next task.

The GPU transcription stack runs on the Alienware host (Windows 11 + Docker
Desktop, RTX 5090), rebuilt and verified **2026-08-14** at ~10.9× realtime.
Verify before assuming it's up — `docker compose -f docker-compose.gpu.yml ps`
should show four healthy containers; an empty result means a cold rebuild, not a
restart. Served over HTTPS on the tailnet at
**`https://transcribe-svc-1.example-tailnet.ts.net`** (mic capture + PWA install work).
The `-1` in that hostname is permanent — see `docs/HANDOFF.md`.

The current worktree also contains a **Codex-built, provider-neutral OIDC bridge**
with static/hybrid/OIDC modes; it is tested but has not yet been configured
against a real Cognito user pool or deployed. See `docs/AUTH.md` for that rollout
and `docs/STATUS.md` for the running phase log.

The PWA client adds an **Audio language** selector (default English), an **Output** selector (*Same as audio* / *English translate*), and post-transcription **speaker editing** (rename / reassign). This is a **patient-owned** recorder for any patient↔provider interaction, not just therapy — role labels are generic.

## Quick start (GPU stack — current deployment)

1. Set up `.env`:
   ```sh
   cp .env.example .env
   # leave AUTH_MODE=static initially; set API_TOKEN
   # (python3 -c "import secrets; print(secrets.token_urlsafe(32))"),
   #      HF_TOKEN (pyannote conditions accepted), TS_AUTHKEY (reusable, tag:transcribe-svc),
   #      DIARIZATION_MODEL=pyannote/speaker-diarization-3.1
   ```
2. Bring up the stack:
   ```sh
   docker compose -f docker-compose.gpu.yml up -d --build
   ```
3. Open the **web client** at <http://localhost:8000> or the tailnet HTTPS URL.
   In static mode, paste the API token in Settings. In hybrid/OIDC mode, use
   **Sign in**; see `docs/AUTH.md` before changing modes.
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
    ├── AUTH.md               OIDC/Cognito bridge and rollout
    ├── STATUS.md             phase-by-phase log
    ├── API.md                contract reference
    ├── DEPLOY.md             bring-up (VM + Alienware GPU)
    └── superpowers/          design specs + execution plans
```
