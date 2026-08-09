"""Machine-readable progress events for subprocess consumers."""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from utteran.logging import mask_secrets
from utteran.types import ProgressCallback, ProgressEvent

PROGRESS_SCHEMA_VERSION = 1


class JsonProgressReporter:
    """Write the public progress contract as one compact JSON object per line."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stderr
        self._lock = threading.Lock()
        self._started = time.perf_counter()
        self._done = False

    @property
    def elapsed_seconds(self) -> float:
        """Return elapsed wall time since this reporter was created."""
        return time.perf_counter() - self._started

    def __call__(self, progress: ProgressEvent) -> None:
        """Translate a backend-neutral callback into the public JSON contract."""
        event_type = progress.event_type
        if event_type == "job_resolved":
            self.emit("job_resolved", **progress.details)
        elif event_type in {"file_start", "file_done"}:
            self.emit(event_type, **progress.details)
        elif event_type == "stage_start":
            self.emit("stage_start", stage=progress.stage)
        elif event_type == "stage_done":
            self.emit(
                "stage_done",
                stage=progress.stage,
                duration_seconds=progress.duration_seconds or 0.0,
                skipped=progress.skipped,
            )
        elif event_type == "output_written":
            self.emit("output_written", **progress.details)
        elif event_type == "warning":
            self.emit("warning", stage=progress.stage, message=progress.message or "")
        else:
            ratio = None
            total = progress.total
            if total is not None and total != 0:
                ratio = max(0.0, min(1.0, progress.completed / total))
            self.emit(
                "progress",
                stage=progress.stage,
                completed=progress.completed,
                total=progress.total,
                ratio=ratio,
                message=progress.message,
            )

    def error(self, error: BaseException, exit_code: int) -> None:
        """Emit a redacted expected or unexpected error event."""
        self.emit(
            "error",
            error_type=type(error).__name__,
            message=str(error),
            exit_code=exit_code,
        )

    def done(self, exit_code: int) -> None:
        """Emit exactly one terminal event for this invocation."""
        if self._done:
            return
        self._done = True
        self.emit("done", duration_seconds=self.elapsed_seconds, exit_code=exit_code)

    def emit(self, event: str, **payload: object) -> None:
        """Serialize a redacted event atomically, preserving the one-line invariant."""
        record: dict[str, object] = {
            "schema_version": PROGRESS_SCHEMA_VERSION,
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
        }
        record.update(payload)
        sanitized = _sanitize(record)
        line = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._stream.write(line + "\n")
            self._stream.flush()


class CombinedProgressReporter:
    """Fan one progress notification out to multiple presentation adapters."""

    def __init__(self, *reporters: ProgressCallback) -> None:
        self._reporters = reporters

    def __call__(self, event: ProgressEvent) -> None:
        for reporter in self._reporters:
            reporter(event)


def combine_progress(*reporters: ProgressCallback | None) -> ProgressCallback | None:
    """Return no callback, one callback, or a fan-out callback as appropriate."""
    selected = tuple(reporter for reporter in reporters if reporter is not None)
    if not selected:
        return None
    if len(selected) == 1:
        return selected[0]
    return CombinedProgressReporter(*selected)


def _sanitize(value: object) -> object:
    """Mask every string recursively without accepting arbitrary object serializers."""
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, Path):
        return mask_secrets(str(value))
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return mask_secrets(str(value))
