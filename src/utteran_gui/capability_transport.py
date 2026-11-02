"""Bounded, private transport for the opt-in metadata bridge only."""

from __future__ import annotations

import math
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO, cast

from utteran_contracts.capabilities import ReasonCode
from utteran_gui.cli import CliError
from utteran_gui.processes import ProcessSupervisor, build_popen_kwargs

STDOUT_LIMIT = 8 * 1024 * 1024
STDERR_LIMIT = 64 * 1024
REQUEST_LIMIT = 1024 * 1024
_CHUNK = 8192

# Metadata lookup needs OS paths/locale, profile libraries, and managed data roots.
# This is an allowlist, not a denylist of guessed secret names. In particular no
# credential, proxy, SDK, session, PYTHON*, or arbitrary UTTERAN_* values survive.
_ENVIRONMENT = frozenset(
    {
        "SYSTEMROOT",
        "WINDIR",
        "SYSTEMDRIVE",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TZ",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "UTTERAN_DATA_ROOT",
    }
)


class BridgeReadError(CliError):
    """Only a fixed allowlisted code; never carries child output or request text."""

    def __init__(self, code: ReasonCode) -> None:
        self.code = code
        super().__init__(code)


def bridge_environment(profile: str, source: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = {
        key: value
        for key, value in (os.environ if source is None else source).items()
        if key.upper() in _ENVIRONMENT
    }
    environment.update(
        UTTERAN_PROFILE=profile,
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HUB_DISABLE_TELEMETRY="1",
    )
    return environment


PopenFactory = Callable[..., subprocess.Popen[bytes]]


def _reap(supervisor: ProcessSupervisor, process: subprocess.Popen[bytes]) -> None:
    # A dead group leader may leave descendants holding its pipes. POSIX groups
    # must be killed even after poll() says the direct child exited; Windows Job
    # Object closure in supervisor.shutdown covers the corresponding case.
    if os.name != "nt":
        killpg = cast(Callable[[int, int], None], getattr(os, "killpg"))  # noqa: B009
        with suppress(ProcessLookupError):
            killpg(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    supervisor.shutdown(timeout=2.0)
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        raise BridgeReadError("bridge_io_error") from None


def read_bridge(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    input: bytes,
    timeout: float,
    popen_factory: PopenFactory | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Drain both pipes concurrently, limit each while reading, reap before return.

    stdout retains at most 8 MiB; stderr is counted and discarded up to 64 KiB.
    Each reader has at most one additional 8 KiB chunk. No unbounded queues, raw
    error retention, communicate(), shell, or success from truncated JSON.
    """
    if not math.isfinite(timeout) or timeout <= 0 or len(input) > REQUEST_LIMIT:
        raise BridgeReadError("bridge_invalid")
    deadline = time.monotonic() + timeout
    try:
        supervisor = ProcessSupervisor()
    except OSError:
        raise BridgeReadError("bridge_unavailable") from None
    process: subprocess.Popen[bytes] | None = None
    threads: list[threading.Thread] = []
    output = bytearray()
    failures: queue.Queue[ReasonCode] = queue.Queue(maxsize=1)
    changed = threading.Event()
    finished = [threading.Event() for _ in range(3)]

    def fail(code: ReasonCode) -> None:
        with suppress(queue.Full):
            failures.put_nowait(code)
        changed.set()

    try:
        kwargs = build_popen_kwargs(cwd=cwd, env=env)
        kwargs.update(
            stdin=subprocess.PIPE, text=False, encoding=None, errors=None, bufsize=0, shell=False
        )
        # The factory is a test seam inside the normal supervisor launch, so
        # real children still belong to the Windows Job Object / POSIX session.
        if popen_factory is None:
            process = cast(subprocess.Popen[bytes], supervisor.popen(command, **kwargs))
        else:
            # A factory must delegate launch through this supervisor (see tests).
            process = popen_factory(command, supervisor=supervisor, **kwargs)
        child = process
        assert child.stdin is not None and child.stdout is not None and child.stderr is not None
        input_pipe = cast(BinaryIO, child.stdin)

        def drain(pipe: BinaryIO, limit: int, retain: bool, done: threading.Event) -> None:
            total = 0
            try:
                while block := pipe.read(min(_CHUNK, limit - total + 1)):
                    total += len(block)
                    if total > limit:
                        fail("bridge_stdout_limit" if retain else "bridge_stderr_limit")
                        return
                    if retain:
                        output.extend(block)
            except (OSError, ValueError):
                fail("bridge_io_error")
            finally:
                done.set()
                changed.set()

        def send() -> None:
            try:
                view = memoryview(input)
                while view:
                    size = input_pipe.write(view)
                    if not size:
                        raise OSError
                    view = view[size:]
            except (OSError, ValueError):
                fail("bridge_unavailable")
            finally:
                with suppress(OSError):
                    input_pipe.close()
                finished[2].set()
                changed.set()

        for target, args in (
            (drain, (cast(BinaryIO, child.stdout), STDOUT_LIMIT, True, finished[0])),
            (drain, (cast(BinaryIO, child.stderr), STDERR_LIMIT, False, finished[1])),
            (send, ()),
        ):
            thread = threading.Thread(target=target, args=args, daemon=True)
            threads.append(thread)
            thread.start()
        while True:
            if not failures.empty():
                raise BridgeReadError(failures.get_nowait())
            if all(event.is_set() for event in finished) and child.poll() is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BridgeReadError("probe_timeout")
            changed.wait(min(0.05, remaining))
            changed.clear()
        # A reader can enqueue a limit/IO error between the loop's queue check
        # and its final completion check. Completion must never bypass it.
        if not failures.empty():
            raise BridgeReadError(failures.get_nowait())
        if child.returncode != 0:
            raise BridgeReadError("bridge_unavailable")
        return subprocess.CompletedProcess(command, child.returncode, bytes(output), b"")
    except BridgeReadError:
        raise
    except (OSError, RuntimeError):
        raise BridgeReadError("bridge_unavailable") from None
    finally:
        if process is not None:
            _reap(supervisor, process)
            for thread in threads:
                thread.join(timeout=2.0)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
            if any(thread.is_alive() for thread in threads):
                raise BridgeReadError("bridge_io_error")
        else:
            supervisor.shutdown()
