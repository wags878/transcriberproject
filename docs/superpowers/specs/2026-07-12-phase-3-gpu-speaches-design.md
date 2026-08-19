# Phase 3 — GPU acceleration via Speaches sidecar

**Date:** 2026-07-12
**Branch:** `phase-3-gpu`
**Status:** DESIGN — awaiting operator review before implementation plan
**Supersedes/complements:** `PROJECT_PLAN.md` §6 Phase 3 (GPU path)

## 1. Why this exists

Phase 2 shipped a working CPU pipeline on the Proxmox VM (`transcribe-svc:cpu`, `WHISPER_MODEL=large-v3`, `MAX_CONCURRENT_JOBS=2`). Per `docs/STATUS.md` 2026-05-01, a 60-minute recording finishes in ~120 minutes wall clock. That's an order of magnitude off the "usable during a therapy day" target.

Two hosts newly on the tailnet unlock the fix:

- **Alienware Area-51** — i9 / 64 GB / **mobile RTX 5090 (24 GB VRAM)**, Windows 11, plugged in.
- **MacBook Pro M5 / 64 GB unified** — fallback tier for when the Alienware is unavailable.

The 5090 can drive `large-v3` inference at roughly 10× realtime, collapsing the 60-min recording from ~120 min wall to ~10 min wall (bounded by CPU-side pyannote diarization, not ASR).

## 2. Hard constraint we discovered

**Our existing container cannot use the 5090 without a full rebuild.**

