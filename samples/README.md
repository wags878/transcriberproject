# Sample audio (synthetic — safe to commit)

These clips are **100% synthetic** (neural TTS, no real people, no PHI) and exist
so anyone can smoke-test the transcription + diarization pipeline without
hunting for audio. Unlike `tests/fixtures/` (gitignored for real/vetted audio),
these are committed on purpose.

## The clips

| File | Length | Speakers | Purpose |
|---|---|---|---|
| `quick_note.mp3` | ~14s | 1 | fast single-speaker sanity check |
| `quick_qa.mp3` | ~32s | 2 | short two-speaker Q&A |
| `friendly_conversation.mp3` | ~91s | 2 | longer two-speaker catch-up |
| `team_standup.mp3` | ~43s | 3 | three distinct voices (diarization beyond two) |

Verified live through the pipeline: `speakers_detected` = 1 / 2 / 2 / 3
respectively. Voices used: `en-US-AriaNeural` (A, US female),
`en-US-GuyNeural` (B, US male), `en-GB-SoniaNeural` (C, UK female). Each clip's
`ml/`-style ground truth is the alternating turns below.

## `friendly_conversation.mp3`

A ~91-second friendly catch-up between two people, rendered with two distinct
[edge-tts](https://github.com/rany2/edge-tts) neural voices so speaker
diarization has a real signal:

- **Voice A** — `en-US-AriaNeural` (female)
- **Voice B** — `en-US-GuyNeural` (male)

### Ground truth (who says what)

```
[A] Hey! It's so good to finally catch up. How have you been?
[B] Honestly, pretty good. Work's been busy, but the good kind of busy. How about you?
[A] Same here. I actually started taking a pottery class on the weekends.
[B] Pottery? That's awesome. I never would have pictured you at a wheel covered in clay.
[A] I know, right? It's messier than I expected, but it's weirdly relaxing. You should come try a session sometime.
[B] I might take you up on that. I've been looking for something to do that isn't staring at a screen.
[A] That's exactly why I started. My eyes were begging me for a break.
[B] Tell me about it. So, are you still planning that trip out to the coast this summer?
[A] Yeah, we booked it last week. Two weeks near the water, no laptop allowed.
[B] That sounds perfect. Send me pictures, especially of the sunsets.
[A] Absolutely. And you have to promise you'll actually take some time off too.
[B] Deal. Let's grab coffee before you leave and plan it all out.
[A] I'd love that. Same place as always?
[B] Where else? I'll see you Saturday.
```

Because you wrote the ground truth, this doubles as a **diarization accuracy
check** — compare the output's `SPEAKER_00`/`SPEAKER_01` attributions against
the A/B turns above. (Two distinct speakers should be detected; expect the
occasional turn-boundary slip, which is normal for real diarization.)

### Try it

Through the PWA (`http://localhost:8000` or the tailnet URL), pick this file and
hit **Transcribe**. Or via curl:

```sh
TOKEN=$(grep ^API_TOKEN .env | cut -d= -f2-)
curl -H "Authorization: Bearer $TOKEN" \
     -F "audio=@samples/friendly_conversation.mp3" \
     -F "title=friendly-convo" \
     http://localhost:8000/v1/transcribe
```

### Regenerate / make your own

Render turns with alternating voices via `edge-tts` (`pip install edge-tts`),
then concatenate with short silences via `ffmpeg`'s concat demuxer. Swap in more
voices or a longer script to simulate other conversation shapes. Keep it
synthetic — never commit real recordings.
