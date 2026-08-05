"""Phase 3d実機計測器。

認識本文は保存せず、品質統計と時間・process tree peak working setだけをJSONへ保存する。
通常の利用者向けCLIではなく、受入是正の再現可能な測定専用である。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from utteran.asr.whisper_cpp import WhisperCppBackend
from utteran.config import WhisperCppConfig
from utteran.types import ASROptions, Segment


def _statistics(segments: list[Segment]) -> dict[str, int | float]:
    texts = [segment.text.strip() for segment in segments]
    maximum = current = repeated = 0
    previous: str | None = None
    for value in texts:
        current = current + 1 if value and value == previous else 1
        if current > 1:
            repeated += 1
        maximum = max(maximum, current)
        previous = value
    combined = "".join(texts)
    japanese = sum(
        1 for char in combined if "\u3040" <= char <= "\u30ff" or "\u3400" <= char <= "\u9fff"
    )
    nonspace = sum(not char.isspace() for char in combined)
    return {
        "max_consecutive_repetition": maximum,
        "repeated_segment_count": repeated,
        "segment_count": len(segments),
        "character_count": len(combined),
        "japanese_character_ratio": japanese / nonspace if nonspace else 0.0,
        "empty_segment_ratio": sum(not value for value in texts) / len(texts) if texts else 0.0,
    }


def _settings(condition: str, vad_model: Path | None) -> tuple[WhisperCppConfig, bool]:
    neutral: dict[str, Any] = {
        "variant": "vulkan",
        "no_context": False,
        "vad": False,
        "entropy_threshold": 1_000_000.0,
        "logprob_threshold": -1_000_000.0,
        "no_speech_threshold": 1.0,
        "temperature": 0.0,
        "temperature_increment": 0.0,
        "repetition_limit": 0,
    }
    word_timestamps = condition == "B3"
    if condition in {"B1", "B2"}:
        # flash attention/DTWだけを切り分け、現在の根本対策既定は揃える。
        neutral.update(
            no_context=True,
            entropy_threshold=2.4,
            logprob_threshold=-1.0,
            no_speech_threshold=0.6,
            temperature_increment=0.2,
        )
    elif condition == "B3":
        neutral.update(
            no_context=True,
            entropy_threshold=2.4,
            logprob_threshold=-1.0,
            no_speech_threshold=0.6,
            temperature_increment=0.2,
        )
    elif condition == "C1":
        neutral["no_context"] = True
    elif condition == "C2":
        neutral.update(vad=True, vad_model=vad_model)
    elif condition == "C3":
        neutral["entropy_threshold"] = 2.4
    elif condition == "C4":
        neutral["logprob_threshold"] = -1.0
    elif condition == "C5":
        neutral["no_speech_threshold"] = 0.6
    elif condition == "C6":
        neutral.update(
            no_context=True,
            vad=True,
            vad_model=vad_model,
            entropy_threshold=2.4,
            logprob_threshold=-1.0,
            no_speech_threshold=0.6,
            temperature_increment=0.2,
        )
    elif condition != "C0":
        raise ValueError(f"unknown condition: {condition}")
    return WhisperCppConfig.model_validate(neutral), word_timestamps


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("conditions", nargs="+")
    parser.add_argument("--vad-model", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    completed: list[dict[str, Any]] = []
    if args.output.is_file():
        completed = json.loads(args.output.read_text(encoding="utf-8"))
    done = {item["condition"] for item in completed}
    for condition in args.conditions:
        if condition in done:
            continue
        settings, words = _settings(condition, args.vad_model)
        if condition == "B2":
            os.environ["UTTERAN_DEBUG_NO_FLASH_ATTN"] = "1"
        else:
            os.environ.pop("UTTERAN_DEBUG_NO_FLASH_ATTN", None)
        backend = WhisperCppBackend(settings, allow_fallback=False)
        backend.load("large-v3-turbo-q5_0", "vulkan", "ggml")
        started_at = datetime.now().astimezone().isoformat()
        started = time.perf_counter()
        try:
            result = backend.transcribe(
                args.audio,
                ASROptions(language=None, word_timestamps=words, beam_size=5),
            )
        finally:
            backend.unload()
        elapsed = time.perf_counter() - started
        completed.append(
            {
                "condition": condition,
                "started_at": started_at,
                "finished_at": datetime.now().astimezone().isoformat(),
                "asr_seconds": elapsed,
                **_statistics(result.segments),
            }
        )
        args.output.write_text(
            json.dumps(completed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
