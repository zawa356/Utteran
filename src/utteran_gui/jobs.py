"""Single-job subprocess orchestration, progress parsing, and tree cancellation."""

from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast

from utteran_gui.cli import CliAdapter, TranscriptionOptions
from utteran_gui.operation_queue import OperationQueue, QueueStatus
from utteran_gui.processes import PopenFactory, TreeKiller, build_popen_kwargs, kill_process_tree
from utteran_gui.security import mask_secrets, sanitize_json

JobStatus = Literal["starting", "running", "completed", "failed", "cancelled"]
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class JobBusyError(RuntimeError):
    """A second request was made while the one allowed job is active."""


class JobUnknownError(KeyError):
    """A requested GUI job id does not exist in this process."""


@dataclass
class GuiJob:
    id: str
    options: TranscriptionOptions
    command: list[str]
    environment: dict[str, str]
    status: JobStatus = "starting"
    created_at: str = field(default_factory=lambda: _now())
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    events: list[dict[str, object]] = field(default_factory=list)
    raw_logs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    last_event_monotonic: float = field(default_factory=time.monotonic)
    started_monotonic: float | None = None
    process: subprocess.Popen[str] | None = None


class JobManager:
    """Run one profile CLI process and retain its complete diagnostic stream."""

    def __init__(
        self,
        cli: CliAdapter,
        *,
        popen_factory: PopenFactory | None = None,
        tree_killer: TreeKiller | None = None,
        stall_seconds: float = 30.0,
        operation_queue: OperationQueue | None = None,
    ) -> None:
        self.cli = cli
        self._popen_factory = popen_factory or cast(PopenFactory, subprocess.Popen)
        self._tree_killer = tree_killer or kill_process_tree
        self._stall_seconds = stall_seconds
        self.queue = operation_queue or OperationQueue()
        self._lock = threading.RLock()
        self._jobs: dict[str, GuiJob] = {}

    def start(self, options: TranscriptionOptions) -> dict[str, object]:
        command, environment = self.cli.build_transcribe_command(options)
        with self._lock:
            job_id = uuid.uuid4().hex
            job = GuiJob(job_id, options, command, environment)
            self._jobs[job_id] = job
        self.queue.submit(
            job_id,
            kind="transcription",
            label=options.input_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
            runner=lambda: self._run_queued(job_id),
            canceller=lambda: self._cancel_direct(job_id),
        )
        return self.snapshot(job_id)

    def cancel(self, job_id: str) -> dict[str, object]:
        self.queue.cancel(job_id)
        return self.snapshot(job_id)

    def shutdown(self) -> None:
        """Cancel every non-terminal job before the GUI server exits."""
        with self._lock:
            job_ids = [job.id for job in self._jobs.values() if job.status not in TERMINAL_STATUSES]
        for job_id in job_ids:
            self._cancel_direct(job_id)

    def _cancel_direct(self, job_id: str) -> None:
        with self._lock:
            job = self._job(job_id)
            if job.status in TERMINAL_STATUSES:
                return
            job.cancel_requested = True
            process = job.process
            if process is None:
                self._finish_cancelled(job)
                return
        if process is not None and process.poll() is None:
            self._tree_killer(process)

    def _run_queued(self, job_id: str) -> QueueStatus:
        self._run(job_id)
        status = self.snapshot(job_id)["status"]
        return cast(QueueStatus, status)

    def snapshot(self, job_id: str) -> dict[str, object]:
        with self._lock:
            return self._snapshot(self._job(job_id))

    def events_since(self, job_id: str, cursor: int) -> tuple[list[dict[str, object]], bool]:
        with self._lock:
            job = self._job(job_id)
            events = [dict(event) for event in job.events[cursor:]]
            terminal = job.status in TERMINAL_STATUSES
        return events, terminal

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._job(job_id)
            if job.cancel_requested:
                self._finish_cancelled(job)
                return
            kwargs = build_popen_kwargs(cwd=self.cli.repo_root, env=job.environment)
            try:
                process = self._popen_factory(job.command, **kwargs)
            except Exception as exc:
                self._append_raw(job, f"Failed to launch CLI: {exc}")
                self._append_event(
                    job,
                    {
                        "schema_version": 1,
                        "timestamp": _now(),
                        "event": "error",
                        "error_type": type(exc).__name__,
                        "message": mask_secrets(str(exc)),
                        "exit_code": 1,
                    },
                )
                self._finish(job, 1)
                return
            job.process = process
            job.status = "running"
            job.started_at = _now()
            job.started_monotonic = time.monotonic()

        stdout_thread = threading.Thread(
            target=self._read_plain_stream,
            args=(job_id, process.stdout),
            name=f"utteran-gui-stdout-{job_id[:8]}",
            daemon=True,
        )
        stdout_thread.start()
        if process.stderr is not None:
            for line in process.stderr:
                self._handle_stderr_line(job_id, line)
        return_code = process.wait()
        stdout_thread.join(timeout=2.0)
        with self._lock:
            job = self._job(job_id)
            effective_code = 130 if job.cancel_requested else return_code
            self._finish(job, effective_code)

    def _read_plain_stream(self, job_id: str, stream: Any) -> None:
        if stream is None:
            return
        for line in stream:
            with self._lock:
                self._append_raw(self._job(job_id), line)

    def _handle_stderr_line(self, job_id: str, line: str) -> None:
        parsed = parse_progress_line(line)
        with self._lock:
            job = self._job(job_id)
            if parsed is None:
                self._append_raw(job, line)
                return
            self._append_event(job, parsed)
            if parsed.get("event") == "output_written":
                path = parsed.get("path")
                if isinstance(path, str) and path not in job.outputs:
                    job.outputs.append(path)

    def _finish(self, job: GuiJob, exit_code: int) -> None:
        job.exit_code = exit_code
        job.finished_at = _now()
        job.process = None
        if exit_code == 130:
            job.status = "cancelled"
        elif exit_code == 0:
            job.status = "completed"
        else:
            job.status = "failed"
        if not any(event.get("event") == "done" for event in job.events):
            self._append_event(
                job,
                {
                    "schema_version": 1,
                    "timestamp": _now(),
                    "event": "done",
                    "duration_seconds": _duration(job),
                    "exit_code": exit_code,
                    "synthetic": True,
                },
            )

    def _finish_cancelled(self, job: GuiJob) -> None:
        self._finish(job, 130)

    def _append_event(self, job: GuiJob, event: dict[str, object]) -> None:
        sanitized = cast(dict[str, object], sanitize_json(event))
        sanitized["id"] = len(job.events)
        job.events.append(sanitized)
        job.last_event_monotonic = time.monotonic()

    def _append_raw(self, job: GuiJob, line: str) -> None:
        text = mask_secrets(line.rstrip("\r\n"))
        if text:
            job.raw_logs.append(text)
            job.last_event_monotonic = time.monotonic()

    def _snapshot(self, job: GuiJob) -> dict[str, object]:
        stalled = (
            job.status == "running"
            and time.monotonic() - job.last_event_monotonic >= self._stall_seconds
        )
        guidance = guidance_for(job.exit_code, job.raw_logs, job.events)
        return {
            "id": job.id,
            "status": job.status,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "exit_code": job.exit_code,
            "stalled": stalled,
            "outputs": list(job.outputs),
            "events": [dict(event) for event in job.events],
            "logs": list(job.raw_logs),
            "guidance": guidance,
        }

    def _job(self, job_id: str) -> GuiJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobUnknownError(job_id) from None


