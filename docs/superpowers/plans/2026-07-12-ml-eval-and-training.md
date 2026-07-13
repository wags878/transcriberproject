# ML track: eval harness, voice enrollment, fine-tuning scaffold — Plan

> **Tracking scheme (for whoever picks this up):** plans live in
> `docs/superpowers/plans/`, designs in `docs/superpowers/specs/`, the running
> log is `docs/STATUS.md` (append-only, dated close-out per phase). One git
> commit per task with rationale in the body; work on a feature branch → PR.
> Steps use `- [ ]` checkboxes for tracking.

**Goal:** Stand up the ML train→eval→serve loop on **synthetic data** so it is
warm, measured, and GPU-exercised before any real (PHI) session data exists —
"don't cold boot." Deliver measurable baselines, then a first real model feature
(voice enrollment), then a fine-tuning scaffold that becomes a button-press once
consented data arrives.

**Guiding honesty (do not skip):**
- Synthetic TTS audio is *clean and uniform*. It is excellent for **building and
  validating pipelines** and for tasks with exact labels (speaker-ID,
  diarization), and for **domain-vocabulary augmentation**. It is **not** a
  substitute for real audio when training the core ASR — fine-tuning Whisper on
  pure TTS risks improving TTS performance while degrading real-speech accuracy
  (distribution shift). Any "great WER" from pure-synthetic ASR training is
  misleading; always report the eval set's nature alongside the number.
- The service already emits `(audio, transcript)` pairs as normal output — a
  natural data flywheel for real fine-tuning later. Keep that pipeline in mind.

**Non-goals / explicitly out of scope:** clinical inference (emotion scoring,
technique detection, diagnosis-adjacent models). Validity/liability bar is too
high; revisit only with domain expertise and consented, labeled data.

**Placement:** everything lands under a new top-level `ml/` directory and must
**not** touch the running service's request path. Training/eval are offline
jobs; only the *artifacts* (an enrolled-voice index, a fine-tuned checkpoint)
ever feed the service, behind explicit config flags.

---

## Track A — Synthetic data generator + eval harness (do first)

The foundation: you can't improve what you can't measure. Generates labeled
synthetic conversations, runs them through the live pipeline, scores WER +
speaker attribution, emits a baseline scorecard.

**Files (created):**
- `ml/synth/generate.py` — parametric conversation generator (edge-tts voices,
  turn scripts, silence padding via ffmpeg). Emits `audio.mp3` + `truth.json`
  ({turns: [{speaker, start?, end?, text}], voices}).
- `ml/synth/scripts/` — a handful of conversation scripts (2-spk, 3-spk, short,
  long, domain-vocab-heavy).
- `ml/eval/score.py` — WER (jiwer) vs. concatenated truth text; speaker
  attribution accuracy + DER-style turn scoring vs. truth turns.
- `ml/eval/run_baseline.py` — generate → POST to the service → score → write
  `ml/eval/reports/<date>-baseline.md` scorecard.
- `ml/README.md` — how to run, what the metrics mean, honesty caveats.
- `ml/requirements-ml.txt` — jiwer, edge-tts, (later) torch/peft/transformers,
  speechbrain/pyannote — kept separate from the service's `requirements.txt`.

- [x] **A1** Write `ml/synth/generate.py` (extract + generalize the recipe
      already proven in `samples/`: render turns with N voices, concat with
      silences, emit `truth.json`). Verify it produces a playable clip + truth.
- [x] **A2** Write `ml/eval/score.py`: WER via jiwer (normalize case/punct);
      speaker-attribution accuracy (align predicted segments to truth turns by
      time overlap, fraction of speech-time correctly labeled); report both.
- [x] **A3** Write `ml/eval/run_baseline.py`: drive the live API with a set of
      generated clips, collect transcripts, score, write a scorecard table.
- [x] **A4** Run it against the current pipeline; commit the first baseline
      report. This is the number every later change is measured against.
- [x] **A5** STATUS.md close-out entry with the baseline numbers.

**Acceptance:** `python ml/eval/run_baseline.py` produces a scorecard with WER +
speaker-accuracy across ≥4 synthetic clips, no PHI, runs offline against the
running stack.

**✅ Track A complete (2026-07-12, branch `ml-eval-harness`).** Delivered under
`ml/` (containerized: `ml/docker-compose.ml.yml`). First baseline —
`ml/eval/reports/2026-07-12-baseline.md` — over 4 clips: mean WER **0.0%**
(genuine but synthetic-clean, so optimistic), mean speaker-attribution **81.1%**,
speaker-count 4/4. See the STATUS 2026-07-12 Track A close-out. Note: edge-tts
pinned to 7.2.8 (6.x now 403s). Attribution is the meaningful metric until real
audio exists; it should rise once diarization moves to GPU (Infra I).

