"""Explicit development-only worker client. Never selected by CliAdapter/JobManager."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from utteran_contracts.worker import (
    EVENT_ADAPTER,
    EVENT_LIMIT,
    REQUEST_LIMIT,
    WorkerArtifact,
    WorkerCancel,
    WorkerCompleted,
    WorkerError,
    WorkerErrorCode,
    WorkerHello,
    WorkerProgress,
    WorkerRequest,
)
from utteran_contracts.worker_framing import ProtocolError, encode_record, read_record
from utteran_contracts.worker_state import EventSequence
from utteran_gui.cli import PROFILE_NAMES, CliAdapter, CliError
from utteran_gui.processes import ProcessSupervisor, build_popen_kwargs, kill_process_tree


class WorkerClientError(ProtocolError):
    """Only a static reason code. No subprocess text or Pydantic input."""


def _verify_file(item: WorkerArtifact) -> Path:
    path = Path(item.path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size != item.size:
        raise WorkerClientError(WorkerErrorCode.ARTIFACT_INVALID)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != item.sha256:
        raise WorkerClientError(WorkerErrorCode.ARTIFACT_INVALID)
    return path.resolve()


def verify_result(request: WorkerRequest, event: WorkerCompleted) -> None:
    """Validate private completion evidence after worker exit; never import core."""
    try:
        general = cast(dict[str, Any], request.context.base_config["general"])
        output = Path(general["output_dir"]).resolve()
        jobs = Path(general["job_dir"]).resolve()
        manifest_path = Path(event.manifest.path).resolve()
        if manifest_path != jobs / event.core_job_id / "manifest.json":
            raise ValueError
        _verify_file(event.manifest)
        if event.manifest.size > REQUEST_LIMIT:
            raise ValueError
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest["version"] != 1
            or manifest["job_id"] != event.core_job_id
            or Path(manifest["input"]["path"]).resolve() != Path(request.input_path).resolve()
            or any(
                manifest["stages"][stage]["status"] != "done"
                for stage in ("audio", "asr", "diarization", "merge", "export")
            )
        ):
            raise ValueError
        for stage in manifest["stages"].values():
            for stored_path in stage["artifacts"]:
                path = Path(stored_path).resolve()
                if not path.is_file() or not (
                    path.is_relative_to(jobs / event.core_job_id) or path.parent == output
                ):
                    raise ValueError
        expected_formats = set(request.settings.output.formats)
        if len(event.artifacts) != len(expected_formats):
            raise ValueError
        paths: set[str] = set()
        for item in event.artifacts:
            path = Path(item.path).resolve()
            if path.parent != output or path.suffix[1:] not in expected_formats:
                raise ValueError
            _verify_file(item)
            paths.add(str(path))
        if (
            len(paths) != len(expected_formats)
            or {Path(p).suffix[1:] for p in paths} != expected_formats
        ):
            raise ValueError
        if paths != {str(Path(p).resolve()) for p in manifest["stages"]["export"]["artifacts"]}:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise WorkerClientError(WorkerErrorCode.ARTIFACT_INVALID) from None


class WorkerClient:
    def __init__(self, repo_root: Path, venv_root: Path | None = None) -> None:
        self.adapter = CliAdapter(repo_root, venv_root)
        self._lock = threading.Lock()

    def command(self, profile: str) -> list[str]:
        if profile not in PROFILE_NAMES:
            raise WorkerClientError(WorkerErrorCode.PROFILE_INVALID)
        try:
            info = self.adapter.profile_info(profile)
        except (CliError, OSError):
            raise WorkerClientError(WorkerErrorCode.PROFILE_UNAVAILABLE) from None
        python = info.path / (
            "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
        )
        if not info.exists or not python.is_file():
            raise WorkerClientError(WorkerErrorCode.PROFILE_UNAVAILABLE)
        if info.compatible is not True:
            raise WorkerClientError(WorkerErrorCode.PROFILE_INCOMPATIBLE)
        # Do not resolve the interpreter symlink: that discards venv identity on POSIX.
        return [str(python.absolute()), "-I", "-m", "utteran.worker", "--protocol", "1"]

    def execute(
        self,
        request: WorkerRequest,
        *,
        cancel: threading.Event | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        handshake_timeout: float = 10.0,
        io_timeout: float = 10.0,
        validation_timeout: float = 30.0,
        deadline_seconds: float | None = None,
    ) -> WorkerCompleted:
        """Soft processing deadline requests cancellation; waits for core cleanup.

        Silent inference has no idle timeout. Native hard cancellation is unsupported.
        on_event receives only the safe projection, never raw/private completions.
        """
        if any(v <= 0 for v in (handshake_timeout, io_timeout, validation_timeout)) or (
            deadline_seconds is not None and deadline_seconds <= 0
        ):
            raise ValueError("timeouts must be positive")
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("worker client is already executing")
        try:
            return self._execute(
                request,
                cancel,
                on_event,
                handshake_timeout,
                io_timeout,
                validation_timeout,
                deadline_seconds,
            )
        except ProtocolError as exc:
            raise WorkerClientError(exc.code) from None
        finally:
            self._lock.release()

    def _execute(
        self,
        request: WorkerRequest,
        cancel: threading.Event | None,
        on_event: Callable[[dict[str, Any]], None] | None,
        handshake_timeout: float,
        io_timeout: float,
        validation_timeout: float,
        deadline_seconds: float | None,
    ) -> WorkerCompleted:
        # Snapshot a fresh validated wire copy; caller mutation cannot alter this run.
        try:
            request = WorkerRequest.model_validate_json(request.model_dump_json())
        except (ValidationError, ValueError):
            raise WorkerClientError(WorkerErrorCode.MALFORMED_MESSAGE) from None
        raw = encode_record(request, REQUEST_LIMIT)
        command = self.command(request.profile)
        environment = self.adapter.environment(request.profile)
        for key in ("PYTHONPATH", "PYTHONHOME"):
            environment.pop(key, None)
        environment.update(
            HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1"
        )
        kwargs = build_popen_kwargs(cwd=self.adapter.repo_root, env=environment)
        kwargs.update(
            stdin=subprocess.PIPE, text=False, encoding=None, errors=None, bufsize=-1, shell=False
        )
        try:
            supervisor = ProcessSupervisor()
        except OSError:
            raise WorkerClientError(WorkerErrorCode.START_FAILED) from None
        process: Any = None
        stopped = threading.Event()
        messages: queue.Queue[Any] = queue.Queue(maxsize=32)
        missing_module = threading.Event()
        readers: list[threading.Thread] = []
        try:
            try:
                process = supervisor.popen(command, **kwargs)
            except (OSError, RuntimeError):
                raise WorkerClientError(WorkerErrorCode.START_FAILED) from None

            def enqueue(item: Any) -> None:
                while not stopped.is_set():
                    try:
                        messages.put(item, timeout=0.1)
                        return
                    except queue.Full:
                        pass

            def stdout() -> None:
                try:
                    while not stopped.is_set():
                        payload = read_record(process.stdout, EVENT_LIMIT)
                        enqueue(EVENT_ADAPTER.validate_python(payload))
                except ProtocolError as exc:
                    enqueue(exc)
                except (OSError, ValidationError, ValueError):
                    enqueue(ProtocolError(WorkerErrorCode.MALFORMED_MESSAGE))

            def stderr() -> None:
                # Drain concurrently; retain no raw diagnostics (including on failure).
                tail = b""
                try:
                    while block := process.stderr.read(8192):
                        tail = (tail + block)[-512:]
                        if b"No module named" in tail and b"utteran" in tail:
                            missing_module.set()
                except OSError:
                    pass

            for target in (stdout, stderr):
                thread = threading.Thread(target=target, daemon=True)
                readers.append(thread)
                thread.start()

            def send(data: bytes) -> None:
                finished: queue.Queue[bool] = queue.Queue(maxsize=1)

                def write() -> None:
                    try:
                        view = memoryview(data)
                        while view:
                            written = process.stdin.write(view)
                            if not written:
                                raise OSError
                            view = view[written:]
                        process.stdin.flush()
                        finished.put(True)
                    except (OSError, ValueError):
                        finished.put(False)

                thread = threading.Thread(target=write, daemon=True)
                readers.append(thread)
                thread.start()
                try:
                    success = finished.get(timeout=io_timeout)
                except queue.Empty:
                    raise WorkerClientError(WorkerErrorCode.IO_TIMEOUT) from None
                if not success:
                    raise WorkerClientError(WorkerErrorCode.UNEXPECTED_EOF)

            state = EventSequence(request)
            started = time.monotonic()
            try:
                hello = messages.get(timeout=handshake_timeout)
            except queue.Empty:
                raise WorkerClientError(WorkerErrorCode.HANDSHAKE_TIMEOUT) from None
            if isinstance(hello, ProtocolError):
                if hello.code != WorkerErrorCode.UNEXPECTED_EOF:
                    raise hello
                try:
                    process.wait(timeout=io_timeout)
                except subprocess.TimeoutExpired:
                    raise WorkerClientError(WorkerErrorCode.IO_TIMEOUT) from None
                for thread in readers:
                    thread.join(timeout=0.1)
                if missing_module.is_set():
                    raise WorkerClientError(WorkerErrorCode.MODULE_MISSING)
                if process.returncode:
                    raise WorkerClientError(WorkerErrorCode.WORKER_CRASH)
                raise hello
            if not isinstance(hello, WorkerHello):
                raise WorkerClientError(WorkerErrorCode.INVALID_SEQUENCE)
            state.accept(hello)
            send(raw)
            validation_started = time.monotonic()
            timed_out: WorkerErrorCode | None = None
            validated = False
            terminal_at: float | None = None
            while True:
                now = time.monotonic()
                if terminal_at is not None and now - terminal_at > io_timeout:
                    raise WorkerClientError(WorkerErrorCode.IO_TIMEOUT)
                if (
                    timed_out is None
                    and state.terminal is None
                    and deadline_seconds is not None
                    and now - started > deadline_seconds
                ):
                    timed_out = WorkerErrorCode.PROCESS_TIMEOUT
                if (
                    timed_out is None
                    and not validated
                    and state.terminal is None
                    and now - validation_started > validation_timeout
                ):
                    timed_out = WorkerErrorCode.VALIDATION_TIMEOUT
                if (
                    state.terminal is None
                    and not state.cancel_sent
                    and ((cancel is not None and cancel.is_set()) or timed_out is not None)
                ):
                    state.cancel_sent = True
                    send(
                        encode_record(
                            WorkerCancel(
                                request_id=request.request_id,
                                job_id=request.job_id,
                                attempt_id=request.attempt_id,
                            ),
                            REQUEST_LIMIT,
                        )
                    )
                try:
                    event = messages.get(timeout=0.05)
                except queue.Empty:
                    continue
                if isinstance(event, ProtocolError):
                    if event.code != WorkerErrorCode.UNEXPECTED_EOF:
                        raise event
                    break
                state.accept(event)
                if isinstance(event, WorkerProgress) and event.state == "validated":
                    validated = True
                if state.terminal is not None and terminal_at is None:
                    terminal_at = time.monotonic()
                if on_event is not None and not isinstance(event, WorkerCompleted):
                    on_event(event.public_projection())
            try:
                returncode = process.wait(timeout=io_timeout)
            except subprocess.TimeoutExpired:
                raise WorkerClientError(WorkerErrorCode.IO_TIMEOUT) from None
            if state.terminal is None:
                raise WorkerClientError(
                    WorkerErrorCode.WORKER_CRASH if returncode else WorkerErrorCode.UNEXPECTED_EOF
                )
            if timed_out is not None:
                raise WorkerClientError(timed_out)
            if isinstance(state.terminal, WorkerError):
                if state.terminal.code == WorkerErrorCode.CANCELLED and not state.cancel_sent:
                    raise WorkerClientError(WorkerErrorCode.INVALID_SEQUENCE)
                if returncode == 0:
                    raise WorkerClientError(WorkerErrorCode.INVALID_SEQUENCE)
                raise WorkerClientError(state.terminal.code)
            if returncode != 0:
                raise WorkerClientError(WorkerErrorCode.WORKER_CRASH)
            verify_result(request, state.terminal)
            if on_event is not None:
                on_event(state.terminal.public_projection())
            return state.terminal
        finally:
            stopped.set()
            if process is not None:
                if process.poll() is None:
                    kill_process_tree(process)
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
                # A dead group leader can leave descendants holding pipes open.
                if os.name != "nt":
                    import signal

                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
                for thread in readers:
                    thread.join(timeout=0.5)
            supervisor.shutdown()
