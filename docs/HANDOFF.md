# Handoff — current state & how to resume

> **Read this first if you're picking up cold** (fresh clone, new machine, new
> person, or a fresh agent). This file lives in the repo on purpose — it does
> not depend on any external memory. Last updated **2026-08-14**.

## TL;DR

The GPU stack is **running on the Alienware** at
`C:\Users\<user>\Github\transcriberproject`, rebuilt from scratch on 2026-08-14 and
verified at **~10.9× realtime** with both GPU tiers confirmed serving
(`asr_backend: speaches@…`, `diarize_device: cuda`).

**Do not trust this file's "it's deployed" claim without checking.** On
2026-08-14 it said exactly that while Docker held zero images, containers, and
volumes for this project. Thirty seconds of verification first:

```sh
docker compose -f docker-compose.gpu.yml ps    # expect 4 containers, all healthy
curl -fsS http://localhost:8000/v1/health
```

If that comes back empty, you are doing a **cold rebuild** — go to
`docs/DEPLOY.md` → "Deploying to the Alienware", which now carries the pre-flight
checks and the bring-up order that works.

**Live URL: `https://transcribe-svc-1.example-tailnet.ts.net`** (HTTPS on, valid cert,
mic + PWA install work). Note the `-1` — it is permanent and intentional; see
"Node name" below before "fixing" it.

The provider-neutral OIDC/Cognito bridge (static/hybrid/OIDC modes, **85 tests
pass**) is built but **still not deployed** — no Cognito pool provisioned. Follow
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
| `speaches-model-init` | **one-shot** — fetches the ASR model on first `up`, then exits 0. `Exited (0)` in `ps` is the correct steady state, not a failure (B-005) | — |

- **Access:** PWA + API at **`https://transcribe-svc-1.example-tailnet.ts.net`**
  (tailnet, valid Let's Encrypt cert — mic recording + PWA install work on
  iPhone) and at `http://localhost:8000` on the host itself. HTTPS enabled
  2026-08-14 via `tailscale serve --bg 8000`; the serve config persists in the
  `tailscale_state` volume. Turn off with `tailscale serve --https=443 off`.
