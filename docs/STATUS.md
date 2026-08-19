# Status log

Append-only. After each phase, add a short note: what was done, what's deferred, what surprised.

---

## 2026-04-30 — Phase 0 (in progress)

**Done:**
- Plan approved (`/home/transcriber/.claude/plans/okay-now-that-we-frolicking-diffie.md`).
- Open questions resolved: license=Proprietary, GPU=skipped, diarization model=`speaker-diarization-community-1`, Tailscale tags=`tag:transcribe-svc`/`tag:transcribe-client`, RETAIN_DAYS=30, MAX_CONCURRENT_JOBS=2.
- Host fact-gathering: see `docs/HARDWARE.md`.
- Verified: Docker v29.4.1, Compose v5.1.3, Tailscale 1.96.4 — all present.

**Deferred:**
- UFW config: operator decision 2026-04-30 to leave UFW off for now. Spec §6 Phase 0 step 4 (deny-by-default + allow `tailscale0` + allow SSH from LAN) is **deviated**. Trade-off: anything on the LAN (`192.168.1.0/24`) can reach the API once it's bound. Acceptable for now given LAN is trusted; revisit before any wider exposure.
- VM tagging in Tailscale admin console (needs operator) — required before Phase 4 ACLs.

**Surprised:**
- Current host is a 7.7 GiB QEMU VM with no AVX-512 — does **not** match the 128 GiB / Xeon-Silver spec hardware. Logged as **B-001** in `docs/BLOCKERS.md`.

**B-001 resolution (same day):** Operator decided this Proxmox VM is the project home; success bar is "works for a 60-min therapy session." Defaults adjusted to `WHISPER_MODEL=medium` and `MAX_CONCURRENT_JOBS=1`; both env-tunable so the operator can ramp up after passing through more Proxmox resources. Phase 1 proceeds.

---

## 2026-04-30 — Phase 1 (in progress)

**Done (scaffolded and structurally validated):**
- Repo layout per spec §5 — see directory tree below.
- `LICENSE` (proprietary), `NOTICE` (with pyannoteAI CC-BY-4.0 attribution), `README.md`, `.env.example`, `.gitignore`, `requirements.txt`.
- `docker/Dockerfile.cpu` (primary, python:3.11-slim base, ffmpeg + libsndfile + libgomp + tini + curl), `docker/Dockerfile.gpu` (cuda:11.8 base, scaffolded only), `docker/entrypoint.sh` (refuses placeholder API_TOKEN).
- `docker-compose.yml` (default = CPU), `docker-compose.cpu.yml`, `docker-compose.gpu.yml`. All three pass `docker compose config --quiet`.
- `app/` — FastAPI service: `main.py` (routes per §4 contract), `pipeline.py` (whisperx wrapper with concurrency semaphore, lazy model loading), `auth.py` (constant-time bearer compare), `storage.py`, `schemas.py`, `config.py` (pydantic-settings).
- `tests/` — `conftest.py` stubs the heavy whisperx pipeline so unit tests don't need torch. `test_health.py`, `test_auth.py`, `test_transcribe_smoke.py`. All Python files pass `python3 -m py_compile`.
- `docs/API.md`, `docs/DEPLOY.md`, `docs/HARDWARE.md`, `docs/BLOCKERS.md` (B-001 resolved).

**Deferred / blocked on operator:**
- **Cannot run `pytest tests/` locally** — VM has Python 3.12 but no `python3-pip` or `python3-venv` packages. Either:
  - `sudo apt install python3-pip python3-venv` (then `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt fastapi pytest httpx && pytest tests/`), or
  - Run tests inside the built container: `docker compose run --rm transcribe-svc pytest tests/`.
