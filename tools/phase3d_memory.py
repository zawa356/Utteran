"""Measure one Phase 3d ASR/diarization run without persisting recognized content."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from acceptance.harness import _read_peak_memory

from utteran.asr.whisper_cpp import WhisperCppBackend
from utteran.config import WhisperCppConfig
from utteran.diarization.pyannote import PyannoteBackend
from utteran.types import ASROptions, DiarizationOptions


def _monitor(stop: threading.Event, peak: list[int]) -> None:
    maximum = 0
    while not stop.wait(0.1):
        current = _read_peak_memory(os.getpid())
        if current is not None:
            maximum = max(maximum, current)
    peak.append(maximum)


def _run_asr(audio: Path) -> dict[str, Any]:
    backend = WhisperCppBackend(
        WhisperCppConfig(variant="vulkan", repetition_limit=10), allow_fallback=False
    )
    backend.load("large-v3-turbo-q5_0", "vulkan", "ggml")
    result = backend.transcribe(audio, ASROptions(language=None, word_timestamps=False))
    backend.unload()
    return {"segment_count": len(result.segments)}


def _run_diarization(audio: Path, device: str) -> dict[str, Any]:
    backend = PyannoteBackend()
    backend.load("pyannote/speaker-diarization-community-1", device)
    result = backend.diarize(audio, DiarizationOptions())
    backend.unload()
    return {
        "turn_count": len(result.turns),
        "exclusive_turn_count": len(result.exclusive_turns or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("asr", "diarization"))
    parser.add_argument("device")
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--minutes", type=int, required=True)
    args = parser.parse_args()
    stop = threading.Event()
    peak: list[int] = []
    monitor = threading.Thread(target=_monitor, args=(stop, peak), daemon=True)
    started_at = datetime.now().astimezone().isoformat()
    started = time.perf_counter()
    monitor.start()
    try:
        detail = (
            _run_asr(args.audio)
            if args.kind == "asr"
            else _run_diarization(args.audio, args.device)
        )
        status = "passed"
        error = None
    except Exception as exc:
        detail = {}
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        stop.set()
        monitor.join()
    record = {
        "kind": args.kind,
        "device": args.device,
        "minutes": args.minutes,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(),
        "elapsed_seconds": time.perf_counter() - started,
        "peak_working_set_bytes": max(peak, default=0),
        "status": status,
        "error": error,
        **detail,
    }
    records: list[dict[str, Any]] = []
    if args.output.is_file():
        records = json.loads(args.output.read_text(encoding="utf-8"))
    records.append(record)
    args.output.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if status != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