- **The hostname is `transcribe-svc-1`, permanently — this is not a bug.** Docs
  written before 2026-08-14 say `transcribe-svc`; that name was taken by a stale
  node at first registration, so MagicDNS derived `transcribe-svc-1`. The stale
  node has since been deleted, but **the name did not come back**: MagicDNS names
  are assigned at registration and persist in the `tailscale_state` volume.
  Neither deleting the stale node, restarting the container, `tailscale set
  --hostname`, nor an admin-console rename moves it (the console won't override a
  hostname-derived name). The only way back to `transcribe-svc` is wiping
  `tailscale_state` to force fresh registration — which needs a **reusable**
  `TS_AUTHKEY` in hand, because a consumed single-use key would leave the whole
  stack down (all containers gate on tailscale's health). Judged not worth it: the
  name is cosmetic. **Lesson for next deploy:** delete stale nodes *before* first
  bring-up, not after — see `docs/DEPLOY.md` Tailscale prep.
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
- **Track B (voice enrollment)** — **LIVE** as of 2026-08-14
  (`ENABLE_ROLE_LABELS=1`). One enrollment: `Speaker A`. The enrolled speaker is named
  in the transcript; in an exactly-2-speaker session the other becomes
  `CLIENT_LABEL` (currently `Speaker`). Threshold 0.5, validated on real voices —
  `ml/enroll/reports/2026-08-14-real-voice-sweep.md`.

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

## ML Track B — LIVE as of 2026-08-14

Enabled with one real enrollment (`Speaker A`) and validated on real voices. Config in
`.env`: `ENABLE_ROLE_LABELS=1`, `ROLE_MATCH_THRESHOLD=0.5`, `CLIENT_LABEL=Speaker`.
Measured separation: genuine 0.59–0.72 vs impostor 0.13. Full numbers, caveats,
and a write-up of how a first pass mis-measured it are in
**`ml/enroll/reports/2026-08-14-real-voice-sweep.md`**.

### To add another enrollment

```sh
# 1. Convert to WAV first — pyannote loads via torchaudio, which will NOT read
#    the WebM/Opus the PWA records.
docker compose -f docker-compose.gpu.yml exec transcribe-svc \
  ffmpeg -y -i /data/uploads/<id>.webm -ac 1 -ar 16000 -c:a pcm_s16le /data/uploads/ref.wav

# 2. Enroll (ml/ is not in the image — bind-mount it). Repeat --clip for
#    several reference clips; the vectors are averaged, and 2-3 clips from
#    different rooms/devices give a far more robust voiceprint than one.
docker compose -f docker-compose.gpu.yml run --rm -v ${PWD}/ml:/app/ml \
  transcribe-svc python -m ml.enroll.enroll --name "Name" \
    --clip /data/uploads/ref.wav --out-dir /data/enrollments

# 3. Restart, then re-sweep — a new enrollment invalidates the old threshold.
docker compose -f docker-compose.gpu.yml up -d transcribe-svc
```

### Gotchas worth knowing before you touch this

- **The enrolled speaker must be alone in the enrollment clip.** One of the
  operator's clips read as a single speaker but contained two people; enrolling it
  would have blended both voices into one useless centroid. Check
  `speakers_detected` *and* read the transcript before enrolling.
- **`/data/enrollments` must be a named volume** — it is, since 2026-08-14. It
  previously was not, so any `up -d` would have silently discarded the voiceprint
  and role labeling would have quietly stopped. Also note the image must create
  the directory: Docker seeds a fresh volume's ownership from the image, so a
  missing path mounts root-owned and enrollment fails with `EACCES`.
- **Only one cluster can win a name.** `match_clusters` is greedy one-to-one, so
  if diarization splits the enrolled speaker across two clusters, only the
  higher-scoring one gets named. Observed in practice.
- **`CLIENT_LABEL` only applies with exactly 2 detected speakers.** A third
  speaker (or an unattributed `SPEAKER_??` segment) disables the inference, and
  everyone but the enrolled speaker stays anonymous.
- **Voiceprints are biometric data.** Local volume only — never on shared or
  cloud storage, and treat the volume like a secret.

### Known robustness gap (not yet fixed)

Whisper can emit a segment end time past the real audio duration (seen:
29.84 s on a 22.87 s file). `compute_cluster_embeddings` then crops out of bounds,
throws per-segment, and that cluster produces **no vector at all** — silently
skipped. If it hits the enrolled speaker's cluster, role labeling does nothing
with no error. Fix would be clamping segment ends to the audio duration in
`app/roles.py` before embedding.

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

- **Python IS on the host now** (3.14.6, `/c/Users/<user>/AppData/Local/Microsoft/WindowsApps/python3`)
  — corrected 2026-08-14; this file previously said there was none. Fine for
  scripting/JSON, but it has none of the project deps, so still run unit tests in
  the WSL no-torch venv:
  `wsl -e bash -lc 'source ~/venvs/tsl/bin/activate && python -m pytest tests/ -q'`
  (bootstrapped via get-pip; `python3-venv` ensurepip is stripped, no passwordless sudo).
  Or inside the image: `docker compose -f docker-compose.gpu.yml run --rm transcribe-svc pytest tests/`.
- **`docker` IS on PATH now** (`/c/Program Files/Docker/Docker/resources/bin/docker`)
  — corrected 2026-08-14. Plain `docker` and `docker compose` work from Git Bash.
  The PowerShell caveat may still apply: if a build fails on "getting credentials",
  prepend that directory to `$env:PATH` so `docker-credential-desktop` resolves.
- **Speaches used to go "healthy" with no model and silently drop you to CPU —
  fixed 2026-08-14 (B-005).** Its healthcheck now greps `/v1/models` for the model
  id (the old one just checked the endpoint answered, which returns 200 on an
  empty list), the image is pinned by digest instead of the floating `:latest-cuda`
  that caused the regression, and `speaches-model-init` fetches the model
  automatically. Still worth confirming what actually served on any perf
  complaint — the transcript `.json` should say `speaches@…` (not
  `local-whisperx`) and `cuda` (not `cpu-fallback`).
- **Slow ≠ CPU fallback.** Speaches evicts the model after 5 min idle
  (`ttl=300`); the next request pays a reload. Measured 0.9× cold vs 10.3× warm on
  the same clip — an 11× swing that looks exactly like a fallback and isn't.
  Diagnose from the transcript header, never from wall-clock.
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