def parse_progress_line(line: str) -> dict[str, object] | None:
    """Parse a complete JSON line; malformed or partial lines remain raw diagnostics."""
    stripped = line.rstrip("\r\n")
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return cast(dict[str, object], sanitize_json(payload))


def guidance_for(
    exit_code: int | None,
    logs: list[str],
    events: list[dict[str, object]],
    *,
    operation: str | None = None,
) -> dict[str, str] | None:
    """Map CLI exit semantics and common dependency failures to actionable UI copy keys.

    ``license`` (model usage terms not accepted, e.g. ModelAgreementError /
    GatedRepoError / HTTP 403) is checked before the generic ``token`` bucket
    (the token itself being invalid, e.g. HuggingFaceAuthenticationError /
    HTTP 401) so the two Phase 1 error classes stay distinguishable in the UI
    instead of collapsing into one "token" message (Phase 5c requirement).
    """
    if exit_code is None or exit_code == 0:
        return None
    model_operation = operation != "venv_build"
    combined = " ".join(logs + [str(event.get("message", "")) for event in events]).casefold()
    error_types = {str(event.get("error_type", "")).casefold() for event in events}
    if exit_code == 130:
        key = "cancelled"
    elif operation == "native_build":
        key = "native"
    elif "memorybudgeterror" in error_types or "memory" in combined or "メモリ" in combined:
        key = "memory"
    elif model_operation and (
        "modelagreementerror" in error_types
        or "利用条件" in combined
        or "gatedrepoerror" in combined
    ):
        key = "license"
    elif model_operation and ("token" in combined or "トークン" in combined):
        key = "token"
    elif model_operation and ("model" in combined or "モデル" in combined):
        key = "model"
    elif "ffmpeg" in combined:
        key = "ffmpeg"
    else:
        key = {1: "general", 2: "configuration", 3: "dependency", 4: "input", 5: "partial"}.get(
            exit_code, "general"
        )
    return {"key": key, "settings_anchor": "token" if key in ("token", "license") else ""}


def _duration(job: GuiJob) -> float:
    if job.started_monotonic is None:
        return 0.0
    return max(0.0, time.monotonic() - job.started_monotonic)


def _now() -> str:
    return datetime.now(UTC).isoformat()
