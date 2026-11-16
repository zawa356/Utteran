"""PR #48 Windows re-acceptance P1: the worker stalled at the first numpy import.

Observed on real Windows (and reproduced on the windows-latest runner): while the
worker's control thread is blocked reading the stdin *pipe*, loading numpy's native
extension (``numpy._core._multiarray_umath``) in the main thread did not return. A pending
synchronous read on the process's standard-input pipe blocks that native initialisation;
the stall ended only when the read completed (a cancel line arrived). Importing numpy
before the reader starts, or moving the protocol input off the process standard input
(fd 0 and STD_INPUT_HANDLE pointing at NUL), both avoided it on the runner.

These tests run the *real* ``python -I -c ... utteran.worker.main()`` child with the GUI's
real spawn flags (``build_popen_kwargs`` + ``ProcessSupervisor``: CREATE_NO_WINDOW,
CREATE_NEW_PROCESS_GROUP and a kill-on-close Job Object on Windows), stdin kept open like
``WorkerClient``. Only ``worker_runtime.execute`` is replaced by a stub whose first action
is the real first ``import numpy`` of the process; framing, hello/accepted, the control
thread, cancel and terminal events are the production code. Every wait is bounded and the
child tree is killed on timeout, so a regression fails in seconds, never stalls CI.
On Linux the stall does not occur; there the tests guard the protocol behaviour only.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from utteran_contracts.worker import REQUEST_LIMIT, WorkerCancel, WorkerRequest
from utteran_contracts.worker_framing import encode_record
from utteran_gui.processes import ProcessSupervisor, build_popen_kwargs, kill_process_tree

ROOT = Path(__file__).parents[1]
STEP = 60.0  # per event; the healthy path needs well under a few seconds

# The child imports nothing heavy before main(): numpy's first import happens inside the
# stub, after accepted, while the production control thread is already reading stdin.
CHILD = r"""
import os, sys, time
import utteran.worker as worker
import utteran.worker_runtime as runtime

MODE = os.environ["UTTERAN_TEST_STUB_MODE"]


def stdin_is_null():
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.GetStdHandle.restype = ctypes.c_void_p
        # FILE_TYPE_CHAR (NUL), not FILE_TYPE_PIPE (the protocol pipe).
        return kernel32.GetFileType(ctypes.c_void_p(kernel32.GetStdHandle(-10))) == 2
    return os.path.samestat(os.fstat(0), os.stat(os.devnull))


def stub(request, cancel, emit):
    assert "numpy" not in sys.modules
    emit("progress", stage="asr", state="running", fraction=None)  # "about to import"
    if MODE == "cancel_before_import":
        deadline = time.monotonic() + 20
        while not cancel.is_cancelled and time.monotonic() < deadline:
            time.sleep(0.01)
    import numpy  # noqa: F401  the process's first numpy import
    emit("progress", stage="asr", state="running", fraction=0.5)  # "imported"
    if MODE == "check_stdin" and not stdin_is_null():
        raise RuntimeError("process standard input is still the protocol pipe")
    if MODE in {"cancel_after_import", "cancel_before_import"}:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            cancel.raise_if_cancelled()
            time.sleep(0.01)
    cancel.raise_if_cancelled()
    return {
        "core_job_id": "0123456789abcdef",
        "artifacts": ({"path": os.path.abspath("synthetic.srt"), "size": 1, "sha256": "a" * 64},),
        "manifest": {"path": os.path.abspath("manifest.json"), "size": 1, "sha256": "b" * 64},
    }


