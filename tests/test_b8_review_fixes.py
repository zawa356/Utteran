"""PR #33 review fix B8-P2-1: real OS subprocess dual-pipe draining and bounded memory.

Every case uses a real Python child process (Linux and Windows). The runner is executed in
a worker thread with a hard deadline; on timeout the child is killed and the test fails,
so the pre-fix deadlock shows up as a failure instead of a hung test run.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest

from utteran_gui.setup_executor import (
    MAX_STATUS_LINE,
    ChildOutputError,
    ChildUnconfirmed,
    ProcessChildRunner,
)

FLOOD = 4 * 1024 * 1024  # far above any OS pipe buffer (64 KiB Linux, 4 KiB-1 MiB Windows)
DEADLINE = 60.0


def _env() -> dict[str, str]:
    import os

    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "COMSPEC")
    return {k: os.environ[k] for k in keep if k in os.environ}


def run_child(
    code: str,
    tmp_path: Path,
    *,
    capture_status: bool = True,
    on_spawn: Any = None,
    popen_factory: Any = None,
) -> tuple[Any, list[Any]]:
    """Run ``code`` through ProcessChildRunner with a hard deadline (kills on timeout)."""
    spawned: list[Any] = []

    def record(process: Any) -> None:
        spawned.append(process)
        if on_spawn is not None:
            on_spawn(process)

    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = ProcessChildRunner(popen_factory).run(
                [sys.executable, "-c", code],
                cwd=tmp_path,
                env=_env(),
                on_spawn=record,
                capture_status=capture_status,
            )
        except BaseException as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(DEADLINE)
    if worker.is_alive():
        for process in spawned:
            raw = getattr(process, "inner", process)
            if raw.poll() is None:
                raw.kill()
        worker.join(10)
        pytest.fail("ProcessChildRunner did not finish: parent/child pipe deadlock")
    if "error" in outcome:
        return outcome["error"], spawned
    return outcome["result"], spawned


STDERR_THEN_STDOUT = (
    "import sys\n"
    f"sys.stderr.write('e' * {FLOOD}); sys.stderr.flush()\n"
    'print(\'{"access": "available"}\')\n'
)
STDOUT_THEN_STDERR = (
    "import sys\n"
    f"sys.stdout.write(('o' * 1023 + '\\n') * {FLOOD // 1024}); sys.stdout.flush()\n"
    f"sys.stderr.write('e' * {FLOOD}); sys.stderr.flush()\n"
)
INTERLEAVED = (
    "import sys\n"
    "for _ in range(64):\n"
    "    sys.stdout.write('o' * 65536 + '\\n'); sys.stdout.flush()\n"
    "    sys.stderr.write('e' * 65536 + '\\n'); sys.stderr.flush()\n"
    'print(\'{"access": "available"}\')\n'
)


def test_stderr_flood_before_stdout_does_not_deadlock(tmp_path: Path) -> None:
    result, _ = run_child(STDERR_THEN_STDOUT, tmp_path)
    assert result.exit_code == 0
    assert json.loads(result.status_line) == {"access": "available"}


def test_stdout_flood_then_stderr_flood_does_not_deadlock(tmp_path: Path) -> None:
    result, _ = run_child(STDOUT_THEN_STDERR, tmp_path)
    assert result.exit_code == 0 and result.status_line is None


def test_both_channels_drained_concurrently(tmp_path: Path) -> None:
    started = time.monotonic()
    result, _ = run_child(INTERLEAVED, tmp_path)
    assert result.exit_code == 0
    assert json.loads(result.status_line) == {"access": "available"}
    assert time.monotonic() - started < DEADLINE


def test_unterminated_huge_line_keeps_memory_bounded(tmp_path: Path) -> None:
    size = 32 * 1024 * 1024
    code = (
        "import sys\n"
        f"chunk = 'x' * 1048576\nfor _ in range({size // 1048576}):\n"
        "    sys.stdout.write(chunk); sys.stderr.write(chunk)\n"
        "sys.stdout.flush(); sys.stderr.flush()\n"
    )
    tracemalloc.start()
    try:
        result, _ = run_child(code, tmp_path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.exit_code == 0 and result.status_line is None
    assert peak < 4 * 1024 * 1024  # 64 MiB was written; nothing is accumulated


@pytest.mark.parametrize(
    "code, expected",
    [
        # stderr is never a trusted status channel
        ('import sys; sys.stderr.write(\'{"access": "available"}\\n\')', None),
        # exactly one JSON object line on stdout is accepted
        ("print('progress'); print('{\"access\": \"available\"}')", {"access": "available"}),
        # two JSON object lines: ambiguous -> rejected
        ('print(\'{"access": "token_missing"}\'); print(\'{"access": "available"}\')', None),
        # malformed JSON -> rejected
        ("print('{\"access\": available}')", None),
        # over the size limit -> rejected (never truncated into a valid-looking value)
        (
            f'print(\'{{"access": "available", "pad": "\' + \'p\' * {MAX_STATUS_LINE} + \'"}}\')',
            None,
        ),
        # final line without newline still counts
        ('import sys; sys.stdout.write(\'{"access": "available"}\')', {"access": "available"}),
    ],
)
def test_status_line_only_from_bounded_stdout(tmp_path: Path, code: str, expected: Any) -> None:
    result, _ = run_child(code, tmp_path)
    assert result.exit_code == 0
    assert (None if result.status_line is None else json.loads(result.status_line)) == expected


def test_status_not_captured_unless_requested(tmp_path: Path) -> None:
    result, _ = run_child('print(\'{"access": "available"}\')', tmp_path, capture_status=False)
    assert result.status_line is None


def test_nonzero_exit_is_reported(tmp_path: Path) -> None:
    result, _ = run_child(STDERR_THEN_STDOUT + "raise SystemExit(7)\n", tmp_path)
    assert result.exit_code == 7


def test_on_spawn_failure_stops_and_confirms_child(tmp_path: Path) -> None:
    def boom(process: Any) -> None:
        raise RuntimeError("store unavailable")

    error, spawned = run_child("import time; time.sleep(60)", tmp_path, on_spawn=boom)
    assert isinstance(error, RuntimeError)  # original failure, child confirmed gone
    assert spawned and spawned[0].poll() is not None


class _BrokenStdout:
    """Wraps a real Popen; its stdout read fails (pipe reader exception)."""

    def __init__(self, inner: subprocess.Popen[bytes]) -> None:
        self.inner = inner
        self.pid = inner.pid
        self.stderr = inner.stderr

        class Broken:
            def read1(self, size: int = -1) -> bytes:
                raise OSError("reader failed")

            def close(self) -> None:
                inner.stdout.close()  # type: ignore[union-attr]

        self.stdout = Broken()

    def poll(self) -> int | None:
        return self.inner.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self.inner.wait(timeout)


def _broken_factory(argv: list[str], **kwargs: Any) -> _BrokenStdout:
    return _BrokenStdout(subprocess.Popen(argv, **kwargs))


def test_reader_failure_after_exit_is_a_confirmed_output_error(tmp_path: Path) -> None:
    error, spawned = run_child("print('x')", tmp_path, popen_factory=_broken_factory)
    assert isinstance(error, ChildOutputError)
    assert spawned[0].poll() is not None


def test_reader_failure_with_live_child_is_unconfirmed(tmp_path: Path) -> None:
    code = f"import sys, time\nsys.stdout.write('o' * {FLOOD}); sys.stdout.flush(); time.sleep(60)"
    error, spawned = run_child(code, tmp_path, popen_factory=_broken_factory)
    try:
        # The child cannot be drained and is still running: never treated as finished.
        assert isinstance(error, ChildUnconfirmed)
        assert spawned[0].poll() is None
    finally:
        spawned[0].inner.kill()
        spawned[0].inner.wait(10)


def test_grandchild_holding_pipes_is_unconfirmed(tmp_path: Path) -> None:
    code = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
    )
    error, spawned = run_child(code, tmp_path)
    try:
        # The direct child exited but its pipes stay open (a descendant still runs).
        assert isinstance(error, ChildUnconfirmed)
    finally:
        import os
        import signal

        if os.name != "nt":
            # The child ran in its own session; stop the orphaned descendant too.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(spawned[0].pid, signal.SIGKILL)
