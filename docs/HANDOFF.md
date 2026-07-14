# Handoff — current state & how to resume

> **Read this first if you're picking up cold** (fresh clone, new machine, new
> person, or a fresh agent). This file lives in the repo on purpose — it does
> not depend on any external memory. Last updated **2026-07-13**.

## TL;DR

The GPU stack remains deployed on the Alienware and served over HTTPS on the
tailnet. **Codex implementation note (2026-07-13):** this worktree now adds a
provider-neutral OIDC/Cognito bridge with static, hybrid, and OIDC-only modes.
**85 tests pass** and the static-mode PWA browser smoke test passes. Cognito has
not been provisioned and these worktree changes have not been deployed; follow
`docs/AUTH.md`, start in hybrid mode, and retain the current `.env` backup.

This is a **patient-owned** recorder for **any** patient↔provider interaction
(doctor, therapist, dentist, coach, …) — "flipping the script" on doctor-facing
AI scribes. Roles are generic (enroll any name + `CLIENT_LABEL`). PHI is
HIPAA-grade → local-only posture, synthetic-only ML rule.

## Current state — the GPU stack

`docker compose -f docker-compose.gpu.yml up -d` brings up **four** containers:

| Container | Role | Device |
|---|---|---|
| `tailscale` | netns owner; publishes `127.0.0.1:8000`; **HTTPS via `tailscale serve`** | — |
| `speaches` | ASR (`faster-whisper-large-v3`), OpenAI-compat on `127.0.0.1:8001` | **GPU** |
| `diarize-svc` | pyannote diarization sidecar on `127.0.0.1:8002` (torch cu128) | **GPU** |
| `transcribe-svc` | FastAPI orchestrator + PWA; routes ASR→speaches, diarization→diarize-svc | CPU (orchestration) |

- **Access:** PWA + API at `http://localhost:8000` (host loopback) and, over the
  tailnet, at **`https://transcribe-svc.<tailnet>.ts.net`** (valid Let's Encrypt
  cert via `tailscale serve` — mic recording + PWA install work on iPhone). The
  serve config persists in the `tailscale_state` volume. Turn off with
  `tailscale serve --https=443 off`.
- **Throughput:** ~8.7–10.9× realtime warm (GPU diarization). Was ~2.85× on CPU.
- **Both GPU features fall back to CPU** per request if their sidecar is down
  (`diarize_device: cpu-fallback`, `asr_backend: local-whisperx`) — an outage
  degrades speed but never fails a job.

To bring up / verify from scratch: `README.md` Quick start + `docs/DEPLOY.md`.

## What's live (features)

- **ASR router** — `ASR_BACKEND=router` tries speaches (GPU) then local WhisperX (CPU).
- **GPU diarization sidecar** — `DIARIZE_BACKEND=remote` → `diarize-svc`, CPU fallback.
- **PWA** — speaker-colored transcript, click-to-seek, mic record (needs HTTPS),
  **Audio language** selector (default English — stops Whisper mis-detecting e.g.
  Welsh on an unfiltered intro), **Output** selector (*Same as audio* /
  *English translate*), and **✎ Edit speakers** (rename across transcript /
  reassign a turn; persists via `POST /v1/results/{id}/relabel`).
- **OIDC auth bridge (Codex, pending deployment)** — standard Authorization Code
  + PKCE in the PWA; signed JWT/JWKS verification in the API; static/hybrid/oidc
  rollout modes. No Cognito SDK and no dependency on AWS hosting.
- **Output language phase 1** — `task=transcribe|translate` (Whisper does
  source-lang or English only; arbitrary targets = phase 2, not built).
- **Track B (voice enrollment)** — built, **off by default** (`ENABLE_ROLE_LABELS=0`).

## Fallback to the old CPU server (Proxmox VM)

`main` still runs on a CPU-only host because **every GPU feature is opt-in and
defaults to CPU**. To fall back:

```
docker compose up -d          # NOT the gpu compose — this is docker-compose.yml (CPU)
```

That uses `ASR_BACKEND=whisperx` (in-process CPU), `DIARIZE_BACKEND=local`
(in-process CPU pyannote), role labels off, `task=transcribe`. The `diarize-svc`
sidecar exists **only** in `docker-compose.gpu.yml` and is never referenced by the
CPU compose. Carry over the same `.env` (secrets below). Slower (no GPU), fully
functional — the same path Phase 1/2 ran on that box.