runtime.execute = stub
os._exit(worker.main())
"""


def request(tmp_path: Path) -> WorkerRequest:
    settings = json.loads((ROOT / "docs/architecture/phase-a/examples.json").read_text("utf-8"))[
        "preset"
    ]["settings"]
    return WorkerRequest(
        request_id="req-stdin",
        job_id="job-stdin",
        attempt_id="attempt-stdin",
        profile="cpu",
        input_path=str(tmp_path / "synthetic.wav"),
        settings=settings,
        context=dict(
            base_config={
                "general": {"output_dir": str(tmp_path / "o"), "job_dir": str(tmp_path / "j")}
            },
            asr_backend="faster-whisper",
            asr_model="large-v3-turbo",
            asr_model_dir=str(tmp_path / "model"),
            asr_device="cpu",
            diarization_device="cpu",
            compute_type="int8",
        ),
    )


class Child:
    def __init__(self, process: Any) -> None:
        self.process = process
        self.events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.started = time.monotonic()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for raw in self.process.stdout:
            self.events.put(json.loads(raw))
        self.events.put(None)

    def send(self, payload: bytes) -> None:
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def next(self) -> dict[str, Any]:
        try:
            event = self.events.get(timeout=STEP)
        except queue.Empty:
            pytest.fail(f"worker produced no event for {STEP:.0f}s (stalled)")
        if event is None:
            pytest.fail(f"worker stdout closed early (exit {self.process.poll()})")
        return event

    def until(self, kind: str, **fields: Any) -> list[dict[str, Any]]:
        seen = []
        while True:
            event = self.next()
            seen.append(event)
            if event["type"] == kind and all(event.get(k) == v for k, v in fields.items()):
                return seen
            if event["type"] in {"completed", "error"}:
                pytest.fail(f"terminal {event} before {kind} {fields}")


@contextmanager
def worker(tmp_path: Path, mode: str) -> Iterator[Child]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME"} and not key.startswith("UTTERAN_")
    }
    env.update(UTTERAN_PROFILE="cpu", UTTERAN_TEST_STUB_MODE=mode)
    kwargs = build_popen_kwargs(cwd=tmp_path, env=env)
    kwargs.update(stdin=subprocess.PIPE, text=False, encoding=None, errors=None, bufsize=-1)
    supervisor = ProcessSupervisor()
    process = supervisor.popen([sys.executable, "-I", "-c", CHILD, "--protocol", "1"], **kwargs)
    try:
        yield Child(process)
        process.wait(timeout=STEP)
    finally:
        if process.poll() is None:
            kill_process_tree(process)
        supervisor.shutdown()


def cancel_line() -> bytes:
    return encode_record(
        WorkerCancel(request_id="req-stdin", job_id="job-stdin", attempt_id="attempt-stdin"),
        REQUEST_LIMIT,
    )


def test_first_numpy_import_completes_while_the_control_thread_waits(tmp_path: Path) -> None:
    with worker(tmp_path, "complete") as child:
        hello = child.next()
        assert hello["type"] == "hello" and time.monotonic() - child.started < 10
        child.send(encode_record(request(tmp_path), REQUEST_LIMIT))
        kinds = [e["type"] for e in child.until("completed")]
        elapsed = time.monotonic() - child.started
    # stdin stayed open the whole time: the control thread was blocked reading it.
    assert kinds[0] == "accepted" and kinds.count("progress") == 2
    assert child.process.returncode == 0 and elapsed < STEP


def test_process_standard_input_is_null_not_the_protocol_pipe(tmp_path: Path) -> None:
    with worker(tmp_path, "check_stdin") as child:
        child.next()
        child.send(encode_record(request(tmp_path), REQUEST_LIMIT))
        terminal = child.until("completed")[-1]
    assert terminal["type"] == "completed"


@pytest.mark.parametrize("mode", ["cancel_before_import", "cancel_after_import"])
def test_cancel_around_the_first_import_reaches_a_cancelled_terminal(
    tmp_path: Path, mode: str
) -> None:
    with worker(tmp_path, mode) as child:
        child.next()
        child.send(encode_record(request(tmp_path), REQUEST_LIMIT))
        if mode == "cancel_before_import":
            child.until("progress", fraction=None)  # just before the import
        else:
            child.until("progress", fraction=0.5)  # just after the import
        child.send(cancel_line())
        seen = child.until("error")
    kinds = [e["type"] for e in seen]
    # Acknowledged is not the terminal; the terminal is the cancelled error after it.
    assert "cancel_acknowledged" in kinds
    assert kinds.index("cancel_acknowledged") < kinds.index("error")
    assert seen[-1]["code"] == "cancelled" and child.process.returncode == 1


def test_cancel_right_after_accepted_still_terminates_cancelled(tmp_path: Path) -> None:
    with worker(tmp_path, "cancel_before_import") as child:
        child.next()
        child.send(encode_record(request(tmp_path), REQUEST_LIMIT) + cancel_line())
        seen = child.until("error")
    assert seen[0]["type"] == "accepted"
    assert seen[-1]["code"] == "cancelled"


def test_stdin_eof_after_the_request_is_still_unexpected_eof(tmp_path: Path) -> None:
    with worker(tmp_path, "cancel_before_import") as child:
        child.next()
        child.send(encode_record(request(tmp_path), REQUEST_LIMIT))
        child.process.stdin.close()
        seen = child.until("error")
    assert seen[-1]["code"] == "unexpected_eof" and child.process.returncode == 1
