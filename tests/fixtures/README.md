# Test fixtures

Place audio fixtures here for the end-to-end smoke test against the running container.

**Do not commit copyrighted audio.** Either:
- Self-record a short two-speaker clip (you reading both sides is fine), or
- Use a public-domain / CC-BY clip and document its source here.

Recommended fixtures:

| Filename | Length | Speakers | Source |
|---|---|---|---|
| `short_two_speaker.wav` | ~30 s | 2 | _supply_ |
| `five_minute.wav`        | ~5 min | 2 | _supply, used for perf baseline_ |

The `pytest` suite under `tests/` does **not** require these fixtures — it
uses a stubbed pipeline. The fixtures are only used by the curl-based
end-to-end smoke test described in `docs/DEPLOY.md`.

`.gitignore` excludes `*.wav`, `*.mp3`, `*.m4a`, `*.flac` from this
directory. To intentionally commit one (e.g. a public-domain CC-BY fixture),
force-add it: `git add -f tests/fixtures/<file>`.
