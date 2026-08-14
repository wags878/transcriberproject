# Blockers

Append-only log of acceptance items that could not be met, with what was attempted and what surfaced. The operator decides whether to push through or accept the limitation.

Format: `B-<NNN>` ID, status (open / resolved / accepted), date opened, brief description.

---

## B-001 — Current host does not match PROJECT_PLAN.md §2 hardware spec

**Status:** RESOLVED (acceptance item now actually met) — 2026-05-01
**Opened:** 2026-04-30
**Phase:** 0
**Acceptance item affected:** §6 Phase 0 — `lscpu | grep -i avx512` returns flags

### Update 2026-05-01 — acceptance item now met for real
Operator reconfigured the Proxmox VM. All three knobs that the spec assumed are now (closer to) in place:
- vCPUs 8 → **16**
- RAM 7.7 GiB → **15.6 GiB**
- CPU model: generic QEMU vCPU → **`Intel(R) Xeon(R) Silver 4214` host-passthrough** with full AVX-512 set including `avx512_vnni` (the int8 VNNI extension that CTranslate2 uses on the int8 compute path)

`lscpu | grep -oE 'avx512[a-z_]*' | sort -u` now returns `avx512bw avx512cd avx512dq avx512f avx512vl avx512_vnni`. Phase 0 §6 acceptance item is **fulfilled**, not just accepted-as-deviated.

Measured impact (5-min fixture, medium model, concurrency=1, same as 2026-04-30 baseline):
- Old: 1236 s wall (4.12× slower than realtime)
- New: 621.8 s wall (2.07× slower than realtime)
- ~2× wall reduction. Consistent with double the cores plus AVX-512 VNNI on the int8 path.

Per-job memory peak now well under ceiling (≤ 5.6 GiB / 15.6 GiB on either model). Path to spec defaults reopened; see `docs/HARDWARE.md` "Performance baseline" for the run table behind these numbers.

---

### (Original 2026-04-30 acceptance kept below for the historical record.)

### Original Resolution (2026-04-30) — superseded by 2026-05-01 update above
Operator decision: this Proxmox-hosted VM is the project home for the foreseeable future. Speed is not critical — the success bar is "works during a single therapy session," i.e. a 60-minute recording finishes processing in some reasonable time after upload, not in real time. Operator can pass through additional Proxmox resources on the fly when needed.

**Tuning applied to defaults:**
- `WHISPER_MODEL=medium` (was `large-v3` in spec) — ~1.5 GB RAM, ~2× faster than large-v3 on CPU. Operator can flip to `large-v3` after raising VM RAM to ~16 GiB.
- `MAX_CONCURRENT_JOBS=1` (was 2 in spec) — guarantees no OOM on 7.7 GiB. Operator can raise to 2 after raising VM RAM.
- AVX-512 absence accepted; faster-whisper falls back to AVX2 / generic SIMD. Performance baseline will be measured on the actual fixture and recorded in `HARDWARE.md` so we know what realistic throughput looks like.

**Path forward:** Phase 1 proceeds. If the operator raises VM RAM and/or switches Proxmox CPU mode to `host` passthrough later, only env vars need to change — no rebuild required.

---

### (Original investigation kept below for context.)

**Original status:** OPEN
**Original phase:** 0
**Original acceptance item:** §6 Phase 0 — `lscpu | grep -i avx512` returns flags

### What the spec assumes
- 2× Intel Xeon Silver, 128 GB DDR4 ECC, AVX-512 available
- Memory budget §2: ~8–10 GB per concurrent transcription, MAX_CONCURRENT_JOBS=2 (~20 GB peak), trivial against 128 GB
- Performance estimate §2: 3–8× realtime on faster-whisper int8 with AVX-512

### What was found on the current host
- QEMU Virtual CPU version 2.5+ (generic QEMU vCPU, not passing through Xeon flags)
- 8 vCPUs (2 sockets × 4 cores × 1 thread), GenuineIntel reported
- **No AVX-512 flags** (`lscpu | grep -oE 'avx512[a-z_]*'` empty)
- **7.7 GiB RAM total** (5.2 GiB already used by host workload, ~250 MiB free, 2.3 GiB buff/cache)
- 4 GiB swap

### Implications

1. **AVX-512 absence:** faster-whisper / CTranslate2 falls back to AVX2 or generic SIMD paths. Realistic throughput drops to roughly 1–2× realtime instead of 3–8×, possibly worse for `large-v3`. A 60-minute recording could take 30–60+ minutes instead of 8–20 minutes.
2. **RAM ceiling:** with only 7.7 GiB total and significant existing usage, loading whisper-large-v3 (~3 GB) + pyannote diarization (~2 GB) + alignment model (~2 GB) + FastAPI (~1 GB) puts us right at the edge. **MAX_CONCURRENT_JOBS=2 will OOM.** Need to either:
   - Reduce concurrency to 1
   - Use a smaller whisper model (`medium` ~1.5 GB, `small` ~500 MB)
   - Skip the alignment step
   - Wait for target hardware
