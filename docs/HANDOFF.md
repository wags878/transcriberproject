# Handoff — current state & how to resume

> **Read this first if you're picking up cold** (fresh clone, new machine, new
> person, or a fresh agent). This file lives in the repo on purpose — it does
> not depend on any external memory. Last updated **2026-07-12**.

## TL;DR

Phase 3 is **done and deployed on GPU**. The three-container stack runs on the
Alienware (Windows 11 + WSL2 + Docker Desktop + RTX 5090). Branch
`phase-3-gpu`, **PR #1** open to `main`, working tree clean. A demo-quality PWA
web client is live. **Agreed next task: ML Track A — the synthetic eval
harness** (below).

## Current state

- **Running:** `docker compose -f docker-compose.gpu.yml up -d` → `tailscale`
  sidecar + **Speaches** (`faster-whisper-large-v3` on the GPU) + `transcribe-svc`
  (FastAPI: pyannote diarization + orchestration on CPU, WhisperX CPU fallback).
  All three healthy. Model is downloaded (persists in the `speaches_models`
  Docker volume via `PRELOAD_MODELS`).
- **Access:** web client + API at `http://localhost:8000` (host loopback,
  published by the GPU compose) and over the tailnet at
  `http://transcribe-svc.<tailnet>.ts.net:8000` (http on :8000, not https).
- **Verified:** ASR on GPU (`asr_backend=speaches@…`, `large-v3`), CPU fallback
  when Speaches is down, diarization on 1/2/3-speaker synthetic clips, 7 audio
  formats. Throughput ~2.85× realtime warm (bottleneck = CPU diarization). See
  the `docs/STATUS.md` Phase 3 close-out + throughput baseline.
- **Tests:** 39 passing. Run them without the heavy stack in the no-torch venv
  (see `docs/DEPLOY.md` → "Running the unit tests on the Alienware"), or inside
  the image: `docker compose -f docker-compose.gpu.yml run --rm transcribe-svc pytest tests/`.

To bring it up / verify from scratch: `README.md` Quick start + `docs/DEPLOY.md`.

## Secrets (not in the repo)

`.env` is **gitignored**. A cold clone must re-provide: `API_TOKEN`,
`HF_TOKEN` (HuggingFace, pyannote conditions accepted),
`TS_AUTHKEY` (reusable, `tag:transcribe-svc`), and
`DIARIZATION_MODEL=pyannote/speaker-diarization-3.1`. `.env.example` documents
all keys. On the current Alienware host these are already set.

## Next task → ML Track A: synthetic eval harness

Build `ml/` per **`docs/superpowers/plans/2026-07-12-ml-eval-and-training.md`**,
Track A (tasks A1–A5): a synthetic conversation generator, WER + speaker-
attribution/DER scoring, `run_baseline.py`, and a first baseline scorecard.
Self-contained, no PHI, runs on the (mostly idle) GPU. It's the measurement
foundation before any fine-tuning. The recipe for generating labeled multi-voice
audio already exists (see `samples/` + `samples/README.md`).

## Open threads (on deck, not started)

- `tailscale serve` → HTTPS so the PWA (mic recording + install) works fully on
  iPhone over the tailnet.
- Friendly `415` guard on undecodable uploads (currently a generic `500`).
- Storage / multi-user: pluggable `StorageBackend`, per-user namespacing, cloud
  (Drive/OneDrive) PHI/BAA caveat — design-doc first, gated on multi-user auth
  (Phase 4). 
- Therapist voice enrollment (ML Track B); GPU diarization (ML Infra) — the
  highest-ROI latency win.
- **Doc governance (anti-drift).** Docs are the source of truth; keep them from
  drifting (we already hit it — README said "Phase 1" while deployed on GPU).
  Plan: a short `GOVERNANCE.md` (standing rule: every PR reconciles docs + a
  doc-impact map of which code area touches which docs), a
  `.github/pull_request_template.md` checklist, and a Claude Code `/doc-review`
  command/skill that reviews the PR diff against the docs semantically and
  proposes fixes — run it as a **pre-merge gate**, not after close. CI can only
  flag "code changed, docs didn't"; it can't verify accuracy, so the review
  (agent or human) is the real check.

## Host / tooling notes (this Alienware, Windows)

- No-torch unit-test venv: `~/venvs/tsl` in WSL (bootstrapped with `get-pip.py`
  — `python3-venv` ensurepip is stripped and there's no passwordless sudo).
- `docker cp` needs **Windows-style paths** run from PowerShell; git-bash
  `/c/...` paths silently produce an unreadable file (curl exit 26). `docker` is
  not on git-bash PATH — use `"/c/Program Files/Docker/.../docker.exe"` or PowerShell.
- PowerShell 5.1 flips `$?` to false on any native-command stderr, so BuildKit
  progress reads as a false "build failed" — check the log for `naming to … Built`.
- The web client's service worker is **network-first** — deploys show up on a
  normal reload; a hard refresh (`Ctrl+Shift+R`) clears any old cached shell.
- ML honesty rule (from the plan): pure-synthetic ASR fine-tuning misleads
  (distribution shift). Synthetic is for pipeline/eval + domain-vocab
  augmentation; real accuracy gains need real (consented) audio.