---

## Track B — Therapist voice enrollment (first real model feature)

Enroll the therapist's voice once → auto-label `Therapist` vs `Client` instead
of anonymous `SPEAKER_00/01`. Uses pretrained speaker embeddings (ECAPA-TDNN via
speechbrain, or pyannote's embedding) + cosine similarity. Synthetic-validatable.

**Files (created):**
- `ml/enroll/embed.py` — wrap a pretrained speaker-embedding model; `embed(wav)`.
- `ml/enroll/enroll.py` — build/store an enrollment vector from one or more
  reference clips → `enrollments/<name>.npy` (+ metadata).
- `app/roles.py` — optional post-diarization pass: for each speaker cluster,
  compare its embedding to enrolled vectors; relabel above a threshold.
- `tests/test_roles.py` — synthetic voices as stand-ins; assert the enrolled
  voice's turns get the enrolled label and others stay generic.

- [ ] **B1** `ml/enroll/embed.py` + pick the embedding model (document choice).
- [ ] **B2** `ml/enroll/enroll.py` — enrollment vector from reference clip(s).
- [ ] **B3** Offline eval on synthetic voices: enroll voice A, verify A's turns
      match and B/C don't (threshold sweep → pick operating point).
- [ ] **B4** `app/roles.py` behind a config flag (`ENABLE_ROLE_LABELS`,
      default off) — relabels stitched segments; service path untouched when off.
- [ ] **B5** Tests + STATUS entry. Wire into `render_txt`/`.json` only when the
      flag is on (contract stability otherwise).

**Acceptance:** with an enrolled synthetic "therapist" voice, its turns render as
the enrolled label; feature is fully off by default.

---

## Track C — Whisper LoRA fine-tuning scaffold (button-press for later)

Build and smoke-test the fine-tuning loop on synthetic data so it's proven on the
5090; real accuracy gains wait for consented data. Also supports the one
legitimate synthetic use: **domain-vocabulary augmentation**.

**Files (created):**
- `ml/finetune/dataset.py` — build a HF `Dataset` of `(audio, text)` from
  generated clips and/or the service's own `(upload, transcript)` outputs.
- `ml/finetune/train_lora.py` — PEFT/LoRA fine-tune of faster-whisper/whisper on
  the 5090; checkpoints to `ml/finetune/checkpoints/`.
- `ml/finetune/README.md` — how to point it at real data later; the flywheel.

- [ ] **C1** `dataset.py` — load generated clips + `truth.json` into a Dataset;
      optional loader for the service's on-disk outputs.
- [ ] **C2** `train_lora.py` — minimal LoRA loop; **smoke-run** a few steps on
      synthetic to prove it trains + checkpoints on the GPU (not for real gains).
- [ ] **C3** Eval integration: score a checkpoint with Track A's harness; report
      side-by-side vs. baseline, clearly labeled "synthetic eval — not
      representative of real speech."
- [ ] **C4** Domain-vocab augmentation demo: synthesize clips of target terms
      (with noise/reverb), show the harness can measure term-level accuracy.
- [ ] **C5** Document the real-data path (Speaches/ct2 conversion of a tuned
      checkpoint) and STATUS entry. Do NOT ship a synthetic-trained model into
      the serving path.

**Acceptance:** `train_lora.py` completes a smoke run + checkpoint on the 5090;
the harness scores it; docs state plainly it is scaffolding, not a real model.

---

## Infra — GPU diarization (highest-ROI systems win; can run in parallel)

Diarization (pyannote) is CPU-bound and the current pipeline bottleneck
(~2.85× realtime full vs. ~14× GPU ASR — see STATUS 2026-07-12 throughput
baseline). Move it to CUDA.

- [ ] **I1** Run pyannote on `cuda` in `app/diarize.py` when a GPU is available
      to transcribe-svc (needs a CUDA-capable image/torch for the diarizer, or a
      separate diarization sidecar mirroring the Speaches split). Evaluate both:
      (a) give transcribe-svc GPU + CUDA torch, (b) diarization sidecar.
- [ ] **I2** Re-measure with Track A's harness; target full-pipeline realtime
      factor ≥ 8× (from ~2.85×). STATUS entry with before/after.

---

## Suggested order

A (measure) → B (first real feature) → I (infra win) → C (scaffold for real
data). A is prerequisite for honestly evaluating B, C, and I.