- **Have not yet run `docker compose up -d`.** First build will pull torch + ctranslate2 + pyannote.audio (multi-GB download, multi-minute build). Holding for operator greenlight given the 7.7 GiB VM.
- **No fixture audio file present.** `tests/fixtures/short_two_speaker.wav` is not committed (and shouldn't be unless operator-recorded or public-domain). Needed for the end-to-end smoke test and the perf-baseline acceptance item.
- **API_TOKEN in `.env`** is still the placeholder; entrypoint will refuse to start. Operator must `cp .env.example .env` (already done) and replace the token.

**Phase 1 acceptance gate (§6):**
- [x] `NOTICE` present and complete — verified.
- [ ] `docker compose up -d` brings service up cleanly — pending operator greenlight on the build.
- [ ] `curl /v1/health` returns the documented JSON — depends on the build.
- [ ] Fixture WAV → 200 + valid schema — depends on fixture + build.
- [ ] `.txt` and `.json` returned at the URLs — depends on build.
- [ ] `.txt` has speaker labels — depends on build.
- [ ] No auth header → 401 — verified by `tests/test_auth.py` (runnable once pip is available).
- [ ] Performance baseline measured on 5-min fixture — depends on fixture + build.

**Surprised:**
- VM has no pip/venv at all — common for stripped-down Proxmox guests but worth noting.
- WhisperX install footprint is heavy enough that the first `docker compose build` will likely be a 10–15 GB image. Worth confirming there's disk headroom on the VM before kicking it off.

---

## 2026-04-30 (cont'd) — Phase 1 acceptance gate **MET**

End-to-end test of `tests/fixtures/short_two_speaker.wav` (39.5 s, two-voice dialogue) returned HTTP 200 with the documented schema. `.txt` has speaker labels and `[mm:ss]` timestamps; `.json` has WhisperX-style segments + per-word timings + speaker assignments + the documented header (id, created_at, model, compute_type, device, diarization_model). Diarization correctly attributed turns to two distinct speakers across the conversation.

**Phase 1 acceptance gate (§6) — final state:**
- [x] `docker compose up -d` brings service up cleanly
- [x] `curl /v1/health` returns the documented JSON
- [x] Fixture WAV → 200 + valid schema response
- [x] `.txt` and `.json` returned at the URLs
- [x] `.txt` has speaker labels (`SPEAKER_00:` / `SPEAKER_01:`)
- [x] No auth header → 401 (verified by `tests/test_auth.py`, all 10 unit tests passing inside container)
- [x] `NOTICE` present
- [x] Perf baseline measured (smoke run; 5-min run in progress)

**Detours hit during Phase 1 (logged in `docs/BLOCKERS.md`):**
- B-001 (resolved): VM hardware ≠ spec hardware. Settled on `medium` model + concurrency=1.
- B-002 (resolved): `transcriber` user wasn't in `docker` group. `usermod -aG docker`.
- B-003 (worked around): PyPI quarantined the `lightning` package. Built a local `lightning` shim that satisfies pip's metadata constraint and aliases `lightning.pytorch.*` → `pytorch_lightning.*` at runtime. Forced full version pin chain: whisperx 3.2.0 / pyannote.audio 3.1.1 / torch 2.0.1 / torchaudio 2.0.2 / numpy <2 / PyAV 11 source-built against bookworm's FFmpeg 5. Switched base image to `python:3.11-slim-bookworm` because Debian 13's FFmpeg 7 broke PyAV 11's source build.
- B-004 (resolved by operator): pyannote `community-1` model required HF auth despite spec's expectation. Operator created HF account, generated token, accepted user conditions for both `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`. Switched `DIARIZATION_MODEL` from `community-1` to `3.1` because community-1's config uses a `plda` parameter that pyannote.audio 3.1.1 doesn't accept.

**Bug logged for follow-up:** `pipeline.load()` is not idempotent on partial failure — if the diarizer init throws, `_model` stays set so subsequent calls short-circuit instead of retrying. Tracked as task #10. Workaround: restart the container after fixing the underlying cause.

**Performance reality:** 39.5 s of audio took 186 s on CPU = ~4.7× slower than realtime. A 60-min session extrapolates to ~4.7 h wall-clock. That's outside the spec's target but consistent with the constrained-VM defaults. See `docs/HARDWARE.md` "Performance baseline" for the operator's tuning options.

---

## 2026-05-01 — VM reconfig + tuning runs (Phase 1 §6 still MET)

**Operator action:** Bumped Proxmox VM specs. All three knobs that the spec assumed are now (closer to) in place:
- vCPUs 8 → **16**
- RAM 7.7 GiB → **15.6 GiB**
- CPU mode generic QEMU vCPU → **`Intel(R) Xeon(R) Silver 4214` host-passthrough** (AVX-512 set including `avx512_vnni` now exposed — the int8 VNNI extension CTranslate2 uses)

Phase 0 §6 acceptance item (`lscpu | grep -i avx512` returns flags) is now **fulfilled** rather than accepted-as-deviated. B-001 updated.

**Smoke + tuning runs (all on `tests/fixtures/five_minute.wav`, 300 s audio):**

| run | settings | wall | pipeline | RT ratio | container peak mem |
|---|---|---|---|---|---|
| Run A | `medium` / conc=1 | 621.8 s | 621.6 s | 0.48× (2.07× slower) | 3.99 GiB |
| Run B | `large-v3` / conc=1 | 815.3 s (incl ~3 GB DL) | 772.0 s | 0.39× (2.57× slower) | 5.63 GiB |
| Run C (×2 parallel) | `medium` / conc=2 | job1 1002 s, job2 753.7 s | 976.5 s / 730.0 s | 0.31× / 0.41× | 5.58 GiB combined |
| Run D (×2 parallel) | `large-v3` / conc=2 | job1 1246 s, job2 996.9 s | 1228.8 s / 982.2 s | 0.24× / 0.31× | **6.60 GiB combined** |

**Headline:** ~2× wall reduction at the same model + concurrency vs the 2026-04-30 baseline (1236 s → 621.8 s). Caused mostly by vCPU 8→16 + AVX-512 VNNI, not the RAM bump directly.

**Phase 1 §6 acceptance gate — re-checked, still MET:**
- [x] `docker compose up -d` brings service up cleanly — verified after each .env change today
- [x] `curl /v1/health` returns the documented JSON
- [x] Fixture WAV → 200 + valid schema response — three different settings, all OK
- [x] `.txt` and `.json` returned at the URLs
- [x] `.txt` has speaker labels — verified Runs A & B; both correctly attribute to two speakers
- [x] No auth header → 401 — verified live (`curl` with no token returned 401 on `/v1/transcribe`)
- [x] `NOTICE` present
- [x] Perf baseline measured (now three baselines on the new hardware)

**.env state at end of these runs:** `WHISPER_MODEL=large-v3`, `MAX_CONCURRENT_JOBS=2` — operator-chosen as the Phase 2 default. Rationale: quality is expected to matter for real (not-fixture) therapy audio; concurrency=2 is mostly insurance for when files queue and will pay off more once this moves to bare-metal hardware where the per-job latency penalty shrinks. Memory peak 6.6 / 15.6 GiB at this config — comfortable.

**Surprised:**
- The single .env comment that said "raise to 2 after VM RAM is increased" implied RAM was the constraint. RAM was barely used at concurrency=1 (peaks 25–36% of 15.6 GiB). The real constraint at concurrency=2 was CPU contention, not memory.
- Single-job CPU saturation only reaches ~4 of 16 vCPUs. There's a per-job thread-count knob (CTranslate2 / OMP) we haven't touched. Tuning that could give single-file latency a meaningful drop without going to concurrency>1.
- Quality difference between `medium` and `large-v3` on the clean studio fixture is essentially nil. The case for large-v3 has to come from real-world audio (overlap, noise, accents), not this fixture.

**Phase 2 readiness:** Phase 1 §6 acceptance is met (was met on 2026-04-30, re-met on the upgraded hardware today). Phase 2 (per `PROJECT_PLAN.md` §6) is **unblocked from the gate side**. Operator decision remaining: pick the `.env` default to ship Phase 2 against (see options listed at the end of this turn's chat).

---

## 2026-05-01 — Phase 2 (complete)

**Goal (per `PROJECT_PLAN.md` §6):** outputs that are pleasant to read and don't pile up forever.

**Done:**
- **Filename convention** — files on disk now `<YYYY-MM-DD>_<HHMM>_<title-slug>_<short-uuid>.{txt,json}`. URLs stayed `/v1/results/{id}/transcript.{txt,json}` for API stability; a tiny on-disk index in `outputs_dir/.index/{job_id}` resolves URL → on-disk filename. Verified live: `2026-05-01_1731_phase-2-final_5a4b6f67.{txt,json}`.
- **`.txt` paragraph merging** — `pipeline.render_txt()` now groups consecutive same-speaker WhisperX segments into one paragraph, two newlines between speaker turns, single `[mm:ss]` prefix per turn (start time of the turn). The Phase 1 `short_two_speaker` smoke went from ~14 segment-lines to 6 readable paragraphs.
- **Retention** — `storage.cleanup_old_files()` walks `uploads_dir` and `outputs_dir` on container startup (called from the FastAPI lifespan in `app/main.py`); `models_dir` is never swept. Logs `Retention: removed N uploads, M outputs (RETAIN_DAYS=K)` so the spec acceptance test is grep-able. Stale stem-index entries are reaped after the file walk.
- **`RETAIN_DAYS` semantics** — finalized: `>0` is days; `=0` deletes everything older than the moment of restart (per spec acceptance test); `<0` (e.g. `-1`) disables. `.env.example` comment updated.
- **Backward compat for Phase 1 outputs** — `storage.transcript_paths()` falls through to legacy `{uuid}.txt`/`.json` when no index entry exists. Verified via `tests/test_phase2.py::test_resolver_legacy_fallback`. Old Phase 1 fixtures stay fetchable until retention reaps them.
- **Disk usage endpoint** — `GET /v1/admin/storage` already shipped in Phase 1 with the spec'd shape; verified unchanged.
- **`.json` header** — already shipped in Phase 1 with all spec fields (and a few extras: `device`, `diarization_model`); changed only one line — `created_at` now reads from `result["created_at"]` so the header timestamp matches the filename's date/time rather than drifting by however long the job took.
- **Tests** — added `tests/test_phase2.py` (12 tests covering paragraph merge, filename pattern with title / untitled / special-char title, slug truncation, retention with positive / zero / negative `RETAIN_DAYS`, models_dir skip, legacy resolver fallback, 404). Extended `conftest.py`'s pipeline stub to 3 segments where two share `SPEAKER_00` so render_txt's merge path is exercised. All 22 tests pass.
- **Image rebuilt** — `docker compose build` baked Phase 2 source into `transcribe-svc:cpu`. Phase 2 is now the durable state across `compose down/up`, not just a `docker cp`'d overlay.
- **Doc updates** — `docs/API.md` (paragraph-per-turn `.txt` example, on-disk filename note, removed Phase-1-era forward-looking language, refreshed example `model`/`diarization_model` to match current operator config); `.env.example` (`RETAIN_DAYS` comment block); this status section.

**Phase 2 §6 acceptance gate — final state:**
- [x] Re-run smoke test; `.txt` is human-readable with timestamps. (Verified 2026-05-01 12:31 CDT.)
- [x] Running with `RETAIN_DAYS=0` and `touch -d '-31 days' …` on a fixture cleans it up at next startup. (Verified: log line `Retention: removed 16 uploads, 20 outputs (RETAIN_DAYS=0)`; specifically the touched file `028bf8e7-…` was confirmed gone after restart; `models_dir` survived.)
- [x] `docs/API.md` updated.

**Deferred:**
- The `pipeline.load()` partial-init idempotency bug (`_model` stays set on partial diarizer init failure) — task #10. Out of Phase 2 scope per the plan; folding it in would expand the test surface (need to mock a throwing diarizer) for a low-frequency error path. Standalone fix.
- Async/polling job model — not in §6, not done.
- Multi-token auth — Phase 4.

**Surprised:**
- The `/v1/admin/storage` endpoint and `.json` header turned out to be already-shipped in Phase 1, so two of five Phase 2 spec items were checkbox-only. Real work was confined to filename convention, paragraph merge, and retention — narrower than the spec list suggested.
- Subtle gotcha caught during implementation: `cleanup_old_files`'s `rglob` would otherwise traverse into `outputs_dir/.index/`, potentially deleting index entries by their own mtime and orphaning files that point at them. Fixed by skipping `.index/` in the file walk; the explicit dangling-index sweep at the end is the canonical place to clean indices.
- `docker compose down` followed by `docker compose up` recreates the container from the **image**, so any `docker cp`'d source overlay is lost. Phase 2 needed `docker compose build` to bake into the image — confirmed in the final round before claiming Phase 2 complete.

**Phase 3 readiness:** Phase 2 §6 acceptance met. Phase 3 (per `PROJECT_PLAN.md` §6) is unblocked from the gate side.

---

## 2026-07-11 — GPU inference path + selected as TesterClaw's second test target

Cross-project session (FamilyOS/TesterClaw coordination chat). Nothing in this repo changed;
this entry records decisions and constraints that land on transcribe-svc.

**Done / decided:**

- **GPU acceleration is now available — and it is committed.** Operator hardware: Alienware
  Area-51, i9 / 64 GB / **mobile RTX 5090 (24 GB VRAM)**, stationary + plugged in, on
  Tailscale.
  - **B-001 was RESOLVED by *lowering* the bar** (Proxmox VM → `WHISPER_MODEL=medium`,
    `MAX_CONCURRENT_JOBS=1`). The 5090 lets us **raise it back**: `large-v3` fits comfortably
    in 24 GB — order-of-magnitude faster and materially more accurate against the
    "60-minute session" success bar. Use `docker-compose.gpu.yml`.
  - Both are env-tunable already (`WHISPER_MODEL`, `MAX_CONCURRENT_JOBS`), so this is config
    + host, not code.
  - **Serve inference from bare metal / native on the GPU host.** Do **not** try to run GPU
    inference in a container on the MacBook: Apple Silicon cannot pass the GPU to a Linux VM
    (macOS holds it for the display) — **including Apple's new `container` project**.
    Paravirtualized workarounds (krunkit/libkrun + Mesa Venus + MoltenVK) are Vulkan-compute
    only and slow. Apple `container` is still fine for the *non-GPU* containers.

- **transcribe-svc is now TesterClaw Plan C **T4.0** — the second-project genericity proof.**
  Chosen over `ah-helpdesk` because it is the operator's own, domain-agnostic project with no
  IP entanglement (ah-project is frozen pending an employment-agreement/counsel check).
  - **It is API-only — no web UI.** TesterClaw's browser/UX persona runner (pages, DOM,
    localStorage, axe, screenshots) **cannot crawl it.** It will be tested with TC's **API
    persona runner** via `openapi_spec_url` (FastAPI serves `/openapi.json`) +
    `auth_strategy: bearer_register_login`. **This is a stronger genericity proof** — a
    different target *class*, different auth, different runner, profile-only, zero code change.
  - Known caveat to check: `POST /v1/transcribe` is a **multipart file upload**; TC's API
    runner may only speak JSON. If it can't upload, probing auth/401s/error handling/
    content-type validation is still a valid proof.

**🚨 HARD CONSTRAINT — PHI (read before any TesterClaw run against this service):**

This service transcribes **therapy sessions** (`docs/STATUS.md` success bar: *"works for a
60-minute therapy session"*). That content is **PHI**, and psychotherapy material carries
heightened protection under HIPAA. `RETAIN_DAYS=30` means transcripts persist on disk.

**TesterClaw must ONLY ever be pointed at a clean instance — empty output dir, SYNTHETIC
audio only (e.g. a ~30-second nonsense clip). NEVER at an instance holding real recordings or
transcripts.** Personas hitting endpoints that return or reference transcript content would
pull PHI into TesterClaw's findings store and potentially out to frontier models.

This is the "**eliminate the exposure, don't just detect it**" layer (L0) from
`FamilyOS/plans/TESTERCLAW_ML_AND_INFERENCE_ROADMAP.md` §3, and the portfolio-wide
**layered-defense** principle in `FamilyOS/PROJECT_PLAN.md` → Standing decisions.

**Watch item — licensing (not a blocker):**

`NOTICE` is thorough and correct (WhisperX BSD-2; pyannote.audio MIT; diarization **model
weights CC-BY-4.0** with the required attribution string captured; Whisper/faster-whisper/
CTranslate2 MIT). One open question: **ffmpeg is "LGPL/GPL depending on build."** Hosted use
generally does not trigger copyleft (no distribution; separate binary, not linked). **But if
the Docker image is ever distributed, know which ffmpeg build is baked in first.**

**Surprised:**

- The security posture note from Phase 0 (UFW off; *"anything on the LAN can reach the API
  once it's bound"*) is the same trap that appears when serving a local model endpoint —
  LM Studio/Ollama ship with **no authentication**, so binding to `0.0.0.0` publishes an open
  model server. Bind to the Tailscale interface + gate with a Tailscale ACL. Worth revisiting
  the UFW deviation here too before any wider exposure.

---

## 2026-07-12 — Phase 3 implemented + deployed on the Alienware (GPU via Speaches)

New home of record: the Alienware Area-51 laptop (Windows 11 + WSL2 + Docker
Desktop, RTX 5090 Laptop 24 GB, driver 592.02). transcribe-svc migrated off the
Proxmox VM. GPU passthrough verified (`docker run --gpus all
nvidia/cuda:12.8.0-base nvidia-smi` sees the 5090).

**Shipped (Tasks 0–10 of the Phase 3 plan):**
- Composable pipeline: `app/asr.py` (`ASRBackend` protocol, `LocalWhisperXASR`,
  `SpeachesASR`, `ASRRouter`), `app/diarize.py` (`Diarizer`), `app/stitch.py`
  (`stitch_speakers`). `TranscribePipeline` now runs ASR + diarization
  concurrently (`asyncio.gather`) and stitches. Public HTTP surface unchanged.
- ASR routing: `ASR_BACKEND=router`, `ASR_HOSTS=http://127.0.0.1:8001,local-whisperx`.
  Health-checked first-healthy-wins with fall-through to CPU on error.
- Three-container GPU stack (`docker-compose.gpu.yml`): tailscale sidecar +
  Speaches (CUDA) + transcribe-svc sharing the tailscale netns. No host ports.
- Unit tests: 39 passing (stitch 7, speaches 4, router 5, + existing). Run
  locally in a no-torch WSL venv (get-pip bootstrap — ensurepip is stripped and
  there's no passwordless sudo; documented in DEPLOY.md).

**Acceptance — all met (synthetic gTTS clip only; PHI rule: never real audio):**
- All three containers healthy; tailscale node `transcribe-svc.example-tailnet.ts.net`
  tagged `tag:transcribe-svc`, online.
- `/v1/health` returns the documented JSON.
- GPU path: `.json` shows `asr_backend=speaches@http://127.0.0.1:8001`,
  `model=Systran/faster-whisper-large-v3`. Transcript accurate + speaker-labeled.
- Fallback: stop Speaches → `asr_backend=local-whisperx`, `model=medium`, still 200.
  Restart Speaches → GPU path resumes.
- VRAM: ~3.6 GB with large-v3 resident (well under 24 GB).

**Detours hit (all resolved):**
- **B-005 (resolved): Speaches does not auto-download the ASR model.** First
  transcription 404'd ("model is not installed locally") and the router silently
  fell back to CPU. Root-caused via the new `served_by`/`model` reporting (added
  precisely because the pre-fix `asr_backend` printed the whole router chain and
  hid which tier served). Fixed with `PRELOAD_MODELS='["...large-v3"]'` in compose
  (persists in the `speaches_models` volume) + relaxed transcribe-svc's
  dependency to `service_started` (it has the CPU fallback) + bumped the speaches
  healthcheck `start_period` to 900s for the ~3 GB first-run pull.
- Windows line endings: `core.autocrlf=true` had rewritten `docker/entrypoint.sh`
  to CRLF in the working tree — a latent build-breaker (CRLF shebang). Added
  `.gitattributes` forcing LF + set repo-local `autocrlf=false`.
- Tooling gotchas for the next agent: the Windows `docker.exe` needs Windows
  paths for `docker cp` (git-bash `/c/...` paths silently produce an unreadable
  file → curl exit 26); `git-bash` has no `docker` on PATH (use the full
  `/c/Program Files/Docker/...` path or PowerShell); PowerShell 5.1 flips `$?` to
  false on any native-command stderr, so BuildKit progress reads as a false
  "build failed".

**Performance reality (first request, cold):** 12.2 s synthetic clip took 34–47 s
wall on a cold stack — dominated by first-time model loads (Speaches loading
large-v3 into VRAM, pyannote + wav2vec2 alignment) and **CPU-side diarization**.
GPU accelerates ASR only; pyannote diarization stays on CPU by design, so it is
the bottleneck for long sessions. Warm steady-state is materially faster. A real
5-minute-fixture baseline and warm-run numbers are still to be measured on
operator-supplied (synthetic) audio.

**Deferred:**
- Pyannote diarization on GPU (would cut the dominant cost for 60-min sessions).
- Startup warm-up / `EAGER_LOAD` so the first real request isn't a cold load.
- PWA client for the demo; native iOS/Android with embedded `tsnet` as
  `tag:transcribe-client` is post-demo (needs Apple/Play developer enrollment).

### Throughput baseline (2026-07-12, warm, 145.9 s synthetic speech)

| Path | Wall | Realtime factor |
|---|---|---|
| Pure GPU ASR (Speaches large-v3, diarization bypassed) | ~10.5 s | ~14× |
| Full GPU pipeline (ASR ∥ CPU diarization + align + stitch) | ~51 s | ~2.85× |
| CPU-only fallback (medium ASR + CPU diarization) | ~91 s | ~1.6× |

GPU ASR is ~14× realtime; the full pipeline is **bounded by CPU-side pyannote
diarization** (~2.85×). ASR finishes in ~10 s then waits ~40 s for diarization.
60-min session extrapolation: GPU path ~21 min wall; CPU-only ~37 min; moving
diarization to GPU would approach the ASR-bound ~4–5 min (~5× win — the single
highest-value optimization). Cold first request is dominated by model load
(a 12 s clip took 34–47 s cold vs. these warm numbers) — warm up before a demo.
Measured via `docker exec` curl inside the netns; `MAX_CONCURRENT_JOBS=1`, so
concurrent uploads serialize.

---

## 2026-07-12 — Session handoff (repo-tracked)

After Phase 3 deploy: added a demo-quality PWA web client (`app/static`, served
at `/`, published on `localhost:8000`), synthetic sample clips (`samples/`, served
at `/samples`), a network-first service worker, and refreshed README/API/DEPLOY.
Confirmed accepted audio = anything ffmpeg decodes; the ML track and
storage/multi-user direction are captured as plans/specs.

**Current state, how to resume, the agreed next task (ML Track A eval harness),
open threads, and host/tooling notes are in `docs/HANDOFF.md`** — the
repo-tracked cold-pickup brief. Read that first on resume.

---

## 2026-07-12 — ML Track A close-out (synthetic eval harness)

Built the measurement foundation under `ml/` (branch `ml-eval-harness`, separate
PR from Phase 3). Self-contained, no PHI, offline — drives the live service only
over its HTTP contract; the request path (`app/`) is untouched.

**Done (tasks A1–A5):**
- `ml/synth/generate.py` — parametric edge-tts + ffmpeg conversation generator
  (generalizes the `samples/` recipe). Emits `audio.mp3` + `truth.json` with
  exact per-turn `start`/`end` (cumulative rendered-clip durations + fixed
  silence), so truth drives both WER and time-overlap attribution scoring.
  4 scripts: 2-spk short/long, 3-spk, domain-vocab-heavy (clinical terms).
- `ml/eval/score.py` — WER via jiwer (case/punct-normalized); speaker-attribution
  accuracy as the fraction of truth speech-time correctly labeled after solving
  the optimal predicted→truth label assignment (one-to-one, DER-style — over-
  detection is penalized). Pure functions, unit-checked.
- `ml/eval/run_baseline.py` — generate → POST `/v1/transcribe` → score → write
  scorecard. Containerized (`ml/Dockerfile`, `ml/docker-compose.ml.yml`) so
  generation+scoring need no host Python/ffmpeg; reaches the host service at
  `host.docker.internal:8000`, token from `.env`.
- First committed scorecard: `ml/eval/reports/2026-07-12-baseline.md`.

**Baseline numbers (large-v3 on GPU via Speaches, CPU pyannote 3.1):**

| Metric | Value |
|---|---|
| Clips scored | 4 (53 / 89 / 32 / 41 s) |
| Mean WER (per-clip) | **0.0%** |
| Word-weighted WER | **0.0%** |
| Mean speaker-attribution accuracy | **81.1%** |
| Speaker-count correct | **4/4** |

**Honesty (do not strip from any future report):** WER 0.0% is *real* (verified:
114/114 words exact on the domain-vocab clip, "cognitive behavioral therapy",
"rumination" et al.) but it reflects **clean, uniform synthetic TTS, not real
speech** — it is optimistic and not a target for real audio. The useful signal
here is speaker attribution (~81%; the ~19% loss is turn-boundary slip at speaker
changes, where a whole ASR segment is credited to one speaker). Every scorecard
carries this caveat inline.

**Surprised / notes:**
- edge-tts 6.x now 403s on Microsoft's endpoint (missing `Sec-MS-GEC` handshake
  token); pinned **7.2.8**.
- Container clock is UTC — `date.today()` produced `2026-07-13`; report renamed
  to the session date `2026-07-12` for consistency (`--report-date` overrides).

**Deferred / on deck:** Track B (voice enrollment → real `Therapist`/`Client`
labels), Infra I (GPU diarization — would also lift the ~19% attribution loss and
the 2.85× throughput ceiling), Track C (LoRA scaffold). Order stands: A → B → I → C.

---

## 2026-07-12 — ML Track B close-out (therapist voice enrollment)

Enroll a voice once → auto-label `Therapist` (and infer `Client` in a 2-speaker
session) instead of anonymous `SPEAKER_00/01`. Branch `track-b-voice-enrollment`
stacked on `ml-eval-harness`. **Feature is off by default** (`ENABLE_ROLE_LABELS=0`)
— the `/v1` output contract is unchanged unless explicitly enabled.

**Done (tasks B1–B5):**
- `app/embed.py` — pretrained speaker-embedding backend + `Embedder` protocol +
  cosine/centroid helpers. **Placed in `app/`, not `ml/`** (deviation from the
  plan's `ml/enroll/embed.py`): `app/roles.py` uses it at request time, so it must
  ship in the service image; `ml/enroll` imports it from there.
- `ml/enroll/enroll.py` — build an enrollment voiceprint from ≥1 clip →
  `<name>.npy` + metadata. Voiceprints are **biometric** → gitignored, kept off
  shared storage.
- `ml/enroll/sweep.py` + `ml/enroll/reports/2026-07-12-threshold-sweep.md` — cosine
  separation on synthetic voices: genuine A **0.85–0.92**, impostors B/C
  **0.16–0.23**; every threshold in **0.3–0.8** gives 2/2 genuine, 0/3 false
  positives. Config default `ROLE_MATCH_THRESHOLD=0.5` sits mid-band.
- `app/roles.py` — post-diarization pass (behind the flag): embed each cluster,
  greedy one-to-one cosine match to enrollments, relabel + infer Client. Pure
  matching/inference/relabel functions (torch-free-testable) + injectable embedder.
  Wired into `pipeline.transcribe` flag-gated, off-thread.
- `tests/test_roles.py` — 14 torch-free tests with a FakeEmbedder. **Full suite
  53 passing** (39 prior + 14).

**Acceptance (verified, `ml/enroll/verify_e2e.py`):** real enrollment + real
pyannote diarization + real embedding on `2spk_long` → voice A's turns render as
`Therapist`, the other as `Client`; Therapist-labeled time overlaps truth-A
**27.0s vs B 0.0s** (right speaker); no anonymous labels remain. **PASS.**

**Notes:**
- **Zero new service dependencies** — the wespeaker embedding model
  (`pyannote/wespeaker-voxceleb-resnet34-LM`) rides the existing pyannote/torch
  stack (speechbrain is already transitively present). No `requirements.txt`
  change; `Dockerfile.cpu` already `COPY app/`, so a rebuild ships `embed.py` +
  `roles.py` automatically.
- The **running** prod container predates this code; it keeps serving with the
  flag off (no behavior change). To actually enable: rebuild the service image,
  enroll a voice into `ENROLLMENTS_DIR`, set `ENABLE_ROLE_LABELS=1`, restart.
- Two truth speakers only get Therapist/Client; 3+ speakers relabel the enrolled
  voice and leave the rest anonymous (deliberate — no basis to name them).

**Deferred / on deck:** Infra I (GPU diarization), Track C (LoRA scaffold).

---

## 2026-07-12 — Operator-driven UX batch (language, speaker editing, output language)

Shipped and **deployed to the live GPU stack** off operator feedback while testing
real (public, non-PHI) therapy clips. Branches `pwa-manual-controls` then
`output-language`.

**Trigger:** a real clip came back as "Welsh gibberish" — Whisper auto-detected
`cy` from an unfiltered video intro and rendered the whole English session in
Welsh. Re-running with English forced fixed it. Motivated language control +
the standing manual-override principle.

**Done:**
- **Language control** — PWA *Audio language* selector (defaults to English so
  auto-detect can't silently mis-pick); API already accepted `language`.
- **Manual speaker editor** — `POST /v1/results/{id}/relabel` (one final speaker
  label per segment; re-renders + persists .txt/.json). PWA "✎ Edit speakers":
  rename a speaker everywhere, reassign an individual turn. The always-available
  override for imperfect auto-labeling. Verified live (relabeled a real transcript
  to 141 Therapist / 107 Lucy turns).
- **Output language (phase 1)** — `task=transcribe|translate` through
  API→pipeline→ASR. Whisper only outputs source-language or English, so the PWA
  *Output* selector offers exactly that, force-locked. Speaches routes translate
  to `/v1/audio/translations`. Verified live: synthetic Spanish clip →
  transcribe=Spanish, translate=English. Arbitrary target languages are **phase
  2** (needs a local translation model — NLLB/M2M-100).
- **SW deploy fix** — service worker fetches shell assets with `cache:"no-store"`
  (v4), so redeploys aren't masked by a stale HTTP-cached `app.js`.

Test suite **68 passing** (+15 over Track B: relabel, task routing/validation).
A browser smoke test caught a real `currentJobId` ReferenceError that would have
broken every render.

**Design principle recorded:** automation is assisted, not absolute — every
auto-labeling/detection feature keeps a manual override (see the manual-fallback
memory).

**Deferred / on deck:** Track B live-enable (needs an operator therapist clip),
Infra I (GPU diarization), output-language phase 2 (translation model), Track C.

---

## 2026-07-13 — Infra I: GPU diarization sidecar (deployed)

Moved the pipeline's dominant cost — pyannote diarization — off CPU and onto the
RTX 5090. Branch `infra-gpu-diarization`.

**Why a sidecar, not in-process:** the 5090 is Blackwell (sm_120) and needs
torch ≥2.7 / cu128, but transcribe-svc pins torch 2.0.1+cu117 (sm_90 max) because
pyannote.audio 3.1.1 forces it (B-003). Upgrading in place detonates the chain.
So it's split out like Speaches ASR: a `diarize-svc` container with a modern CUDA
stack (torch 2.8+cu128, pyannote.audio 3.3.2) exposing `POST /diarize` on the
shared tailscale netns (127.0.0.1:8002). transcribe-svc's pins are untouched.
Notably `lightning` is **no longer quarantined** on PyPI, so modern pyannote
installs cleanly now.

**Before/after (realtime factor = audio_seconds / wall_seconds, warm):**

| Path | Clip | Wall | Realtime factor |
|---|---|---|---|
| Before — CPU diarization (STATUS 2026-07-12) | 145.9 s | ~51 s | ~2.85× |
| After — GPU diarization sidecar | 88.8 s | 8.1 s | **~10.9×** |
| After — GPU diarization sidecar | 834 s | 96.2 s | **~8.7×** |

`diarize_device: cuda` confirmed in the transcript header; the sidecar logs
`POST /diarize 200`. The realtime factor now clears the plan's ≥8× target (the
longer the clip, the more diarization dominates, so 8.7× on 14 min vs 10.9× on
90 s).

**CPU fallback (operator requirement) — verified live:** with the sidecar
stopped, a transcription still succeeded with `diarize_device: cpu-fallback` and
correct speaker count. RemoteDiarizer health-checks per request and falls back to
in-process CPU pyannote on any sidecar failure; 4 unit tests cover it.

**Build gotchas (diarize-svc image) for next time:**
- pyannote 3.3.2 calls `hf_hub_download(use_auth_token=...)`, removed in new
  huggingface_hub → pin `huggingface_hub==0.25.2`.
- pyannote imports `matplotlib` at pipeline-load but doesn't pull it → add it.
- torch 2.6+ defaults `torch.load(weights_only=True)`, which rejects pyannote
  checkpoints (lightning passes `weights_only=True` *explicitly*) → the sidecar
  force-patches `torch.load` to `weights_only=False` (trusted HF checkpoint).

**Deferred / on deck:** Track B live-enable (operator clip), output-language
phase 2 (translation model), Track C. Both GPU services (Speaches + diarize)
share the one 5090; VRAM headroom is fine for large-v3 + pyannote.

---

## 2026-07-13 — Provider-neutral OIDC authentication bridge (Codex; built, not deployed)

**Codex implementation note for Claude:** this change intentionally preserves
the existing documentation structure and keeps Cognito behind a standard OIDC
boundary. AWS identity can be demonstrated while the API/GPU remain local, and
another conforming provider can replace it later.

**Done:**
- Added `AUTH_MODE=static|hybrid|oidc`. Static remains the default; hybrid keeps
  `API_TOKEN` as an emergency migration key; OIDC-only disables it.
- Added provider-neutral `AuthPrincipal` (`subject`, optional email, scopes,
  groups, method). The legacy key maps to `local-admin` so later ownership work
  can depend on one identity shape.
- Added RS256 JWT verification using provider discovery/JWKS, bounded caching,
  unknown-key rotation refresh, issuer/expiry/issued-at/application checks,
  Cognito access-token rules, ID-token rejection, and optional required scopes.
- Added public `GET /v1/auth/config` and protected `GET /v1/auth/me`.
- Replaced the PWA's normal persistent token flow with standard Authorization
  Code + PKCE login when configured. Access/refresh tokens and the hybrid
  fallback are session-scoped; an old `localStorage` token is migrated and
  deleted. Service-worker shell cache bumped to v5.
- Added `docs/AUTH.md` with exact Cognito setup and rollout/rollback steps.
- Verification: **85 tests pass**, Python syntax and JavaScript syntax pass,
  `git diff --check` passes, and a real local-browser static-mode smoke test
  confirmed the settings UI, token entry, sample load, and enabled action with
  no console errors.

**Not done / deliberately deferred:**
- No Cognito user pool or AWS resources were created; no real OIDC browser flow
  has been exercised yet. The operator must provide the AWS identity boundary.
- Not deployed to the Alienware stack yet.
- Storage is still global. Authentication identifies users but does not yet
  enforce transcript ownership; this is not multi-user-ready.
- Local sign-out clears app tokens but does not yet force provider-wide logout.

**Next:** provision a Cognito development pool/client, deploy in `hybrid` mode,
complete the synthetic rollout checklist in `docs/AUTH.md`, then decide whether
to proceed to per-user storage ownership or return to Track B live-enable.

---

## 2026-08-14 — Cold rebuild on the Alienware (fresh clone, empty Docker state)

The stack was **not** running. Docs from 2026-07-13 described it as deployed and
healthy here; reality was a fresh clone at a new path with no `.env` and a Docker
install holding zero transcribe-svc images, containers, or volumes. Rebuilt from
scratch and back online. Nothing about the *design* was wrong — this entry exists
because the docs asserted a running deployment that no longer existed, which cost
the first chunk of the session to discover.

**What had drifted:**

| Doc claim | Reality on 2026-08-14 |
|---|---|
| Stack deployed and healthy on the Alienware | No images/containers/volumes at all |
| Repo at `Github/<folder>/transcriberproject` | `C:\Users\<user>\Github\transcriberproject` |
| Reachable at `transcribe-svc.<tailnet>.ts.net` | Name held by a stale node; new node is `transcribe-svc-1` |
| `PRELOAD_MODELS` prevents the empty-Speaches trap | Silently ignored by the image (**B-005**) |

**Bring-up order that worked** (the sequencing matters — the three non-tailscale
containers share the tailscale netns and gate on its health, so nothing else can
start until Tailscale authenticates):

1. `docker compose build transcribe-svc` and
   `docker compose -f docker-compose.gpu.yml build diarize-svc` — neither needs a
   secret, so both can run while the operator is still fetching keys.
2. Verify GPU passthrough and HF access **before** bring-up (see below).
3. Pre-pull the Speaches model into `transcribe-svc_speaches_models` (**B-005**).
4. `up -d tailscale` alone first — it is the gate and the likeliest thing to fail
   (key/tag/ACL). Failing here with one container up is much easier to read than
   failing with four.
5. `up -d` for the rest.

**Verified live, this host:**

| Check | Result |
|---|---|
| GPU passthrough | RTX 5090 Laptop, 24463 MiB, driver 592.02 |
| Tailscale | authenticated, `tag:transcribe-svc`, healthy |
| ASR that served | `speaches@http://127.0.0.1:8001` (`Systran/faster-whisper-large-v3`) |
| Diarization device | `cuda` (`pyannote/speaker-diarization-3.1`) |
| Throughput (warm) | 90.9 s audio → **8.3 s wall ≈ 10.9× realtime** |
| Transcript | 2 speakers separated, `en`, 15 segments |
| PWA `/` + `/samples` | 200 |
| Auth | 401 unauthenticated |
| Listener posture | `0.0.0.0:8000` only; 8001/8002 loopback-only |

10.9× on a 90 s clip reproduces the 2026-07-13 GPU-diarization number exactly, so
this rebuild performs identically to the deployment it replaced.

**Two verifications promoted to pre-flight.** Both failure modes are invisible at
startup and only bite mid-job, so they now run before `up`:

- **HF token/model access** — a bad token does not fail at boot. `transcribe()`
  runs ASR and diarization under `asyncio.gather` with no `return_exceptions`, so
  a diarization load failure takes down the whole request. Check returns 200 for
  all three gated models (B-004).
- **Speaches model presence** — an empty Speaches reports *healthy* and degrades
  to CPU silently (B-005).

**Config changed for this host** (was tuned for the 15.6 GiB / 16-vCPU Proxmox VM;
this box has 24 CPUs and 31 GiB to Docker): `WHISPER_MODEL=large-v3`,
`MAX_CONCURRENT_JOBS=2`. These only affect the CPU fallback tier — GPU ASR model
selection is `ASR_MODEL_ID`.

**HTTPS enabled, same day.** `tailscale serve --bg 8000` →
**`https://transcribe-svc-1.example-tailnet.ts.net`**, cert verifies clean
(`ssl_verify_result=0`) from both inside the netns and the Windows host. Mic
capture and PWA install work. Serve config persists in `tailscale_state`.

**MagicDNS name is `transcribe-svc-1` and that is now permanent.** Worth recording
in full, because the obvious fixes all fail:

1. The stale `transcribe-svc` node was deleted — the name **did not** come back.
2. Container restart / full `down` + `up` — still `-1`. Node identity lives in the
   `tailscale_state` volume; reconnecting ≠ re-registering.
3. `tailscale set --hostname=transcribe-svc` — accepted, no change. The control
   plane keeps the name assigned at registration.
4. Admin-console rename — unavailable; the console won't override a
   hostname-derived name.

Only a wipe of `tailscale_state` forces fresh registration, and that requires a
known-**reusable** `TS_AUTHKEY`: every container gates on tailscale's health, so a
consumed single-use key would leave the entire stack down. Operator decision: not
worth the risk for a cosmetic name. Docs standardized on `transcribe-svc-1`.

**The actual lesson:** delete stale nodes *before* first bring-up. Afterward is
too late — MagicDNS assignment is sticky. `docs/DEPLOY.md` Tailscale prep now says
so explicitly.

**B-005 fixed, same day.** Three changes to `docker-compose.gpu.yml`:

1. **Healthcheck asserts the model**, not just that `/v1/models` answers. Proven
   against a throwaway Speaches on an empty volume — the old check exits 0 there
   (reports healthy), the new one exits non-zero (reports unhealthy). That empty
   state is the entire bug, so testing the negative case was the point.
2. **Image pinned by digest** (`@sha256:6ec12eb…`). The floating `:latest-cuda`
   is *how* this regressed with no change on our side. No usable semantic version
   label exists on the image, so a digest is the only precise pin.
3. **`speaches-model-init`**, a one-shot that actually downloads the model —
   restoring the behavior `PRELOAD_MODELS` was meant to provide. Idempotent,
   `restart: "no"`. Depends on speaches with `service_started`, **not**
   `service_healthy`: the new healthcheck requires the model this service
   downloads, so gating on healthy would deadlock.

`PRELOAD_MODELS` is retained with a comment saying it does nothing, so it isn't
re-added later as a "fix".

Verified after the change: init logged `already cached, nothing to do` (exit 0),
speaches healthy under the new check, HTTPS transcription at **~10.8× realtime**
with `speaches@…` / `cuda`. Note an unhealthy speaches still does not stop the
stack — `transcribe-svc` depends on it with `service_started`, preserving the
deliberate CPU fallback. What changed is that degradation is now visible in
`docker compose ps` rather than invisible.

**Still open:**
- Retired Proxmox node `transcriber` (100.69.164.58, offline 28d+) still in the
  tailnet — harmless, just debris.
- OIDC bridge still not deployed (no Cognito pool).

---

## 2026-08-14 — ML Track B live-enabled on real voices

Track B (voice enrollment → role labels) is **on**. One enrollment, `Speaker A`, built
from a 12.1 s solo clip the operator recorded through the PWA. `.env`:
`ENABLE_ROLE_LABELS=1`, `ROLE_MATCH_THRESHOLD=0.5`, `CLIENT_LABEL=Speaker`.
Verified live — the enrolled speaker is named in the transcript and the other
person stays anonymous.

**Two infrastructure bugs had to be fixed first, both of which would have made
this silently not work:**

1. **`/data/enrollments` was not a volume.** `ENROLLMENTS_DIR` defaults there, but
   compose only mounted `models`, `uploads`, `outputs` — so the voiceprint lived
   on the container filesystem and every `up -d` would have discarded it. Role
   labeling would then quietly stop with no error. Added an `enrollments` volume
   to both the GPU and CPU composes.
2. **The image never created `/data/enrollments`.** Docker seeds a fresh named
   volume's ownership from the directory it mounts over; with no such directory
   the volume mounted root-owned and enrollment died with `PermissionError` on
   save. Added it to `Dockerfile.cpu`'s mkdir/chown.

**Real-voice sweep — the first non-synthetic measurement.** Full report:
`ml/enroll/reports/2026-08-14-real-voice-sweep.md`.

| | Genuine | Impostor | Gap |
|---|---|---|---|
| Synthetic (2026-07-12) | 0.850–0.920 | 0.158–0.226 | 0.62 |
| **Real** | 0.590–0.718 | **0.128** | **0.46** |

Real voices separate less than synthetic, but the degradation is on the *genuine*
side (~0.15 lower); impostor similarity is essentially unchanged. So the practical
failure mode on real audio is a **missing** label, not a **wrong** one — the safe
direction for a medical transcript. 0.5 sits 0.37 above the impostor and 0.09
below the lowest genuine.

**A first pass got this wrong and briefly set 0.65.** The probe clip's initial
transcription never surfaced the second speaker at all — his Spanish went
untranscribed — so both available clusters were the *same* person and a genuine
score of 0.6113 was read as an impostor, implying a dangerously tight 0.126 gap.
Re-running the clip as WAV produced three speakers, the Spanish text, and a true
impostor at 0.1282. The corrected threshold buys real protection; 0.65 only
produced false negatives on the operator's own voice. **Lesson: establish who each
cluster actually is before treating a low score as an impostor score** — a "gap"
between two unidentified clusters is not evidence of anything.

**Known gap, not fixed:** Whisper can emit a segment ending past the audio
duration (29.84 s on a 22.87 s file). `compute_cluster_embeddings` crops out of
bounds, throws per-segment, and drops that cluster entirely with no error. Benign
here; if it hits the enrolled speaker's cluster, labeling silently no-ops.
Clamping segment ends to the audio duration in `app/roles.py` would fix it.
