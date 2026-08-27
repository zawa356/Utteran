"""Generate the repository's deterministic diarization quality WAV fixture."""

from __future__ import annotations

import json
import math
import struct
import wave
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "diarization_quality"
GROUND_TRUTH = FIXTURE_DIR / "meeting_ground_truth.json"
OUTPUT = FIXTURE_DIR / "meeting.wav"
SAMPLE_RATE = 16_000
AMPLITUDE = 0.24
FUNDAMENTALS = {"REF_A": 118.0, "REF_B": 176.0, "REF_C": 232.0}


def _envelope(position: float, length: float) -> float:
    attack = min(1.0, position / 0.025)
    release = min(1.0, (length - position) / 0.04)
    syllable = 0.72 + 0.28 * math.sin(2.0 * math.pi * 4.2 * position) ** 2
    return max(0.0, attack * release * syllable)


def generate() -> Path:
    """Write a mono PCM signal with a distinct speech-like harmonic voice per speaker."""
    payload: dict[str, Any] = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    duration = float(payload["duration"])
    turns = list(payload["turns"])
    samples = [0.0] * round(duration * SAMPLE_RATE)
    for turn in turns:
        start = float(turn["start"])
        end = float(turn["end"])
        fundamental = FUNDAMENTALS[str(turn["speaker"])]
        first = round(start * SAMPLE_RATE)
        last = round(end * SAMPLE_RATE)
        for index in range(first, last):
            position = index / SAMPLE_RATE - start
            phase = 2.0 * math.pi * fundamental * position
            voiced = (
                math.sin(phase)
                + 0.42 * math.sin(2.0 * phase)
                + 0.22 * math.sin(3.0 * phase)
                + 0.11 * math.sin(5.0 * phase)
            )
            samples[index] += AMPLITUDE * _envelope(position, end - start) * voiced
    pcm = b"".join(
        struct.pack("<h", round(max(-1.0, min(1.0, sample)) * 32767.0)) for sample in samples
    )
    with wave.open(str(OUTPUT), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm)
    return OUTPUT


if __name__ == "__main__":
    print(generate())
