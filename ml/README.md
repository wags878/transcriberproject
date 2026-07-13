# ml/ — offline ML train→eval→serve tooling

Everything here is an **offline job**. Nothing in this directory is imported by
the running service (`app/`); the only coupling is over the HTTP API. Artifacts
(a scorecard today; an enrolled-voice index or a fine-tuned checkpoint later)
are the only things that ever flow back toward the service, behind explicit
config flags.

Plan of record: [`docs/superpowers/plans/2026-07-12-ml-eval-and-training.md`](../docs/superpowers/plans/2026-07-12-ml-eval-and-training.md).
This directory currently implements **Track A — the synthetic eval harness**.

## ⚠ Honesty caveat (read this before trusting any number)

The audio here is **100% synthetic neural TTS** — clean, uniform, no PHI, safe
to commit. That makes it:

- **Great** for building and validating the pipeline, and for **exact-label**
  tasks (speaker attribution / diarization), where the ground truth is known by
  construction.
- **Great** for domain-vocabulary augmentation experiments (Track C).
- **Not** a substitute for real audio when judging ASR accuracy. Real
  recordings have noise, overlap, disfluency, and accent variation that TTS
  lacks, so **WER here is optimistic**. Fine-tuning ASR on pure TTS can even
  *improve* TTS scores while *degrading* real-speech accuracy (distribution
  shift). **Always report the eval set's nature next to the number** — the
  scorecard does this automatically.

## Layout

```
ml/
├── synth/
│   ├── generate.py        # parametric edge-tts + ffmpeg conversation generator
│   └── scripts/*.json     # conversation scripts (2-spk, 3-spk, domain-vocab)
├── eval/
│   ├── score.py           # WER (jiwer) + speaker-attribution accuracy
│   ├── run_baseline.py    # generate → drive API → score → write scorecard
│   └── reports/           # committed baseline scorecards
├── requirements-ml.txt    # jiwer, edge-tts, httpx (separate from the service)
├── Dockerfile             # reproducible harness image
└── docker-compose.ml.yml  # runs the harness against the live stack
```

## Running it (containerized — no host Python/ffmpeg needed)

The harness talks to the **running service** over HTTP, so bring the stack up
first, then run the harness as a separate compose project:

```sh
# 1. service must be up (publishes 127.0.0.1:8000)
docker compose -f docker-compose.gpu.yml up -d

# 2. full baseline: generate clips → transcribe → score → scorecard
docker compose -f ml/docker-compose.ml.yml run --rm ml-eval
```

The scorecard lands in `ml/eval/reports/<date>-baseline.md` (bind-mounted back
to the host). Generated clips cache under `ml/synth/out/` (gitignored;
regenerable). The container reads `API_TOKEN` from the repo-root `.env` and
reaches the host service at `host.docker.internal:8000` by default (override
with `BASE_URL`, e.g. the tailnet URL).

Generate a single clip without scoring:

```sh
docker compose -f ml/docker-compose.ml.yml run --rm ml-eval \
    python -m ml.synth.generate --script ml/synth/scripts/2spk_short.json --out-dir ml/synth/out
```

### Host venv alternative

If you have Python 3.11 + ffmpeg on PATH:

```sh
pip install -r ml/requirements-ml.txt
python -m ml.eval.run_baseline            # from the repo root
```

## What the metrics mean

- **WER** (word error rate) — edit distance between the reference (truth turns
  concatenated in order) and the hypothesis (predicted segments concatenated in
  time order), over case/punctuation-normalized text. Lower is better; `0%` is
  perfect. Reported per-clip, plus a mean and a word-weighted mean.
- **Speaker-attribution accuracy** — the fraction of ground-truth speech time
  the pipeline labels with the *correct* speaker. The service emits anonymous
  `SPEAKER_00/01/...`; we first solve for the predicted→truth label mapping that
  maximizes overlapping speech time (optimal assignment), then report accuracy
  under that mapping. `diarization_error = 1 − accuracy`. We also report whether
  the detected speaker **count** matched.

## Adding a conversation

Drop a JSON script in `ml/synth/scripts/` (see the existing ones):

```json
{
  "name": "my_convo",
  "silence_ms": 400,
  "voices": {"A": "en-US-AriaNeural", "B": "en-US-GuyNeural"},
  "turns": [
    {"speaker": "A", "text": "..."},
    {"speaker": "B", "text": "..."}
  ]
}
```

`run_baseline.py` picks up every `*.json` in that directory automatically. Keep
it synthetic — **never** commit or point this at real recordings.
