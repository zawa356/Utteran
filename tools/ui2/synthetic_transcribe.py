"""Synthetic stand-in for ``utteran transcribe --progress-json`` (UI-3 S3-A E2E only).

Runs no model, reads no audio and writes no file. It prints the public progress JSONL contract
(``utteran.progress``) on stderr and exits like the real CLI, so the *real* ``JobManager`` parses,
finishes and cancels it. The scenario comes from the input file *name* (synthetic paths only):

- ``*slow*``: keeps reporting ASR progress for up to 120 s (used to test cancellation).
- ``*fail*``: an input error (exit 4).
- ``*lying-done*``: reports ``done`` with exit code 0, then exits 3 (``done`` is not success).
- otherwise: audio → asr → export, ``output_written`` per format, exit 0.

    python synthetic_transcribe.py <input_path> <output_dir> <formats>

Nothing here is imported by the product.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import PurePath


def emit(event: str, **payload: object) -> None:
    record: dict[str, object] = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "event": event,
    }
    record.update(payload)
    sys.stderr.write(json.dumps(record, separators=(",", ":")) + "\n")
    sys.stderr.flush()


def stage(name: str, steps: int = 3, delay: float = 0.15) -> None:
    emit("stage_start", stage=name)
    for index in range(1, steps + 1):
        time.sleep(delay)
        emit(
            "progress", stage=name, completed=index, total=steps, ratio=index / steps, message=None
        )
    emit("stage_done", stage=name, duration_seconds=steps * delay, skipped=False)


def main(argv: list[str]) -> int:
    input_path, output_dir, formats = argv[1], argv[2], argv[3].split(",")
    name = PurePath(input_path.replace("\\", "/")).name
    stem = PurePath(name).stem
    started = time.monotonic()
    # Plain stdout/stderr lines become raw logs; the UI must never show or keep them.
    print(f"synthetic raw log for {input_path}", flush=True)
    sys.stderr.write("not json: synthetic stderr diagnostic\n")
    stage("audio", steps=2)
    if "fail" in name:
        emit("error", error_type="InputError", message=f"cannot read {input_path}", exit_code=4)
        emit("done", duration_seconds=time.monotonic() - started, exit_code=4)
        return 4
    emit("stage_start", stage="asr")
    if "slow" in name:
        for index in range(1, 1201):
            time.sleep(0.1)
            if index % 5 == 0:
                emit("progress", stage="asr", completed=index, total=1200, ratio=index / 1200)
        return 0
    stage("asr", steps=4, delay=0.4)
    stage("export", steps=1)
    for fmt in formats:
        emit("output_written", path=str(PurePath(output_dir) / f"{stem}.{fmt}"), format=fmt)
    emit("done", duration_seconds=time.monotonic() - started, exit_code=0)
    if "lying-done" in name:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
