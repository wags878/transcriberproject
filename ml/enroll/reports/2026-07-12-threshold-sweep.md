# Voice-enrollment threshold sweep — 2026-07-12

**Synthetic voices — clean and separable; real voices separate less. Re-sweep on consented real enrollments before trusting a threshold in production.**

- Embedding model: `pyannote/wespeaker-voxceleb-resnet34-LM`
- Enrollment: voice A (`en-US-AriaNeural`) → `Therapist`, from `ml/enroll/scripts/enroll_therapist.json` (distinct sentences).
- Probes: each truth speaker's regions in the eval clips; A is the genuine match, B/C are impostors.

## Similarity to the enrolled voice

| Clip | Speaker | Cosine | Genuine? |
|---|:---:|---:|:---:|
| 2spk_long | A | 0.920 | ✅ yes |
| 2spk_long | B | 0.158 | no |
| 3spk_standup | A | 0.850 | ✅ yes |
| 3spk_standup | B | 0.160 | no |
| 3spk_standup | C | 0.226 | no |

## Separation

- Lowest genuine (A) similarity: **0.850**
- Highest impostor (B/C) similarity: **0.226**
- Cleanly separated: **yes**
- **Recommended `ROLE_MATCH_THRESHOLD`: 0.538** (midpoint of the gap, clamped to [0.2, 0.8]).

## Threshold sweep

| Threshold | Genuine accepted | Impostors accepted (false +) |
|---:|:---:|:---:|
| 0.20 | 2/2 | 1/3 |
| 0.30 | 2/2 | 0/3 | ✅
| 0.40 | 2/2 | 0/3 | ✅
| 0.50 | 2/2 | 0/3 | ✅
| 0.60 | 2/2 | 0/3 | ✅
| 0.70 | 2/2 | 0/3 | ✅
| 0.80 | 2/2 | 0/3 | ✅

> A clean operating point accepts every genuine A and zero impostors. Set `ROLE_MATCH_THRESHOLD` in `.env` (or accept the config default) and enable with `ENABLE_ROLE_LABELS=1`.