## Secrets (not in the repo)

`.env` is **gitignored**. A cold clone must re-provide: `API_TOKEN`, `HF_TOKEN`
(HuggingFace, pyannote conditions accepted for `speaker-diarization-3.1` +
`segmentation-3.0`), `TS_AUTHKEY` (reusable, `tag:transcribe-svc`),
`DIARIZATION_MODEL=pyannote/speaker-diarization-3.1`. `.env.example` documents all
keys. On the current Alienware host these are already set. Current API token is in
the operator's `.env` (the static/hybrid emergency bearer; rotate freely).
OIDC adds `AUTH_MODE`, `OIDC_ISSUER`, and `OIDC_CLIENT_ID`; none are secrets.
Keep `API_TOKEN` secret while hybrid mode is enabled.

## Next task → ML Track B live-enable (needs one operator clip)

Track B code is done and off by default. To turn it on:
1. The image already ships `app/embed.py` + `app/roles.py` (merged to main; rebuild
   transcribe-svc if the running container predates it).
2. **Enroll the PATIENT's voice** (not the provider — the patient holds the app and
   can record a solo clip easily; the provider is inferred as the other speaker):
   ```
   docker compose -f docker-compose.gpu.yml run --rm -v ${PWD}/ml:/app/ml \
     transcribe-svc python -m ml.enroll.enroll --name "You" --clip <ref.wav> --out-dir /data/enrollments
   ```
3. Set `ENABLE_ROLE_LABELS=1` (and `CLIENT_LABEL=Patient`/`Doctor`/… to taste) in
   `.env`, then `docker compose -f docker-compose.gpu.yml up -d`.
4. **Re-sweep the threshold on real voices** before trusting it — synthetic voices
   separate more cleanly than real ones (`python -m ml.enroll.sweep`).

## Other on-deck (not started)

- **Output-language phase 2** — arbitrary target languages via a local translation
  model (NLLB/M2M-100) as a post-pass, kept local for PHI.
- **Track C** — Whisper LoRA fine-tuning scaffold (smoke-run on synthetic; real
  gains wait for consented data). Re-measure any change with Track A's harness.
- **Storage / multi-user** — pluggable `StorageBackend`, per-user namespacing,
  cloud PHI/BAA caveat — authentication now exposes a stable subject, but
  storage remains shared and must be namespaced before third-party users.
- **Friendly `415`** on undecodable uploads (currently generic `500`).
- **Doc governance (anti-drift)** — a `GOVERNANCE.md` standing rule + PR-template
  checklist + a `/doc-review` pre-merge gate. Docs are the source of truth.

## Host / tooling notes (this Alienware, Windows)

- **No Python on the host.** Run unit tests in the WSL no-torch venv:
  `wsl -e bash -lc 'source ~/venvs/tsl/bin/activate && python -m pytest tests/ -q'`
  (bootstrapped via get-pip; `python3-venv` ensurepip is stripped, no passwordless sudo).
  Or inside the image: `docker compose -f docker-compose.gpu.yml run --rm transcribe-svc pytest tests/`.
- **`docker` is not on any PATH.** Use `"/c/Program Files/Docker/Docker/resources/bin/docker.exe"`.
  From **PowerShell** you must also prepend that dir to `$env:PATH` or the
  **`docker-credential-desktop` helper isn't found** and builds fail on "getting credentials".
- **No `gh` CLI** (Windows or WSL). Open PRs via the GitHub web UI / the push URL.
- PowerShell 5.1 flips `$?` to false on any native-command stderr, so BuildKit
  progress reads as a false "build failed" — check for `… Built` in the log.
- The service worker is **network-first + `cache:"no-store"`** — deploys show up on
  the next load. A browser that still has the *old* SW controlling needs **one**
  hard refresh (`Ctrl+Shift+R`); after that it self-updates.
- **diarize-svc build gotchas** (Blackwell/modern stack): pin `huggingface_hub==0.25.2`
  (pyannote 3.3.2 passes `use_auth_token`), add `matplotlib`, and the app force-patches
  `torch.load(weights_only=False)` (torch 2.6+ default rejects the pyannote checkpoint).
- ML honesty rule: pure-synthetic ASR fine-tuning misleads (distribution shift);
  synthetic is for pipeline/eval + domain-vocab augmentation. Always label a WER
  number with the eval set's nature.