3. **QEMU vCPU flags:** the host is exposing a generic QEMU CPU model rather than passing through host CPU flags. If this VM is meant to be the deployment target, the hypervisor should be reconfigured to use `host` or `host-passthrough` CPU mode so guest sees real Xeon Silver flags including AVX-512 (assuming the underlying physical host has them).

### Open questions for operator

- Is this VM (`transcriber`, 100.x.y.z) the actual deployment target, or a dev/staging machine?
- If it is the target: can the hypervisor be reconfigured to pass through host CPU flags? Can the VM be sized up to 32+ GiB RAM at minimum, ideally close to the 128 GiB budgeted?
- If it's dev-only: should development proceed here with reduced ambitions (smaller model, concurrency=1) and reserve the perf-baseline acceptance item for the real target?

### Resolution options being considered

- (a) Reconfigure VM hypervisor: `cpu: host` mode, raise RAM allocation. Re-measure.
- (b) Treat current host as dev/staging only. Develop with `medium` model + concurrency=1; defer perf baseline to deployment time on real hardware.
- (c) Accept limitation: ship with current hardware, document degraded performance in `docs/HARDWARE.md`.

Pending operator input.

---

## B-002 — `transcriber` user not in `docker` group

**Status:** RESOLVED — 2026-04-30 (operator ran `sudo usermod -aG docker transcriber`). Existing shells in this session use `sg docker -c '...'` until they re-login; new shells inherit the group naturally.
**Opened:** 2026-04-30
**Phase:** 0 (gap — should have been caught earlier)

The `docker.sock` is owned by `root:docker` (mode `srw-rw----`). User `transcriber` is in `sudo` but not in `docker`, so any `docker compose build/run/up` from this user fails with:

```
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

This was missed during Phase 0 because I only ran `docker --version` (no socket access required), not `docker run --rm hello-world` as called for in §6 Phase 0 acceptance step 1. Logging the gap.

### Fix
Operator runs (one time):

```sh
sudo usermod -aG docker transcriber
# Then either logout/login OR run `newgrp docker` in any shells that need it.
```

Verify:

```sh
groups | tr ' ' '\n' | grep -x docker  # should print "docker"
docker run --rm hello-world             # should succeed without sudo
```

After that, the `docker compose build` will work normally.

---

## B-003 — `lightning` PyPI package is quarantined; constrains whisperx version

**Status:** RESOLVED (worked around) — 2026-04-30
**Phase:** 1
**Discovered during:** first two builds attempts

### What surfaced
- First build attempt failed: `whisperx==3.2.0` → `faster-whisper==1.0.0` → `av==11.*`. PyAV 11 has no prebuilt wheel for python 3.11 on Debian 13 (Python 3.11-slim base). Pip tried to source-build and failed with `pkg-config is required for building PyAV`.
- Second build attempt (after bumping `whisperx>=3.3.0,<4`) failed differently: pip backtracked through whisperx 3.7.4 → 3.3.0 trying to satisfy constraints, then resolved `pyannote-audio>=3.3.2` → `lightning>=2.0.1`. Pip reported "Could not find a version that satisfies the requirement lightning>=2.0.1".
- Direct probe confirmed: `https://pypi.org/simple/lightning/` returns the page with `pypi:project-status="quarantined"` and **zero file links**. PyPI has quarantined the `lightning` package. `pytorch-lightning` (the older/separate PyPI name) is unaffected.

### Why this matters
- Pyannote-audio renamed its dependency from `pytorch-lightning` → `lightning` between 3.2 and 3.3. Versions ≥3.3.2 cannot install through PyPI alone while `lightning` is quarantined.
- Whisperx ≥3.3 requires pyannote-audio ≥3.3.2 transitively. So the entire whisperx 3.3+ line is blocked through PyPI.

### Resolution
- Pin `whisperx==3.2.0`, which uses `pyannote-audio==3.1.1`, which uses `pytorch-lightning` (active on PyPI).
- Accept the consequence: `av==11.*` has to source-build. Dockerfile.cpu now installs `pkg-config + libavcodec-dev + libavformat-dev + libavutil-dev + libswresample-dev + libswscale-dev` so the source build succeeds. Adds ~50 MB to the image and a couple minutes to the build.

