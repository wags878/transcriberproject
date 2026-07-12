# transcribe-svc

A privately-hosted HTTP service that accepts audio recordings, transcribes them with WhisperX, identifies speakers via pyannote diarization, and writes labeled `.txt` + structured `.json` to disk. Output is intended for upload into a Claude Project for downstream analysis.

**This is a proprietary project.** See `LICENSE`. Upstream component attributions are in `NOTICE`.

## Status

Greenfield as of 2026-04-30. Phase 1 (FastAPI + WhisperX containerized service) is in progress. See `docs/STATUS.md` for the running phase log and `docs/BLOCKERS.md` for any open issues.

## Quick start

1. Copy env template and set a real token:
   ```sh
   cp .env.example .env
   # edit .env: set API_TOKEN to the output of:
   #   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Bring it up (CPU profile is the default):
   ```sh
   docker compose up -d
   ```
3. Verify health:
   ```sh
   curl http://localhost:8000/v1/health
   ```
4. Transcribe an audio file:
   ```sh
   TOKEN=$(grep ^API_TOKEN .env | cut -d= -f2)
   curl -H "Authorization: Bearer $TOKEN" \
        -F "audio=@/path/to/recording.wav" \
        -F "title=therapy-2026-04-30" \
        http://localhost:8000/v1/transcribe
   ```

The full API contract is in `docs/API.md`. Deployment notes are in `docs/DEPLOY.md`.

## Architecture

See `/home/transcriber/Github/prompts/PROJECT_PLAN.md` for the architectural source of truth (§4 has the API contract diagram). The execution plan is at `/home/transcriber/.claude/plans/okay-now-that-we-frolicking-diffie.md`.

## Project layout

```
transcribe-svc/
├── README.md                 (this file)
├── NOTICE                    upstream component attributions
├── LICENSE                   proprietary, all rights reserved
├── .env.example              copy to .env; never commit .env
├── docker-compose.yml        default = CPU profile
├── docker-compose.cpu.yml    explicit CPU overlay
├── docker-compose.gpu.yml    GPU overlay (not built by default)
├── docker/
│   ├── Dockerfile.cpu        primary image
│   ├── Dockerfile.gpu        future / optional
│   └── entrypoint.sh
├── app/                      FastAPI service code
│   └── static/               installable PWA client (served at /)
├── tests/                    pytest suite
└── docs/
    ├── HARDWARE.md           current host facts
    ├── BLOCKERS.md           open issues
    ├── STATUS.md             phase-by-phase log
    ├── API.md                contract reference
    └── DEPLOY.md             VM bring-up
```
