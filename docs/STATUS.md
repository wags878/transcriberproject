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