- 5090 is Blackwell, compute capability sm_120.
- sm_120 requires CUDA 12.8 and PyTorch ≥ 2.7.
- Our container pins `whisperx==3.2.0` (from B-003 resolution), which requires `torch==2.0.1`.
- torch 2.0.1 was compiled for sm_90 max — the 5090 is unusable from it.
- [WhisperX issue #1211](https://github.com/m-bain/whisperX/issues/1211) confirms Blackwell is unsupported in the 3.2.x line.

Two paths considered:

- **Path 1 (chosen):** Split ASR into a separate Speaches container that ships its own modern CUDA 12.8 + PyTorch stack. transcribe-svc's own image is untouched; `pipeline.py` gains an OpenAI-compat ASR client that calls Speaches over HTTP. pyannote stays local in transcribe-svc.
- **Path 2 (rejected):** Rebuild transcribe-svc's image from scratch on CUDA 12.8 + PyTorch 2.7+ + a modern whisperx line. Re-solves the B-003 lightning/PyAV pin chain from scratch. Substantially bigger job (~1 day dep archeology + retest all fixtures) and drops the working state we validated in Phase 1/2.

**Rationale for Path 1:** Contains the version-pin blast radius to a single new sidecar with a maintained upstream Docker image (`ghcr.io/speaches-ai/speaches`). Our own image stays on the pin chain we already made work. Escape hatch: if Phase 3 gets abandoned, `main` still ships a working service.

## 3. Architecture

```
                Client (iPhone PWA / etc.)
                            │
                            │  POST /v1/transcribe (audio + bearer)
                            │  via Tailscale MagicDNS
                            ▼
              ┌──────────────────────────────────────────────┐
              │  Alienware (Windows 11 + WSL2 + Docker)      │
              │                                              │
              │   ┌─────────────────────────────────────┐    │
              │   │  tailscale sidecar container        │    │
              │   │  hostname = transcribe-svc         ├──── shared netns
              │   │  tag:transcribe-svc                 │    │
              │   └─────────────────────────────────────┘    │
              │                    │                          │
              │        (all following share the tailscale     │
              │         container's network namespace)        │
              │                    │                          │
              │   ┌────────────────┴──────────────────────┐   │
              │   │  transcribe-svc container (CPU-only)  │   │
              │   │   - FastAPI (port 8000)               │   │
              │   │   - pyannote diarization (CPU)        │   │
              │   │   - OpenAICompatASR client            │   │
              │   │   - stitcher                          │   │
              │   │   - storage / retention               │   │
              │   └───────────────────┬───────────────────┘   │
              │                       │                       │
              │       POST /v1/audio/transcriptions           │
              │              (localhost:8001)                 │
              │                       │                       │
              │   ┌───────────────────▼───────────────────┐   │
              │   │  speaches container (GPU)             │   │
              │   │   - CUDA 12.8, PyTorch 2.7+           │   │
              │   │   - faster-whisper large-v3           │   │
              │   │   - int8_float16 compute type         │   │
              │   │   - binds 127.0.0.1:8001 only         │   │
              │   └───────────────────────────────────────┘   │
              │                       │                       │
              │              --gpus all (NVIDIA CT)           │
              │                       ▼                       │
              │            RTX 5090 (24 GB VRAM)             │
              └──────────────────────────────────────────────┘
```

**Network topology note.** All three containers share the tailscale sidecar's netns via `network_mode: service:tailscale`. Result:

- The tailscale container gets one node identity; the whole stack is reachable as `transcribe-svc.<tailnet>.ts.net`.
- `transcribe-svc` binds to `0.0.0.0:8000` — reachable over Tailscale only, because there is no host port binding.
- `speaches` binds to `127.0.0.1:8001` — reachable only from within the shared netns, i.e. only from `transcribe-svc`. Not exposed on the tailnet.
- No `-p` host port mappings anywhere in the compose file.

## 4. Data flow (single request)

1. Client uploads audio to `POST /v1/transcribe` (bearer-authed).
2. `transcribe-svc` writes audio to `uploads_dir`, allocates a job UUID.
3. Two concurrent tasks kick off via `asyncio.gather`:
   - **Task A:** POST audio bytes to `http://localhost:8001/v1/audio/transcriptions` with `response_format=verbose_json`. Speaches returns `{text, segments:[{start,end,text}]}`.
   - **Task B:** Load audio, run pyannote pipeline locally on CPU. Returns speaker turns `[{start, end, speaker}]`.
4. **Stitch:** for each ASR segment, find the pyannote turn(s) that overlap it, tag the segment with the dominant speaker.
5. Render `.txt` (paragraph-per-turn, same format as Phase 2) and `.json` (WhisperX-schema-compatible header + segments + speakers).
6. Client polls `/v1/results/{id}/transcript.{txt,json}` — unchanged from Phase 2.

Wall clock, 60-min recording, expected: `max(pyannote_cpu ≈ 5-10 min, speaches_gpu ≈ 2-4 min) + stitch ≈ ~10 min`. Pyannote CPU is the new bottleneck; accepted for Phase 3.

## 5. Fallback chain

`ASR_HOSTS` env is a comma-separated list. Client tries each in order, first healthy responder wins.

Default for the Alienware deployment:

```
ASR_HOSTS=http://localhost:8001,http://mbp.tailnet.ts.net:8001,local-whisperx
```

- **`localhost:8001`** — Speaches on same host (fastest path).
- **`mbp.tailnet.ts.net:8001`** — LM Studio isn't shipping `/v1/audio/transcriptions` yet, so Mac tier uses `whisper.cpp` server (`--inference-path /v1/audio/transcriptions`) with the `ggml-large-v3.bin` model. Deployed separately, not part of this compose file.
- **`local-whisperx`** — sentinel string. Falls back to the in-container WhisperX pipeline we shipped in Phase 1/2. Runs on CPU. Slow but always available.

Health check: `HEAD /health` (or `GET /health`) with 2 s timeout per host. On first healthy responder, forward the request. If all fail, return HTTP 503 with an actionable error message.

## 6. What changes, what doesn't

**Unchanged (contract stability for the T4.0 test target and iPhone client):**

- `POST /v1/transcribe` request shape
- `GET /v1/results/{id}/transcript.{txt,json}` URLs
- `GET /v1/health`, `GET /v1/admin/storage`
- Bearer-token auth model
- `.txt` paragraph-per-turn format (Phase 2)
- `.json` schema (header + segments + speakers)
- Filename convention on disk (`<date>_<time>_<slug>_<uuid>.{txt,json}`)
- Retention rules (`RETAIN_DAYS`)

**Changed:**

- `app/pipeline.py` — new `OpenAICompatASR` client class; new `LocalWhisperXASR` fallback class (extracted from current code); `Pipeline.transcribe()` runs pyannote and ASR concurrently, then stitches.
- `app/config.py` — new settings: `ASR_HOSTS`, `ASR_HEALTHCHECK_TIMEOUT_S`, `ASR_MODEL_ID`, `ASR_RESPONSE_FORMAT`.
- `.env.example` — document new settings, add `TS_AUTHKEY` block.
- `docker-compose.gpu.yml` — replace scaffold with the three-service topology above. Retire the old `Dockerfile.gpu` (unused).
- `docker/Dockerfile.cpu` — unchanged.
- `tests/` — new stitcher tests; new OpenAI-compat client tests (mock Speaches responses); existing fixtures re-run through Path 1.
- `docs/DEPLOY.md` — Windows/WSL2/Docker-Desktop section; power-plan checklist; Tailscale auth-key setup.
- `docs/STATUS.md` — Phase 3 close-out entry after acceptance gate is met.

## 7. Windows 11 host prerequisites (operator-side)

1. NVIDIA driver v566+ (Blackwell support) — WSL2-compatible Studio or Game Ready. Install, reboot.
2. Windows 11 22H2+ fully patched. Virtualization enabled in BIOS.
3. `wsl --install -d Ubuntu-22.04` in admin PowerShell.
4. Docker Desktop with WSL2 backend, Ubuntu-22.04 integration enabled.
5. Power settings:
   - Never sleep (plugged in): Settings → System → Power & battery.
   - Lid close action: Do nothing (both battery and plugged in).
6. Defender Firewall: allow Docker Desktop on both private and public profiles.
7. Verify GPU passthrough:
   ```bash
   docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi
   ```
   Must show the 5090. If this fails, stop — nothing downstream will work.
8. Tailscale admin console:
   - Confirm `tag:transcribe-svc` and `tag:transcribe-client` are in the ACL policy.
   - Generate a **reusable, pre-authorized** auth key tagged `tag:transcribe-svc`. Save for `.env`.

## 8. Acceptance criteria for Phase 3

Adapted from `PROJECT_PLAN.md` §6 Phase 3, tightened to this design:

- [ ] `docker compose -f docker-compose.gpu.yml up -d` on Alienware brings the three-container stack up cleanly.
- [ ] `curl https://transcribe-svc.<tailnet>.ts.net/v1/health` returns `{status:ok, device:cuda, ...}`.
- [ ] `nvidia-smi` on host shows Speaches holding VRAM (< 4 GB) after model load.
- [ ] Existing 39.5 s and 5-min fixtures pass end-to-end via Path 1 (Speaches ASR + local pyannote).
- [ ] Wall clock for 5-min fixture ≤ 90 s (target: ≤ 60 s).
- [ ] `.txt` and `.json` outputs schema-equivalent to Phase 2 outputs on the same fixture.
- [ ] Speaker labels attributed correctly on the two-speaker fixture.
- [ ] Killing Speaches container → next request falls through to local-whisperx (CPU) and still succeeds.
- [ ] `/v1/admin/storage` unchanged in shape.
- [ ] `docs/DEPLOY.md` Windows/WSL2 section end-to-end followable by operator.

## 9. PHI hard constraint (unchanged from 2026-07-11 STATUS entry)

- This service transcribes therapy sessions. Content is PHI.
- Speaches receives raw audio bytes. Speaches must **only** run on the Alienware inside the operator's tailnet. No outbound telemetry (`SPEACHES_ANALYTICS=false` if such a knob exists — will confirm during implementation).
- `RETAIN_DAYS=30` behavior unchanged.
- **The test harness runs only against a clean instance with synthetic audio**, never against one holding real recordings — same rule as 2026-07-11.

## 10. Rollback / escape hatch

- All Phase 3 work stays on `phase-3-gpu` branch until acceptance gate is met and operator approves merge.
- `main` continues to ship the Phase 2 CPU state that the T4.0 test target depends on. Nothing on `main` is broken by this work.
- If Phase 3 stalls or is abandoned, deleting the branch loses only speculative work.
- A pointer entry on `main`'s `docs/STATUS.md` documents the branch's existence so `main` isn't blind to it.

## 11. Open questions (to resolve during writing-plans or implementation)

1. Does Speaches expose a `HEAD /health` we can cheaply probe, or do we probe `GET /health` / `GET /v1/models`?
2. What compute type does the 5090 actually prefer — `int8_float16` (assumed) vs. `float16` vs. `bfloat16`? Bench during acceptance, adjust `.env`.
3. Does Speaches' `verbose_json` return segment timestamps precise enough for our stitcher, or do we need word-level (`timestamp_granularities=word`)? Empirical check.
4. When both LM Studio and Alienware are down and we fall through to `local-whisperx`, does that path still succeed on the Alienware (CPU is x86_64, whisperx pins should still resolve)? Or does the fallback only work back on the Proxmox VM?
5. `docker-compose.gpu.yml` currently has an unused `Dockerfile.gpu` scaffold. Retire it or repurpose it? Leaning retire.

## 12. Deferred to later phases

- **Diarization on GPU.** Operator OK'd CPU pyannote for Phase 3. If wall-clock disappoints, Phase 3.1 or 4 could ship a pyannote-on-GPU worker.
- **Word-level timestamps in `.json` output.** Phase 2 dropped from Phase 1's word-timings because paragraph rendering worked without them. If reintroduced, `timestamp_granularities=word` is the switch.
- **Multi-token auth.** Phase 4.
- **On-device iPhone transcription.** Discussed 2026-07-12; future exploration, requires native iOS client.
- **Edge appliance (Jetson Orin Nano).** Discussed 2026-07-12, tabled.
