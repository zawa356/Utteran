"""B8 durable setup execution through a fixed allowlist of step commands.

Evidence order for every child process (each committed before the next starts):
``step_started`` (step running) -> ``child_intent`` (launch = intended) -> spawn ->
``step_launched`` (PID + OS start identity) -> exit observed -> ``step_exited`` ->
per-step verification -> ``step_finished`` (succeeded / skipped only with evidence).
A step is never ``succeeded`` from an exit code or a file's existence alone; a child whose
exit cannot be observed keeps the heavy slot (``recovery_required``).

The executor runs inside the single B7 dispatcher thread (one heavy slot for jobs and
setups). Commands are fixed argv lists with ``shell=False``; plan values only fill
validated slots (profile kind, catalog model ref). Child output is drained and dropped
except the bounded JSON status line of the credential preflight.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from utteran_contracts.control import OwnerIdentity, ProcessIdentity
from utteran_contracts.setup import (
    SETUP_MODELS,
    EvidenceKind,
    PlanContent,
    SetupErrorCode,
    SetupStep,
    StepReason,
)
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.setup_repository import SetupControlStore
from utteran_gui.process_verifier import start_identity
from utteran_gui.setup_planner import SetupObserver, validate_dag

LOGGER = logging.getLogger(__name__)
MAX_STATUS_LINE = 64 * 1024


class ChildUnconfirmed(Exception):
    """A spawned child's exit could not be observed; the slot must stay occupied."""


class StepUnsupported(Exception):
    pass


@dataclass(frozen=True)
class ChildResult:
    exit_code: int
    status_line: str | None  # last JSON object line (bounded), only when requested


class ChildRunner(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        on_spawn: Callable[[Any], None],
        capture_status: bool,
    ) -> ChildResult: ...


class ChildOutputError(Exception):
    """A pipe reader failed after the child exit was observed (exit is confirmed)."""


READ_CHUNK = 64 * 1024
# After the direct child exits, its pipes must reach EOF within this grace period; a
# descendant still holding them means the process tree is not confirmed finished.
PIPE_EOF_GRACE = 10.0


class _Drain:
    """Reads one pipe to EOF in fixed-size chunks and drops the bytes.

    Memory is bounded by ``READ_CHUNK`` plus, when ``capture`` is set, one candidate status
    line of at most ``MAX_STATUS_LINE`` bytes (a longer line is discarded, never truncated).
    """

    def __init__(self, stream: Any, *, capture: bool) -> None:
        self.stream = stream
        self.capture = capture
        self.error: BaseException | None = None
        self.json_lines: list[str] = []
        self.ambiguous = False
        self._line = bytearray()
        self._overflow = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="utteran-setup-pipe")

    def _end_line(self) -> None:
        if self.capture and not self._overflow and self._line.strip():
            try:
                text = self._line.decode("utf-8").strip()
            except UnicodeDecodeError:
                text = ""
            if text.startswith("{"):
                if len(self.json_lines) >= 1:
                    self.ambiguous = True
                else:
                    self.json_lines.append(text)
        elif self.capture and self._overflow:
            self.ambiguous = True  # an over-long line may have been a status: refuse
        self._line.clear()
        self._overflow = False

    def _feed(self, chunk: bytes) -> None:
        if not self.capture:
            return
        start = 0
        while True:
            index = chunk.find(b"\n", start)
            piece = chunk[start:] if index < 0 else chunk[start:index]
            if not self._overflow:
                if len(self._line) + len(piece) > MAX_STATUS_LINE:
                    self._overflow = True
                    self._line.clear()
                else:
                    self._line.extend(piece)
            if index < 0:
                return
            self._end_line()
            start = index + 1

    def _run(self) -> None:
        reader = getattr(self.stream, "read1", None) or self.stream.read
        try:
            while True:
                chunk = reader(READ_CHUNK)
                if not chunk:
                    break
                self._feed(chunk)
            self._end_line()
        except BaseException as exc:  # reported to the runner; never re-raised here
            self.error = exc

    def status(self) -> str | None:
        if self.ambiguous or len(self.json_lines) != 1:
            return None
        try:
            parsed = json.loads(self.json_lines[0])
        except ValueError:
            return None  # malformed: never interpreted
        return self.json_lines[0] if isinstance(parsed, dict) else None


def _close(stream: Any) -> None:
    with contextlib.suppress(Exception):
        if stream is not None:
            stream.close()


