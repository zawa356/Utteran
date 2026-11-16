"""One-request profile worker: python -m utteran.worker --protocol 1."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from typing import Any, BinaryIO

from pydantic import ValidationError

from utteran_contracts.worker import (
    EVENT_ADAPTER,
    EVENT_LIMIT,
    REQUEST_LIMIT,
    WorkerCancel,
    WorkerErrorCode,
    WorkerHello,
    WorkerRequest,
)
from utteran_contracts.worker_framing import ProtocolError, encode_record, read_record


def serve(source: BinaryIO, output: BinaryIO, profile: str) -> int:
    # Imports of the core happen only inside the profile process, after hello.
    lock = threading.RLock()
    sequence = 0
    terminal = False
    identity: dict[str, str] = {}

    def emit(kind: str, **payload: Any) -> None:
        nonlocal sequence, terminal
        with lock:
            if terminal:
                return
            event = EVENT_ADAPTER.validate_python(
                dict(type=kind, sequence=sequence, **identity, **payload)
            )
            output.write(encode_record(event, EVENT_LIMIT))
            output.flush()
            sequence += 1
            terminal = kind in {"completed", "error"}

    # Hello has null IDs: accepted binds the validated request's three IDs.
    hello = WorkerHello(sequence=0, profile=profile)  # type: ignore[arg-type]
    output.write(encode_record(hello, EVENT_LIMIT))
    output.flush()
    sequence = 1
    try:
        request = WorkerRequest.model_validate(read_record(source, REQUEST_LIMIT))
        identity.update(
            request_id=request.request_id, job_id=request.job_id, attempt_id=request.attempt_id
        )
        if request.profile != profile:
            raise ProtocolError(WorkerErrorCode.ID_MISMATCH)
        from utteran.types import CancelToken
        from utteran.worker_runtime import error_code, execute

        cancel = CancelToken()
        control_error: list[WorkerErrorCode] = []
        emit("accepted")

        def control() -> None:
            try:
                while True:
                    message = WorkerCancel.model_validate(read_record(source, REQUEST_LIMIT))
                    if any(getattr(message, key) != value for key, value in identity.items()):
                        raise ProtocolError(WorkerErrorCode.ID_MISMATCH)
                    with lock:
                        if terminal:
                            return
                        cancel.cancel()
                        emit("cancel_acknowledged")
            except Exception as exc:
                with lock:
                    if terminal:
                        return
                    code = (
                        exc.code
                        if isinstance(exc, ProtocolError)
                        else WorkerErrorCode.MALFORMED_MESSAGE
                    )
                    control_error.append(code)
                    cancel.cancel()

        threading.Thread(target=control, daemon=True, name="worker-control").start()
        try:
            result = execute(request, cancel, emit)
            with lock:
                if control_error:
                    raise ProtocolError(control_error[0])
                emit("completed", **result)
            return 0
        except Exception as exc:
            emit("error", code=control_error[0] if control_error else error_code(exc))
            return 1
    except Exception as exc:
        code = exc.code if isinstance(exc, ProtocolError) else WorkerErrorCode.MALFORMED_MESSAGE
        emit("error", code=code)
        return 1


def _point_std_input_at_null() -> None:
    """Make fd 0 (and on Windows STD_INPUT_HANDLE) the null device.

    Only the worker's private protocol input reads the request/cancel pipe. On Windows a
    synchronous read pending on the process's standard-input pipe (the control thread)
    blocked the native initialisation of numpy's extension module in the main thread
    until the read completed (PR #48 re-acceptance; reproduced on windows-latest).
    Native libraries that look at the inherited standard input now see NUL instead.
    """
    with open(os.devnull, "rb") as null:
        os.dup2(null.fileno(), 0)
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetStdHandle.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
        kernel32.SetStdHandle.restype = ctypes.c_int
        if not kernel32.SetStdHandle(ctypes.c_uint32(-10 & 0xFFFFFFFF), msvcrt.get_osfhandle(0)):
            raise OSError(ctypes.get_last_error(), "SetStdHandle failed")


def main() -> int:
    # A separate fd owns protocol output. Discard ALL backend/native stdout/stderr;
    # raw third-party diagnostics are not safe to redact by regex after the fact.
    protocol_fd = os.dup(sys.stdout.fileno())
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    # Likewise a separate (non-inheritable) fd owns protocol input; the process standard
    # input becomes NUL before any core/backend import, so children and native code can
    # neither inherit nor query the request/cancel pipe.
    protocol_in = os.dup(sys.stdin.fileno())
    try:
        _point_std_input_at_null()
    except OSError:
        return 1
    logging.disable(sys.maxsize)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=int, required=True)
    args = parser.parse_args()
    if args.protocol != 1:
        return 2
    profile = os.environ.get("UTTERAN_PROFILE", "")
    if profile not in {"cpu", "cuda", "intel", "vulkan"}:
        return 2
    # Config is a BaseSettings subclass: even model_validate can consult env
    # for omitted defaults (or fail parsing irrelevant malformed env JSON).
    # The worker owns this process. Keep runtime/token/profile variables, but
    # discard ALL ambient core settings before adapting the finalized request.
    sections = ("general", "asr", "diarization", "output", "ffmpeg", "alignment")
    prefixes = tuple(f"utteran_{section}" for section in sections)
    for key in tuple(os.environ):
        name = key.casefold()
        if any(name == prefix or name.startswith(prefix + "__") for prefix in prefixes):
            del os.environ[key]
    # Defense in depth for the audited local-only runtime, before SDK imports.
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    # Never closed here: the daemon control thread may still hold the reader's lock while
    # blocked in readline, so close() would deadlock. os._exit ends the process instead
    # (as it did for sys.stdin.buffer).
    source = os.fdopen(protocol_in, "rb")
    with os.fdopen(protocol_fd, "wb", buffering=0) as output:
        try:
            return serve(source, output, profile)
        except (BrokenPipeError, OSError, ValidationError):
            return 1


if __name__ == "__main__":
    # Control reader is a daemon blocked on stdin; avoid buffered stdin teardown
    # races. Core execution/finally and protocol fd closure have already finished.
    os._exit(main())
