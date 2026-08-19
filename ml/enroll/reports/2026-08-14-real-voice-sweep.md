# Voice-enrollment sweep — REAL voices — 2026-08-14

**First measurement on real, non-synthetic voices.** The 2026-07-12 sweep used
TTS voices and its own honesty note said to re-sweep before trusting a threshold
in production. This is that re-sweep.

- Embedding model: `pyannote/wespeaker-voxceleb-resnet34-LM` (unchanged)
- Enrollment: **Speaker A**, from one 12.1 s solo clip recorded through the PWA
  (`abe08dae…webm` → 16 kHz mono WAV). Consented, operator's own voice.
- Probe clip: a 22.9 s two-person recording (Speaker A + Speaker B, Speaker B speaking Spanish),
  clusters taken from the service's own diarization output.
- Method: `app.roles.compute_cluster_embeddings` + `app.embed.cosine` — the same
  code path the service runs at request time, not a parallel reimplementation.

## Similarity to the enrolled voice

| Cluster | Actually who | Cosine | Genuine? |
|---|---|---:|:---:|
| `SPEAKER_00` | Speaker A | **0.7176** | ✅ yes |
| `SPEAKER_01` | Speaker A (diarization split them in two) | **0.5899** | ✅ yes |
| `SPEAKER_02` | Speaker B — *"¿Dónde está el baño?"* | **0.1282** | no (true impostor) |

## Separation

- Lowest genuine: **0.5899**
- Highest impostor: **0.1282**
- Separation: **0.4617** — cleanly separated
- **`ROLE_MATCH_THRESHOLD` = 0.5** (0.37 above the impostor, 0.09 below the
  lowest genuine). Midpoint convention would give 0.359; 0.5 is preferred as the
  more conservative end of the same window, and it is the existing default.

| Threshold | Genuine accepted | Impostors accepted |
|---:|:---:|:---:|
| 0.30 | 2/2 | 0/1 ✅ |
| 0.40 | 2/2 | 0/1 ✅ |
| 0.50 | 2/2 | 0/1 ✅ |
| 0.60 | 1/2 | 0/1 |
| 0.65 | 1/2 | 0/1 |
| 0.70 | 1/2 | 0/1 |

## How real compares to synthetic

| | Genuine | Impostor | Gap |
|---|---|---|---|
| Synthetic (2026-07-12) | 0.850–0.920 | 0.158–0.226 | 0.62 |
| **Real (this sweep)** | 0.590–0.718 | 0.128 | **0.46** |

Real voices do separate less — genuine scores land ~0.15 lower — but the margin
is far healthier than a first, botched pass suggested. Impostor similarity is
essentially the same as synthetic; it is the *genuine* side that degrades. So the
risk on real audio is **false negatives** (no label), not false positives
(wrong label). That is the safe direction to fail for a medical transcript.

## Correction — how the first pass got it wrong

An initial run scored only two clusters and read `0.6113` as the impostor,
implying a 0.126 gap and prompting a threshold of 0.65. That cluster was **Speaker A**,
not Speaker B. The mistake was possible because the probe clip's first transcription
never surfaced Speaker B at all — their Spanish went untranscribed, so the only two
clusters available were both Speaker A, and the "impostor" was a genuine sample.

Speaker B appeared only after re-running the clip as WAV, which produced three speakers
and the Spanish text. Lesson: **confirm a probe clip actually contains the
impostor before treating a low genuine score as an impostor score.** A "gap"
between two clusters means nothing until each cluster's identity is established.

## Caveats

- **One enrollment clip, 12.1 s.** Thin. `build_enrollment` accepts repeated
  `--clip`; 2–3 clips from different sessions/devices/rooms would make the
  centroid far more robust. Expect genuine scores to vary with mic and room.
- **One impostor, one clip.** Speaker B at 0.1282 is a single sample from a single
  recording, and he is speaking a different language, which likely flatters the
  separation. A same-language impostor would score higher.
- **Diarization split the enrolled speaker across two clusters.** Because
  `match_clusters` is greedy one-to-one, only the highest-scoring cluster can
  receive the name — the other stays anonymous even though it clears the
  threshold. Not a correctness bug (nobody is mislabeled), but it means a split
  speaker will be partially labeled.
- **Out-of-bounds segments silently drop a cluster.** Whisper emitted a segment
  ending at 29.84 s on a 22.87 s file; `compute_cluster_embeddings` cropped past
  the end, threw per-segment, and that cluster produced no vector at all. Here it
  degraded gracefully, but if it hits the *enrolled* speaker's cluster, role
  labeling silently does nothing. Clamping segment ends to the audio duration
  before embedding would fix it.