class ProcessChildRunner:
    """Production runner: ``Popen(shell=False)`` via the GUI process supervisor.

    stdout and stderr are drained *concurrently* by two reader threads (no ``selectors``,
    so Windows pipes work), in bounded chunks that are discarded. Only stdout is a trusted
    status channel. The runner never declares a child finished without observing its
    exit and the EOF of both pipes; otherwise it raises ``ChildUnconfirmed``.
    """

    def __init__(
        self,
        popen_factory: Callable[..., Any] | None = None,
        *,
        eof_grace: float = PIPE_EOF_GRACE,
    ) -> None:
        self.popen_factory: Callable[..., Any] = popen_factory or subprocess.Popen
        self.eof_grace = eof_grace

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        on_spawn: Callable[[Any], None],
        capture_status: bool,
    ) -> ChildResult:
        from utteran_gui.processes import build_popen_kwargs

        kwargs = build_popen_kwargs(cwd=cwd, env=env)
        # Binary pipes: bounded chunk reads (no line assembly in a text wrapper).
        for name in ("text", "encoding", "errors", "bufsize"):
            kwargs.pop(name, None)
        kwargs["shell"] = False
        process = self.popen_factory(argv, **kwargs)
        try:
            on_spawn(process)
        except BaseException as exc:
            _stop(process)
            with contextlib.suppress(Exception):
                process.wait(timeout=5)
            alive = process.poll() is None
            _close(process.stdout)
            _close(process.stderr)
            if alive:
                raise ChildUnconfirmed from exc
            raise
        drains = [
            _Drain(stream, capture=capture_status and stream is process.stdout)
            for stream in (process.stdout, process.stderr)
            if stream is not None
        ]
        for drain in drains:
            drain.thread.start()
        try:
            while True:
                try:
                    code = process.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if any(not d.thread.is_alive() and d.error is not None for d in drains):
                        # A pipe can no longer be drained while the child still runs.
                        raise ChildUnconfirmed from None
        except ChildUnconfirmed:
            raise
        except BaseException:
            if process.poll() is None:
                raise ChildUnconfirmed from None
            raise
        deadline = time.monotonic() + self.eof_grace
        for drain in drains:
            drain.thread.join(max(0.0, deadline - time.monotonic()))
        if any(drain.thread.is_alive() for drain in drains):
            raise ChildUnconfirmed  # a descendant still holds the pipes
        for stream in (process.stdout, process.stderr):
            _close(stream)
        if any(drain.error is not None for drain in drains):
            raise ChildOutputError
        status = next((d.status() for d in drains if d.capture), None)
        return ChildResult(int(code), status)


def _stop(process: Any) -> None:
    from utteran_gui.processes import kill_process_tree

    if process.poll() is None:
        kill_process_tree(process)


class StepAdapter(Protocol):
    """Fixed command builders + verifications for the allowlisted step kinds."""

    repo_root: Path

    def environment(self, profile: str) -> dict[str, str]: ...

    def profile_command(self, profile: str, arguments: list[str]) -> list[str]: ...

    def venv_command(self, profile: str) -> list[str]: ...

    def profile_verified(self, profile: str) -> bool: ...


class ProductionStepAdapter:
    """Uses the existing ``setup.ps1`` and profile CLI (``models``/``config``/``transcribe``)."""

    def __init__(self, cli: Any) -> None:
        self.cli = cli
        self.repo_root = Path(cli.repo_root)

    def environment(self, profile: str) -> dict[str, str]:
        env: dict[str, str] = self.cli.environment(profile)
        return env

    def profile_command(self, profile: str, arguments: list[str]) -> list[str]:
        command: list[str] = self.cli.command(profile, arguments)
        return command

    def venv_command(self, profile: str) -> list[str]:
        # Windows PowerShell 5.1 compatible, no shell, fixed flags only.
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.repo_root / "setup.ps1"),
            "-Profile",
            profile,
            "-Yes",
        ]

    def profile_verified(self, profile: str) -> bool:
        info = self.cli.profile_info(profile)
        return bool(info.exists) and info.compatible is True


@dataclass
class _Run:
    operation_id: str
    fence: int
    lost: threading.Event
    cancel: threading.Event


class _Wait(Exception):
    def __init__(
        self,
        reason: Literal["credential_required", "agreement_required", "inventory_changed"],
    ) -> None:
        self.reason = reason


class _Fail(Exception):
    def __init__(self, code: SetupErrorCode, step: str | None) -> None:
        self.code, self.step = code, step


class _Stop(Exception):
    """Cooperative cancel observed at a child boundary, or ownership lost."""


