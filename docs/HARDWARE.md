# Hardware

Records what the service is actually running on. Fill in the *Target deployment* column once it differs from *Current host*.

## Status

> **Read the host sections in order.** The active deployment is the **Alienware
> RTX 5090** (GPU stack). The Proxmox VM section that follows it is the retained
> CPU fallback and is no longer where the service runs — several of its
> conclusions (AVX-512 tuning, thread counts, whisper model sizing) apply only to
> that CPU path.

> **Spec alignment note (CPU host only).** Phase 0 §6 acceptance item (`lscpu | grep -i avx512` returns flags) is now **fulfilled** as of 2026-05-01 on the Proxmox VM. That host is still smaller than the spec's 128 GB / 12-core target, but the SIMD path the spec depends on is in place. See `docs/BLOCKERS.md` entry **B-001** for history. Moot for the GPU stack, which does not use the AVX-512 int8 path for ASR.

## Current host — Alienware (home of record since 2026-07-12; re-verified 2026-08-14)

The GPU stack runs here. The Proxmox VM below is retained as the documented CPU
fallback, not the active deployment.

| Field | Value |
|---|---|
| Repo path | `C:\Users\<user>\Github\transcriberproject` |
| OS | Windows 11 Pro 10.0.26200 |
| CPU | 24 logical processors available to Docker |
| RAM available to Docker | **31.07 GiB** |
| GPU | **NVIDIA GeForce RTX 5090 Laptop GPU, 24463 MiB** (Blackwell, sm_120) |
| NVIDIA driver | 592.02 (CUDA 13.1) — spec minimum is v566+ |
| Docker Desktop | 4.84.0 (engine 29.6.2), WSL2 backend, `nvidia` runtime present |
| Tailscale node | `transcribe-svc-1` / 100.x.y.z, `tag:transcribe-svc` |
| Tailnet | `example-tailnet.ts.net` (HTTPS certs available) |
| Disk free (C:) | 1.4 TB |

Image sizes on this host (2026-08-14): `transcribe-svc:cpu` 14 GB,
`speaches:latest-cuda` 8.6 GB, `transcribe-svc-diarize:cuda` 4.4 GB. Plus ~2.9 GB
of Whisper weights in `transcribe-svc_speaches_models`. Budget ~30 GB for a cold
rebuild before build cache.

**VRAM:** ~5.6 GiB resident with large-v3 + pyannote both loaded, against 24 GiB —
ample headroom for the two GPU services to share the one card. Speaches evicts its
model after `ttl=300` (5 min idle) and reloads from local disk on the next
request, so idle VRAM drops to near zero. That is expected and is *not* a CPU
fallback.

**This host does not match `PROJECT_PLAN.md` §2 at all** — that spec described a
128 GB dual-Xeon CPU box with no GPU (B-001). It has been superseded in practice:
the GPU path beats the spec's own 3–8× realtime target. §2 should be rewritten
around this host next time it is revised.

## Previous host — Proxmox VM (measured 2026-05-01, after reconfig)