### When to revisit
Watch for two signals:
1. PyPI un-quarantines `lightning` (check `https://pypi.org/simple/lightning/` — when there are file links again, that's the cue).
2. A whisperx release that drops the lightning chain or that vendors lightning differently.

When either happens, bump `whisperx` to a 3.3+ release and remove the libav-dev safety net from the Dockerfile (PyAV 12+ has prebuilt wheels).

---

## B-004 — `pyannote/speaker-diarization-community-1` requires HF token after all

**Status:** RESOLVED — 2026-08-14 (operator supplied `HF_TOKEN`; access verified)
**Opened:** 2026-04-30
**Phase:** 1
**Acceptance item affected:** §6 Phase 1 — fixture WAV → 200 + speaker-labeled `.txt`

### What the spec said
PROJECT_PLAN.md §8 Q7 (and the operator's 2026-04-30 decision) chose `pyannote/speaker-diarization-community-1` specifically because it was supposed to be CC-BY-4.0 with **no HF account or token required** — distinct from the gated `speaker-diarization-3.1` model.

### What actually happened
On first transcription attempt, pyannote.audio returned:

```
Could not download 'pyannote/speaker-diarization-community-1' pipeline.
It might be because the pipeline is private or gated so make sure to authenticate.
Visit https://hf.co/settings/tokens to create your access token and retry...
If this still does not work, it might be because the pipeline is gated:
visit https://hf.co/pyannote/speaker-diarization-community-1 to accept the user conditions.
```

Even though the model's *license* is CC-BY-4.0, HuggingFace gates the *download* behind: (a) accepting the model's user-conditions page, and (b) presenting a HF access token. There's no anonymous download path through pyannote.audio's loader.

### Resolution
Operator needs to do, one time:
1. Create / log in to a HuggingFace account.
2. Visit `https://hf.co/pyannote/speaker-diarization-community-1` and accept the user conditions.
3. Generate a Read token at `https://hf.co/settings/tokens`.
4. Paste it into `.env` on the `HF_TOKEN=` line.

Once `HF_TOKEN` is set, the pipeline will download the model on next request and cache it to the `models` named volume; subsequent restarts skip the download.

### Spec correction
Update `project_decisions.md` (memory) and PROJECT_PLAN.md §8 Q7 next time it's revised: a HF account + token are required even for `community-1`. The community vs 3.1 distinction is about the *license* (CC-BY-4.0 vs custom), not about download authentication.

### Resolution (2026-08-14)
Operator populated `HF_TOKEN` in `.env` during the cold rebuild. Verified before
first transcribe rather than discovering it mid-job — all three models the stack
needs return HTTP 200 for that token:

```sh
HF=$(awk -F= '/^HF_TOKEN=/{print $2}' .env)
for m in pyannote/speaker-diarization-3.1 \
         pyannote/segmentation-3.0 \
         pyannote/wespeaker-voxceleb-resnet34-LM; do
  curl -s -o /dev/null -w "$m -> %{http_code}\n" \
       -H "Authorization: Bearer $HF" "https://huggingface.co/api/models/$m"
done
```

A 403 on any line means the conditions page for that model has not been accepted.
Worth running on any fresh clone: a bad/missing token does not fail at startup,
it fails inside the first transcription (see the `asyncio.gather` note in B-005).

---

## B-005 — Speaches ignores `PRELOAD_MODELS`; healthcheck passes with no model

**Status:** RESOLVED — 2026-08-14 (fix applied and verified; see Resolution below)
**Opened:** 2026-08-14
**Phase:** Infra (cold rebuild)
**Affects:** `docker-compose.gpu.yml` (speaches service)

### What surfaced

During the 2026-08-14 cold rebuild, `ghcr.io/speaches-ai/speaches:latest-cuda`
was started with exactly the compose's environment — `PRELOAD_MODELS`,
`WHISPER__COMPUTE_TYPE`, telemetry off — and downloaded **nothing**:

```
$ curl -s http://127.0.0.1:8001/v1/models
{"data":[],"object":"list"}
```

The startup config dump the image prints contains no preload field at all, so the
variable is simply not read by the current image.

### Why this is worse than a slow first request

The compose healthcheck is:

```yaml
test: ["CMD", "curl", "-fsS", "http://127.0.0.1:8001/v1/models"]
```

`/v1/models` returns **HTTP 200 with an empty list** when no model is present. So
the container is marked **healthy** while being unable to serve a transcription.
The failure then surfaces one layer up and silently: `ASRRouter` health-checks
Speaches, sees it up, sends the job, gets a 404 for the un-downloaded model, and
falls back to `local-whisperx` on CPU. The operator sees a *successful* response
that took 5–10× longer than expected, with no error anywhere.

This is the exact failure the line-62 comment in `docker-compose.gpu.yml` records
as "learned at deploy" — `PRELOAD_MODELS` was added to prevent it, and has since
silently stopped doing so. The guard rotted; the trap did not.

### Workaround applied 2026-08-14

Pre-pull the model into the compose volume before first `up`. The image does
expose a working download endpoint:

```sh
docker run -d --name speaches-prewarm --gpus all \
  -v transcribe-svc_speaches_models:/home/ubuntu/.cache/huggingface \
  ghcr.io/speaches-ai/speaches:latest-cuda
docker exec speaches-prewarm \
  curl -s -X POST "http://127.0.0.1:8001/v1/models/Systran/faster-whisper-large-v3"
docker rm -f speaches-prewarm     # volume persists; compose reuses it
```

~2.9 GB, a few minutes. Compose then starts Speaches with the model already
cached. (Compose warns the volume "was not created by Docker Compose" — cosmetic.)

### Real fixes to choose between

1. **Assert the model, not the endpoint** — make the healthcheck prove the model
   is actually loaded, so an empty Speaches never reads as healthy:
   ```yaml
   test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8001/v1/models | grep -q faster-whisper-large-v3"]
   ```
   Cheapest change and it closes the silent-fallback hole directly.
2. **Pin a Speaches tag that honors `PRELOAD_MODELS`** — `latest-cuda` is a
   floating tag, which is how this regressed unnoticed. Pinning also satisfies the
   "pull only after Phase 4 pins SHAs" note in `DEPLOY.md`.
3. **Make the fallback loud.** `ASRRouter` (`app/asr.py`) has two distinct
   fallback paths and neither reaches the caller:
   - backend fails `health()` → logged at **info**, skipped;
   - backend passes `health()` then raises mid-request → logged at **warning**,
     falls through to the next backend.

   The B-005 case takes the *second* path: Speaches is healthy, 404s the job,
   gets caught, and `local-whisperx` serves it — so the response is a normal 200
   and the only trace is one warning line. The transcript header does record the
   truth in `asr_backend`, but nothing surfaces it at request time. Surfacing
   degradation in `/v1/health` (or a response header) would make this class of
   regression visible without reading logs.

Recommend (1) + (2) together: (1) stops the bad state from being called healthy,
(2) stops the floating tag from changing behavior under us again.

### Resolution (2026-08-14) — all three applied

**1. Healthcheck asserts the model, not the endpoint.**

```yaml
test:
  - CMD-SHELL
  - curl -fsS http://127.0.0.1:8001/v1/models | grep -q "${ASR_MODEL_ID:-Systran/faster-whisper-large-v3}"
```

Verified against a throwaway Speaches on an empty volume — the decisive test,
since the whole bug is that the *old* check passed in exactly this state:

| Healthcheck | Empty Speaches | Verdict |
|---|---|---|
| old — `curl -fsS /v1/models` | exit **0** | reports HEALTHY ← the bug |
| new — grep for model id | exit **non-zero** | reports UNHEALTHY ← fixed |

**2. Image pinned by digest.** `:latest-cuda` →
`@sha256:6ec12ebf890a17e0d4b242a8ba9e0eb1fb836e60e8a3c857aea9838d541579ac`.
A floating tag is *how this regressed with no change on our side*; the image has
no usable semantic version label (`org.opencontainers.image.version` is the Ubuntu
base, `24.04`), so the digest is the only precise pin.

**3. Root cause fixed, not just detected** — new one-shot `speaches-model-init`
service restores the "a fresh deploy needs no manual pull" behavior
`PRELOAD_MODELS` was supposed to give. It waits for the API, exits immediately if
the model is cached, otherwise POSTs `/v1/models/{id}` (which *does* work) and
verifies the result. Idempotent, `restart: "no"`, so it is a no-op on subsequent
`up`. It depends on speaches with `service_started`, **not** `service_healthy` —
the new healthcheck requires the model this service downloads, so waiting for
healthy would deadlock.

`PRELOAD_MODELS` is deliberately left in place with a comment explaining it does
nothing, so nobody "fixes" its absence by re-adding it.

**Verified end to end after the change:** init logged `already cached, nothing to
do` and exited 0; speaches healthy under the new check; transcription over HTTPS
returned `asr_backend: speaches@…`, `diarize_device: cuda`, 2 speakers, at
**~10.8× realtime**.

**Residual risk:** the healthcheck greps for `ASR_MODEL_ID`, so changing that
variable without the model being present makes speaches unhealthy until
`speaches-model-init` fetches it — which is the intended, visible behavior rather
than a silent CPU fallback. Note also that unhealthy does **not** stop the stack:
`transcribe-svc` depends on speaches with `service_started`, preserving the
deliberate CPU-fallback design. The difference is that the degradation is now
visible in `docker compose ps` instead of invisible.
