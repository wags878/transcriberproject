"""Parametric synthetic conversation generator.

Renders a scripted multi-speaker conversation to a single audio file plus a
ground-truth JSON, reusing the recipe proven in ``samples/`` (edge-tts neural
voices → per-turn clips → ffmpeg concat with silence padding).

    python -m ml.synth.generate --script ml/synth/scripts/2spk_short.json \
        --out-dir ml/synth/out

produces ``<out-dir>/<script-name>/audio.mp3`` + ``truth.json``.

Ground truth is exact by construction: we know the speaker of every turn, and
the turn boundaries are computed from each rendered clip's measured duration
plus the fixed inter-turn silence. That makes ``truth.json`` usable both for
WER (concatenated text) and for time-overlap speaker-attribution scoring.

Honesty: this audio is clean, uniform TTS. It is excellent for building and
validating the pipeline and for exact-label tasks (speaker attribution), but it
is NOT representative of real speech acoustics — see ml/README.md.

Self-contained offline job: talks to Microsoft's edge-tts endpoint to synthesize
voices, and shells out to ffmpeg/ffprobe. It never touches the service code.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Turn:
    speaker: str
    text: str
    start: float
    end: float


def _run(cmd: list[str]) -> str:
    """Run a subprocess, raising with captured stderr on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def _ffprobe_duration(path: Path) -> float:
    """Duration of an audio file in seconds, via ffprobe."""
    out = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nokey=1:noprint_wrappers=1",
        str(path),
    ])
    return float(out.strip())


async def _render_turn(text: str, voice: str, out_path: Path) -> None:
    """Synthesize one turn to mp3 with edge-tts."""
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


def _make_silence(path: Path, seconds: float, *, sample_rate: int = 24000) -> None:
    """Render a mono silence clip matching edge-tts output params."""
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", f"{seconds:.3f}",
        "-q:a", "9",
        str(path),
    ])


def _concat(list_file: Path, out_path: Path) -> None:
    """Concatenate the clips in a concat-demuxer list, re-encoding to a clean
    single mp3 so downstream tooling sees uniform stream params."""
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:a", "libmp3lame", "-q:a", "2",
        str(out_path),
    ])


def load_script(script_path: Path) -> dict[str, Any]:
    data = json.loads(script_path.read_text(encoding="utf-8"))
    if "voices" not in data or "turns" not in data:
        raise ValueError(f"{script_path} must define 'voices' and 'turns'")
    for i, turn in enumerate(data["turns"]):
        spk = turn.get("speaker")
        if spk not in data["voices"]:
            raise ValueError(
                f"{script_path} turn {i}: speaker {spk!r} has no voice in 'voices'"
            )
        if not (turn.get("text") or "").strip():
            raise ValueError(f"{script_path} turn {i}: empty text")
    return data


def generate_from_script(script_path: Path, out_dir: Path) -> Path:
    """Render a script to ``<out_dir>/audio.mp3`` + ``truth.json``.

    Returns the output directory. ``out_dir`` is created if missing.
    """
    script = load_script(script_path)
    voices: dict[str, str] = script["voices"]
    silence_ms: int = int(script.get("silence_ms", 400))
    silence_s = silence_ms / 1000.0

    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "audio.mp3"
    truth_path = out_dir / "truth.json"

    turns: list[Turn] = []
    with tempfile.TemporaryDirectory(prefix="synthgen-") as tmp:
        tmp_dir = Path(tmp)

        # One fixed silence clip reused between every turn.
        silence_path = tmp_dir / "silence.mp3"
        _make_silence(silence_path, silence_s)

        concat_entries: list[Path] = []
        cursor = 0.0
        for i, spec in enumerate(script["turns"]):
            speaker = spec["speaker"]
            text = spec["text"].strip()
            clip = tmp_dir / f"turn_{i:03d}.mp3"
            asyncio.run(_render_turn(text, voices[speaker], clip))
            dur = _ffprobe_duration(clip)

            start = cursor
            end = cursor + dur
            turns.append(Turn(speaker=speaker, text=text, start=round(start, 3),
                              end=round(end, 3)))
            concat_entries.append(clip)
            cursor = end

            # Trailing silence after every turn except the last.
            if i < len(script["turns"]) - 1:
                concat_entries.append(silence_path)
                cursor += silence_s

        # ffmpeg concat demuxer list; paths must be quoted & escaped.
        list_file = tmp_dir / "concat.txt"
        list_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in concat_entries),
            encoding="utf-8",
        )
        _concat(list_file, audio_path)

    total_duration = round(_ffprobe_duration(audio_path), 3)
    truth = {
        "name": script.get("name", script_path.stem),
        "description": script.get("description", ""),
        "voices": voices,
        "silence_ms": silence_ms,
        "duration_seconds": total_duration,
        "num_speakers": len(voices),
        "synthetic": True,  # never a real recording; safe to commit, no PHI
        "turns": [
            {"speaker": t.speaker, "text": t.text, "start": t.start, "end": t.end}
            for t in turns
        ],
    }
    truth_path.write_text(
        json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--script", required=True, type=Path,
                    help="path to a conversation script JSON")
    ap.add_argument("--out-dir", required=True, type=Path,
                    help="base output dir; a subdir named after the script is created")
    ap.add_argument("--name", default=None,
                    help="override the output subdir name (default: script stem)")
    args = ap.parse_args(argv)

    name = args.name or args.script.stem
    out_dir = args.out_dir / name
    generate_from_script(args.script, out_dir)
    print(f"wrote {out_dir / 'audio.mp3'} + {out_dir / 'truth.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