| Field | Value |
|---|---|
| Hostname | `transcriber` |
| OS | Ubuntu 24.04.4 LTS (`noble`) |
| Kernel | `6.17.0-22-generic` (HWE branch) |
| CPU | `Intel(R) Xeon(R) Silver 4214 CPU @ 2.20GHz` (host-passthrough — was generic QEMU vCPU on 2026-04-30) |
| Sockets / cores / threads | 4 sockets × 4 cores × 1 thread = **16 vCPUs** (was 8 vCPUs on 2026-04-30) |
| Vendor flags | GenuineIntel; **AVX-512 set present**: `avx512bw avx512cd avx512dq avx512f avx512vl avx512_vnni` (last one is what CTranslate2's int8 path uses) |
| RAM | **15.6 GiB total** (was 7.7 GiB on 2026-04-30) |
| Swap | 4.0 GiB |
| Tailscale IP | `100.x.y.z` |
| LAN IP | `192.168.1.100/24` (`ens18`) |
| Docker | v29.4.1 |
| Docker Compose plugin | v5.1.3 |
| Tailscale | 1.96.4 |
| UFW | Operator-disabled (per 2026-04-30 decision). Service will be reachable on LAN as well as tailnet — see `docs/STATUS.md`. |

## Target deployment (per PROJECT_PLAN.md §2)

| Field | Value |
|---|---|
| Host | Existing home server |
| CPU | 2× Intel Xeon Silver (specific SKU TBD) |
| RAM | 128 GB DDR4 ECC |
| AVX-512 | Required (spec §6 Phase 0 acceptance) |
| GPU | Old NVIDIA Tesla, 8 GB VRAM, no tensor cores — **not pursued** (operator decision 2026-04-30) |
| OS | Ubuntu 24.04 LTS |
| VM | Yes (hypervisor TBD; moot since GPU passthrough not pursued) |

## Performance baseline

| Run | Date | Audio length | Wall-clock | Realtime ratio | Notes |
|---|---|---|---|---|---|
| smoke | 2026-04-30 | 39.5 s (`short_two_speaker.wav`) | 186 s | 4.7× slower than realtime | First real run after model download. Whisper `medium` int8, pyannote 3.1, CPU. 2 speakers correctly diarized, English auto-detected. |
| baseline-5min | 2026-04-30 | 300 s (`five_minute.wav`) | 1236 s (20:36) | **4.12× slower than realtime** | Models warm. Same stack. Slight improvement over smoke because diarization setup cost amortizes over longer inputs. |
| smoke-post-rambump | 2026-05-01 | 39.5 s (`short_two_speaker.wav`) | 117 s wall / 80.1 s pipeline (per service log) | **2.0× slower than realtime** (pipeline-internal) | First run after the VM reconfig was thought to be RAM-only. Wall faster than the 2026-04-30 short run mostly because models were cached. The bigger speedup came from CPU-mode and vCPU bumps that were also applied — see Run A below. |
| Run A — medium / conc=1 | 2026-05-01 | 300 s (`five_minute.wav`) | 621.8 s wall / 621.6 s pipeline | **2.07× slower than realtime** (was 4.12× pre-reconfig) | Apples-to-apples vs the 2026-04-30 baseline-5min row. Same model + concurrency, only the VM hardware changed. **2× wall reduction** attributable to vCPU 8→16 + AVX-512 VNNI now exposed (RAM bump alone wouldn't speed CPU compute). Container peak 3.99 GiB; CPU peaked at ~386% (~4 of 16 vCPUs — single job doesn't saturate). 2 speakers correctly diarized, English. |
| Run B — large-v3 / conc=1 | 2026-05-01 | 300 s (`five_minute.wav`) | 815.3 s wall (incl. one-time large-v3 download) / 772.0 s pipeline | **2.57× slower than realtime** | First run with `WHISPER_MODEL=large-v3`. Only ~25% slower than `medium` for ~5× the parameter count — thanks to AVX-512 VNNI on the int8 path. Container peak 5.63 GiB / 15.6 GiB. Transcript on the clean studio fixture is essentially identical to medium's; quality differential will only show up on noisy / accented / overlap-heavy audio. |
| Run C — medium / conc=2 (parallel ×2) | 2026-05-01 | 300 s ×2 in parallel | Job 1: 1002 s wall / 976.5 s pipeline; Job 2: 753.7 s / 730.0 s | per-job 2.4–3.2× slower than realtime | Two simultaneous POSTs. Both returned 200 with valid bodies; no OOM. System-level throughput: 2 jobs in ~1002 s vs serialized 2×621.8 ≈ 1244 s → **~24% throughput gain**. Cost: per-job latency worsens 17–57%. Container peak 5.58 GiB combined; CPU peaked at ~1289% (~13 of 16 vCPUs — concurrency=2 is what actually exercises the new core count). |
| Run D — large-v3 / conc=2 (parallel ×2) | 2026-05-01 | 300 s ×2 in parallel | Job 1: 1246 s wall / 1228.8 s pipeline; Job 2: 996.9 s / 982.2 s | per-job 3.2–4.1× slower than realtime | The spec's intended config validated end-to-end. Both POSTs returned 200, valid schema, 2 speakers diarized. No OOM. Container peak **6.60 GiB / 15.6 GiB (42%)**; CPU peaked ~1340% (~13 of 16 vCPUs). System throughput: 2 jobs in ~1246 s vs serialized 2×772 ≈ 1544 s → **~24% throughput gain** (same ratio as Run C). Per-job latency penalty (1.27–1.59×) is the cost of running them concurrently; this is fine for "drop files in, pick them up later" but not for "I uploaded one file and want it ASAP." |

### GPU stack — Alienware RTX 5090

⚠️ **Different convention from the CPU table above.** These are *faster* than
realtime, quoted as a realtime **factor** (`audio_seconds / wall_seconds`, higher
is better). The CPU rows above quote "× slower than realtime" (lower is better).

| Run | Date | Audio | Wall | Realtime factor | Notes |
|---|---|---|---|---|---|
| GPU diarization sidecar | 2026-07-13 | 88.8 s | 8.1 s | **~10.9×** | Infra I close-out. Was ~2.85× with CPU diarization. |
| GPU diarization sidecar | 2026-07-13 | 834 s | 96.2 s | **~8.7×** | Longer clip — diarization dominates more, so the factor drops. |
| Cold-rebuild verification | 2026-08-14 | 90.9 s | 8.3 s | **~10.9×** | `samples/friendly_conversation.mp3`, warm. `asr_backend: speaches@…`, `diarize_device: cuda`, 2 speakers, `en`, 15 segments. Reproduces the 2026-07-13 number exactly after a full rebuild. |
| HTTPS path — **cold** (model evicted) | 2026-08-14 | 32.4 s | 35.3 s | **~0.9×** | `samples/quick_qa.mp3` over `https://transcribe-svc-1…`. **Still GPU** — `speaches@…` / `cuda` confirmed in the header. The whole gap is Speaches reloading large-v3 after `ttl=300` eviction. |
| HTTPS path — **warm** (same clip, immediately after) | 2026-08-14 | 32.4 s | 3.1 s | **~10.3×** | Identical request, model resident. 11× faster than the cold run on the same code path. |

**Measure warm, or you will misdiagnose the stack.** The cold/warm pair above is
the same clip, same request, same GPU backends — 0.9× vs 10.3×. A single short
clip submitted after ≥5 minutes idle spends most of its wall-clock reloading
weights, which looks exactly like a CPU fallback and is not one. Distinguish them
by the transcript header, never by wall-clock: cold-but-GPU still reports
`speaches@…` / `cuda`, whereas a real fallback reports `local-whisperx` /
`cpu-fallback`. Send one throwaway request to warm the model before timing
anything.

**Extrapolated to a real session (GPU stack):** a 60-minute recording finishes in
roughly **6–7 minutes**. This clears `PROJECT_PLAN.md` §2's 10–20-minute / 3–8×
target that the CPU host never reached, and turns "drop it in, pick it up later"
into "wait for it."

The first request after ≥5 minutes idle pays a few seconds of model reload
(Speaches `ttl=300`), so a one-off short clip measures worse than these warm
numbers. Measure warm, and always confirm `asr_backend` / `diarize_device` in the
transcript header — a silent CPU fallback (B-005) is the single most likely reason
a GPU-stack measurement looks like a CPU-stack one.

**Extrapolated to a real session (post-2026-05-01 reconfig, CPU host):**

| model | concurrency | per-job wall for 60-min audio | system throughput notes |
|---|---|---|---|
| medium | 1 | ~2.1 h | best per-file latency at this model size |
| medium | 2 (queued) | ~2.4–3.2 h per job | ~24% more aggregate throughput when files queue up |
| large-v3 | 1 | ~2.6 h | only ~25% latency penalty over medium; transcript quality identical on clean audio, modestly better on hard audio |
| large-v3 | 2 (queued) | per-job ~3.4 h (job 1) / ~2.7 h (job 2); aggregate ~3.4 h for two | Spec config; validated 2026-05-01 (Run D). Memory peak 6.6 / 15.6 GiB. |

Spec's 10–20-minute target (3–8× realtime on Xeon Silver + AVX-512) is still not hit — we have AVX-512 now, but only 16 vCPUs and a VM-bound memory subsystem rather than the spec's 24-thread bare-metal envelope. The "drop file in after session, ready before next session" bar is comfortably met; "real-time-ish" is not.

Remaining tuning knobs, in rough order of bang-for-buck:

- **Tune CTranslate2 thread count.** Run A pegged only ~4 of 16 vCPUs; Run C with concurrency=2 reached ~13. The per-job thread cap is internal (CTranslate2 default heuristics). If the deployment usually sees a single file at a time, raising the per-job thread count (e.g. via `OMP_NUM_THREADS` / CTranslate2 `inter_threads`/`intra_threads` knobs) could close the gap toward Run C-style utilization for a single job. Worth a benchmark run before committing.
- **Validate large-v3 + concurrency=2.** Untested. Memory headroom looks fine. Would lock in the spec's intended config.
- **Smaller whisper model** — `small` is ~2× faster than `medium`, `base` is ~4× faster; quality trade-offs land harder on noisy/accented audio.
- **Diarization is non-trivial fixed cost.** Pipeline timings include pyannote 3.1 — a meaningful chunk of every run is diarization, which doesn't scale linearly with the same knobs. Worth profiling separately before chasing more whisper-side speed.

## Coral M.2 Accelerator

Per PROJECT_PLAN.md §10: present in the operator's home server, **out of scope** for this project. Reserved for future M5 device VAD / Frigate NVR. No Edge TPU userspace drivers will be installed on this VM.

## NVIDIA GPU

**Superseded 2026-07-12.** The 2026-04-30 decision (PROJECT_PLAN.md §8 Q2) to skip
GPU entirely applied to the *old Tesla* in the home server (8 GB, no tensor cores)
— that card is still not pursued. It does **not** apply to the current
deployment: the project moved to the Alienware RTX 5090 and
`docker-compose.gpu.yml` is now the primary, actively deployed configuration.

Both GPU features remain **opt-in with CPU fallback**, so the CPU-only path in
`docker-compose.yml` continues to work unchanged on any GPU-less host:

- `ASR_BACKEND=router` → Speaches on GPU, falling back to in-process WhisperX.
- `DIARIZE_BACKEND=remote` → `diarize-svc` on GPU, falling back to in-process
  pyannote.

Fallback is per-request and verified live (STATUS 2026-07-13), so a GPU or sidecar
outage degrades speed but never fails a job.