class SetupExecutor:
    """Runs one setup attempt to a fenced end. Called by the B7 dispatcher thread only."""

    def __init__(
        self,
        store: SetupControlStore,
        *,
        adapter: StepAdapter,
        observer: SetupObserver,
        runner: ChildRunner,
        lease_seconds: int = 60,
        identity_of: Callable[[int], str | None] = start_identity,
        work_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.observer = observer
        self.runner = runner
        self.lease_seconds = lease_seconds
        self.identity_of = identity_of
        self.work_dir = work_dir
        self._lock = threading.Lock()
        self._active: _Run | None = None

    @property
    def active_operation(self) -> str | None:
        with self._lock:
            return None if self._active is None else self._active.operation_id

    def request_cancel(self) -> None:
        """Shutdown: record cancelling (cooperative, honoured at the next child boundary)."""
        with self._lock:
            active = self._active
        if active is None:
            return
        try:
            view = self.store.get(active.operation_id)
            if view.status == "running":
                self.store.transition(
                    active.operation_id,
                    {
                        "schema_version": 1,
                        "expected_revision": view.revision,
                        "to_status": "cancelling",
                        "reason_code": "cancel_accepted",
                    },
                )
        except ControlError:
            pass
        active.cancel.set()

    def notify_cancel(self, operation_id: str) -> None:
        with self._lock:
            if self._active is not None and self._active.operation_id == operation_id:
                self._active.cancel.set()

    # ------------------------------------------------------------------ dispatch

    def dispatch(self, operation_id: str, revision: int, owner: OwnerIdentity) -> bool:
        try:
            content, last = self.store.setup_plan_for(operation_id)
        except ControlError:
            return True
        observation = self.observer.observe()
        known = {content.inventory_fingerprint, *((last,) if last else ())}
        try:
            grant = self.store.begin_attempt(
                operation_id, revision, owner, lease_seconds=self.lease_seconds
            )
        except ControlError as exc:
            if exc.code in {
                ErrorCode.REVISION_CONFLICT,
                ErrorCode.NOT_DISPATCHABLE,
                ErrorCode.SLOT_OCCUPIED,
                ErrorCode.QUEUE_PAUSED,
                ErrorCode.INVALID_TRANSITION,
            }:
                return True
            raise
        run = _Run(operation_id, grant.fencing_token, threading.Event(), threading.Event())
        with self._lock:
            self._active = run
        try:
            if observation.fingerprint not in known:
                # Environment changed since approval: never execute an outdated plan.
                self._commit(
                    lambda: self.store.setup_wait(operation_id, run.fence, "inventory_changed")
                )
                return True
            self._attempt(run, content)
        finally:
            with self._lock:
                self._active = None
        return True

    def _heartbeat(self, run: _Run, done: threading.Event) -> None:
        interval = max(self.lease_seconds / 3, 0.05)
        while not done.wait(interval):
            try:
                self.store.renew_lease(
                    run.operation_id, run.fence, lease_seconds=self.lease_seconds
                )
                if self.store.get(run.operation_id).status == "cancelling":
                    run.cancel.set()
            except ControlError as exc:
                if exc.code is ErrorCode.FENCING_TOKEN_STALE:
                    run.lost.set()
                    run.cancel.set()
                    return

    def _attempt(self, run: _Run, content: PlanContent) -> None:
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(run, done), daemon=True)
        beat.start()
        outcome: tuple[str, Any] = ("succeeded", None)
        try:
            self.store.reset_steps(run.operation_id, run.fence)
            for step in validate_dag(content.steps):
                self._boundary(run)
                self._step(run, content, step)
            self._boundary(run)
        except _Wait as wait:
            outcome = ("wait", wait.reason)
        except _Fail as fail:
            outcome = ("failed", fail)
        except _Stop:
            outcome = ("cancelled", None)
        except ChildUnconfirmed:
            outcome = ("unconfirmed", None)
        except ControlError as exc:
            if exc.code is ErrorCode.FENCING_TOKEN_STALE:
                outcome = ("lost", None)
            else:
                LOGGER.warning("v1 setup step bookkeeping failed: %s", exc.code.value)
                outcome = ("failed", _Fail("executor_failed", None))
        except Exception:
            LOGGER.warning("v1 setup executor failed")
            outcome = ("failed", _Fail("executor_failed", None))
        finally:
            done.set()
            beat.join(timeout=5)
        if run.lost.is_set() or outcome[0] == "lost":
            return  # another owner holds the fence: write nothing
        operation_id, fence = run.operation_id, run.fence
        if outcome[0] == "unconfirmed":
            self._commit(lambda: self.store.flag_setup_unconfirmed(operation_id, fence))
            return
        last = self.observer.observe().fingerprint
        if outcome[0] == "wait":
            reason = outcome[1]
            self._commit(
                lambda: self.store.setup_wait(operation_id, fence, reason, last_fingerprint=last)
            )
        elif outcome[0] == "failed":
            failure = outcome[1]
            self._commit(
                lambda: self.store.finish_setup(
                    operation_id,
                    fence,
                    "failed",
                    error=failure.code,
                    error_step=failure.step,
                    last_fingerprint=last,
                )
            )
        else:
            kind = "cancelled" if outcome[0] == "cancelled" else "succeeded"
            self._commit(
                lambda: self.store.finish_setup(
                    operation_id,
                    fence,
                    kind,  # type: ignore[arg-type]
                    last_fingerprint=last,
                )
            )

    def _boundary(self, run: _Run) -> None:
        if run.lost.is_set():
            raise _Stop
        if run.cancel.is_set() or self.store.get(run.operation_id).status == "cancelling":
            raise _Stop

    # ------------------------------------------------------------------ children

    def _child(
        self,
        run: _Run,
        step: SetupStep,
        argv: list[str],
        *,
        profile: str,
        capture_status: bool = False,
    ) -> ChildResult:
        self._boundary(run)
        self.store.child_intent(run.operation_id, run.fence, step.id)
        spawned: list[Any] = []

        def on_spawn(process: Any) -> None:
            spawned.append(process)
            child = ProcessIdentity(pid=process.pid, start_identity=self.identity_of(process.pid))
            self.store.step_launched(run.operation_id, run.fence, step.id, child)

        try:
            result = self.runner.run(
                argv,
                cwd=self.adapter.repo_root,
                env=self.adapter.environment(profile),
                on_spawn=on_spawn,
                capture_status=capture_status,
            )
        except ChildUnconfirmed:
            raise
        except ControlError:
            raise
        except Exception:
            if spawned and spawned[0].poll() is None:
                raise ChildUnconfirmed from None
            if not spawned:
                self.store.step_launched(run.operation_id, run.fence, step.id, None)
                raise _Fail("launch_failed", step.id) from None
            raise _Fail("process_failed", step.id) from None
        if not spawned:
            self.store.step_launched(run.operation_id, run.fence, step.id, None)
            raise _Fail("launch_failed", step.id)
        self.store.step_exited(run.operation_id, run.fence, step.id, result.exit_code)
        return result

    def _finish(
        self,
        run: _Run,
        step: SetupStep,
        status: Literal["succeeded", "failed", "skipped", "pending"],
        reason: StepReason,
        evidence: EvidenceKind | None = None,
    ) -> None:
        self.store.step_finished(run.operation_id, run.fence, step.id, status, reason, evidence)

    def _fail(self, run: _Run, step: SetupStep, reason: StepReason, code: SetupErrorCode) -> None:
        self._finish(run, step, "failed", reason)
        raise _Fail(code, step.id)

    # ------------------------------------------------------------------ steps

    def _step(self, run: _Run, content: PlanContent, step: SetupStep) -> None:
        profile = content.profile_kind
        if step.target is None:
            raise _Fail("unsupported_step", step.id)
        self.store.step_started(run.operation_id, run.fence, step.id)
        if step.kind == "venv_sync":
            self._venv(run, step, profile)
        elif step.kind == "credential_preflight":
            self._preflight(run, step, profile)
        elif step.kind == "model_download":
            self._model(run, step, profile)
        elif step.kind == "smoke":
            self._smoke(run, step, content)
        else:
            self._fail(run, step, "process_failed", "unsupported_step")

    def _venv(self, run: _Run, step: SetupStep, profile: str) -> None:
        if step.target != profile:
            self._fail(run, step, "process_failed", "unsupported_step")
        if self.adapter.profile_verified(profile):
            # Already built and its manifest matches the current lock: do not touch it.
            self._finish(run, step, "skipped", "validated_reuse", "profile_manifest_verified")
            return
        result = self._child(run, step, self.adapter.venv_command(profile), profile=profile)
        if result.exit_code != 0:
            self._fail(run, step, "process_failed", "process_failed")
        if not self.adapter.profile_verified(profile):
            self._fail(run, step, "verification_failed", "verification_failed")
        self._finish(run, step, "succeeded", "verified", "profile_manifest_verified")

    def _preflight(self, run: _Run, step: SetupStep, profile: str) -> None:
        ref = step.target or ""
        if ref not in SETUP_MODELS or not SETUP_MODELS[ref].gated:
            self._fail(run, step, "preflight_invalid", "unsupported_step")
        if not self.observer.observe().credential_present:
            self._finish(run, step, "pending", "credential_required")
            raise _Wait("credential_required")
        argv = self.adapter.profile_command(
            profile, ["config", "token-status", "--json", "--check-model", ref]
        )
        result = self._child(run, step, argv, profile=profile, capture_status=True)
        access = None
        try:
            payload = json.loads(result.status_line or "")
            if isinstance(payload, dict) and isinstance(payload.get("access"), str):
                access = payload["access"]
        except ValueError:
            access = None
        if result.exit_code == 0 and access == "available":
            self._finish(run, step, "succeeded", "verified", "credential_checked")
        elif access in {"token_missing", "token_invalid"}:
            self._finish(run, step, "pending", "credential_required")
            raise _Wait("credential_required")
        elif access == "agreement_required":
            self._finish(run, step, "pending", "agreement_required")
            raise _Wait("agreement_required")
        elif access == "network_error":
            self._fail(run, step, "network_error", "network_error")
        else:
            self._fail(run, step, "preflight_invalid", "preflight_invalid")

    def _verify_model(self, run: _Run, step: SetupStep, profile: str, ref: str) -> bool:
        argv = self.adapter.profile_command(profile, ["models", "verify", ref])
        return self._child(run, step, argv, profile=profile).exit_code == 0

    def _model(self, run: _Run, step: SetupStep, profile: str) -> None:
        ref = step.target or ""
        if ref not in SETUP_MODELS:
            self._fail(run, step, "process_failed", "unsupported_step")
        # Verify first: an existing complete copy (managed or HF cache) is reused untouched.
        if self._verify_model(run, step, profile, ref):
            self._finish(run, step, "skipped", "validated_reuse", "manager_verified")
            return
        download = self.adapter.profile_command(
            profile, ["models", "download", ref, "--progress-json"]
        )
        if self._child(run, step, download, profile=profile).exit_code != 0:
            self._fail(run, step, "process_failed", "process_failed")
        # Manager resume only; a partial copy is never accepted: verify again after download.
        if not self._verify_model(run, step, profile, ref):
            self._fail(run, step, "verification_failed", "verification_failed")
        self._finish(run, step, "succeeded", "verified", "manager_verified")

    def _smoke(self, run: _Run, step: SetupStep, content: PlanContent) -> None:
        ref = step.target or ""
        model = SETUP_MODELS.get(ref)
        if model is None or model.backend != "faster-whisper":
            self._fail(run, step, "process_failed", "unsupported_step")
            return
        from utteran_gui.setup_wizard import _write_synthetic_wav

        workdir = Path(tempfile.mkdtemp(prefix="utteran-setup-smoke-", dir=self.work_dir))
        try:
            audio, output = workdir / "smoke.wav", workdir / "output"
            _write_synthetic_wav(audio)
            output.mkdir()
            diarization = (
                ["--diarization-backend", "pyannote"]
                if content.diarization_enabled
                else ["--no-diarization"]
            )
            argv = self.adapter.profile_command(
                content.profile_kind,
                [
                    "transcribe",
                    str(audio),
                    "--output-dir",
                    str(output),
                    "--format",
                    "txt",
                    "--asr-backend",
                    model.backend,
                    "--asr-model",
                    model.model_id,
                    *diarization,
                ],
            )
            result = self._child(run, step, argv, profile=content.profile_kind)
            if result.exit_code != 0:
                self._fail(run, step, "process_failed", "process_failed")
            if not any(path.is_file() for path in output.rglob("*.txt")):
                self._fail(run, step, "verification_failed", "verification_failed")
            # Local smoke evidence only; it is *not* promoted to B4 candidate readiness.
            self._finish(run, step, "succeeded", "verified", "smoke_output_present")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _commit(action: Callable[[], object]) -> None:
        for attempt in range(5):
            try:
                action()
                return
            except ControlError as exc:
                if exc.code is ErrorCode.FENCING_TOKEN_STALE:
                    return
                if exc.code not in {ErrorCode.STORE_BUSY, ErrorCode.STORE_UNAVAILABLE}:
                    LOGGER.warning("v1 setup reconciliation refused: %s", exc.code.value)
                    return
            time.sleep(0.05 * 2**attempt)
        LOGGER.warning("v1 setup reconciliation deferred to recovery")
