"""B7 single-slot dispatcher: frozen snapshot -> B3 worker, fenced and journaled.

Order of evidence for one attempt (each step committed before the next starts):
pre-start revalidation of the frozen snapshot -> ``begin_attempt`` (lease, fencing token,
intent) -> child spawn -> ``record_launch`` (child PID + OS start identity) -> worker run
-> child exit observed -> B3 ``verify_result`` + B7 input re-check -> fenced terminal.
Anything uncertain keeps the slot occupied and is flagged ``recovery_required``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from utteran_contracts.control import OwnerIdentity, ProcessIdentity
from utteran_contracts.engines import get_engine, worker_refusals
from utteran_contracts.jobs import BlockReason, JobErrorCode, JobSnapshot
from utteran_contracts.output_naming import job_output_name
from utteran_contracts.worker import WorkerCompleted, WorkerRequest
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.job_repository import CompletionEvidence, JobControlStore
from utteran_gui.job_admission import AdmissionError, capture_input
from utteran_gui.process_verifier import start_identity

LOGGER = logging.getLogger(__name__)
_WORKER_CODES = frozenset(JobErrorCode.__args__)  # type: ignore[attr-defined]


class PlanError(Exception):
    pass


# Ownership marker of a per-job output directory: one line, the owning job id.
OUTPUT_MARKER = ".utteran-job"


def job_output(snapshot: JobSnapshot, job_id: str) -> tuple[str, str | None]:
    """(output directory, artifact stem) for a job, from frozen values only.

    Snapshots without ``output_stamp`` (before 0.1.54) keep their accepted meaning:
    the user's directory itself and core's own file naming (stem ``None``).
    """
    parent = snapshot.file.output_dir
    if snapshot.output_stamp is None:
        return parent, None
    try:
        name = job_output_name(
            snapshot.input_identity.path, snapshot.output_stamp, job_id, parent=parent
        )
    except ValueError:
        raise PlanError("output_unavailable") from None
    return str(Path(parent) / name), name


def claim_output_dir(directory: Path, job_id: str) -> None:
    """Create the job's own directory, or prove an existing one already belongs to it.

    Never adopts a directory that has content but no matching marker, so the outputs of
    another job (or the user's own files) are not overwritten. A retry of the same job
    finds its marker and continues; partial artifacts stay inside this one directory.
    """
    marker = directory / OUTPUT_MARKER
    try:
        try:
            directory.mkdir()
        except FileExistsError:
            if directory.is_symlink() or not directory.is_dir():
                raise PlanError("output_unavailable") from None
            if marker.is_file() and not marker.is_symlink():
                if marker.read_text(encoding="utf-8").strip() != job_id:
                    raise PlanError("output_unavailable") from None
                return
            if any(directory.iterdir()):
                raise PlanError("output_unavailable") from None
        # New (or empty, e.g. interrupted between mkdir and marker) directory.
        with marker.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(job_id + "\n")
    except OSError:
        raise PlanError("output_unavailable") from None


class WorkerRunner(Protocol):
    def execute(
        self,
        request: WorkerRequest,
        *,
        cancel: threading.Event | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
        on_spawn: Callable[[Any], None] | None = None,
    ) -> WorkerCompleted: ...


def plan_request(
    snapshot: JobSnapshot, *, job_id: str, attempt_id: str, job_dir: Path
) -> WorkerRequest:
    """Map a frozen snapshot to the B3 worker request, or refuse.

    Engine-neutral: what may run is read from the engine declaration of the frozen
    ``spec.backend``; every value of the request comes from the snapshot. Nothing is
    re-selected here and no other engine is substituted.
    """
    spec = snapshot.resolved
    settings = spec.settings
    output_dir, output_stem = job_output(snapshot, job_id)
    try:
        engine = get_engine(spec.backend)
    except ValueError:
        raise PlanError("worker_plan_unsupported") from None
    variant = spec.requested_route.native_variant
    if (
        worker_refusals(engine, settings, recipe_id=spec.recipe_id)
        or spec.worker_support != "supported"
        or spec.device not in engine.devices
        or (variant is not None) != bool(engine.native_variants)
        or (variant is not None and variant not in engine.native_variants)
        or (settings.diarization.enabled and spec.diarization_device is None)
        or not Path(spec.worker_model_dir).is_absolute()
        or not job_dir.is_absolute()
    ):
        raise PlanError("worker_plan_unsupported")
    return WorkerRequest.model_validate(
        {
            "request_id": "req_" + attempt_id[4:],
            "job_id": job_id,
            "attempt_id": attempt_id,
            "profile": spec.profile_id,
            "input_path": snapshot.input_identity.path,
            "settings": settings.to_wire(),
            "context": {
                "base_config": {
                    "general": {
                        "output_dir": output_dir,
                        "job_dir": str(job_dir),
                    }
                },
                "asr_backend": engine.engine,
                # Two meanings from the same frozen snapshot, never one field for both:
                # the catalogue ID B3 checks against the preset, and the local directory
                # B7 approved for that ID, which B3 alone loads (offline, no lookup).
                "asr_model": spec.model_id,
                "asr_model_dir": spec.worker_model_dir,
                "asr_device": variant if variant is not None else spec.device,
                # Unused while diarization is off; the frozen device otherwise.
                "diarization_device": spec.diarization_device or "cpu",
                "compute_type": spec.compute_type,
                # The version frozen with the snapshot, not the current one.
                "prompt_compiler": spec.prompt_compiler_version,
                # Artifacts share the job directory's stem; absent for old snapshots.
                **({} if output_stem is None else {"output_stem": output_stem}),
                **({} if variant is None else {"native_variant": variant}),
            },
            "resume": snapshot.resume_mode != "fresh",
            "force": snapshot.resume_mode == "force",
        }
    )


@dataclass
class _Active:
    job_id: str
    fencing_token: int
    cancel: threading.Event


class JobDispatcher:
    """One heavy slot. Never dispatches while the store says paused / occupied / held."""

    def __init__(
        self,
        store: JobControlStore,
        *,
        prestart: Callable[[JobSnapshot], BlockReason | None],
        runner: WorkerRunner,
        owner: OwnerIdentity,
        job_dir: Path,
        lease_seconds: int = 60,
        poll_interval: float = 0.5,
        identity_of: Callable[[int], str | None] = start_identity,
        setup: Any = None,
    ) -> None:
        self.store = store
        # B8: optional SetupExecutor sharing this thread and the single heavy slot.
        self.setup = setup
        self.prestart = prestart
        self.runner = runner
        self.owner = owner
        self.job_dir = job_dir
        self.lease_seconds = lease_seconds
        self.poll_interval = poll_interval
        self.identity_of = identity_of
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._active: _Active | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="utteran-v1-dispatch", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # never let the loop die silently on a store fault
                LOGGER.warning("v1 dispatcher iteration failed")
                worked = False
            if not worked:
                self._wake.wait(self.poll_interval)
                self._wake.clear()

    def wake(self) -> None:
        self._wake.set()

    def notify_cancel(self, job_id: str) -> None:
        if self.setup is not None:
            self.setup.notify_cancel(job_id)
        with self._lock:
            if self._active is not None and self._active.job_id == job_id:
                self._active.cancel.set()

    @property
    def active_job_id(self) -> str | None:
        with self._lock:
            if self._active is not None:
                return self._active.job_id
        return None if self.setup is None else self.setup.active_operation

    def stop(self, timeout: float = 30.0) -> bool:
        """Shutdown intent -> no new dispatch -> cooperative cancel -> wait for exit.

        Returns False when the attempt did not finish in time; its slot stays occupied
        and the next explicit v1 start resolves it through recovery.
        """
        self._stop.set()
        self._wake.set()
        with self._lock:
            active = self._active
        if active is not None:
            self._request_cancel(active)
        if self.setup is not None:
            self.setup.request_cancel()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                return False
            self._thread = None
        return self.active_job_id is None

    def _request_cancel(self, active: _Active) -> None:
        try:
            view = self.store.get(active.job_id)
            if view.status == "running":
                self.store.transition(
                    active.job_id,
                    {
                        "schema_version": 1,
                        "expected_revision": view.revision,
                        "to_status": "cancelling",
                        "reason_code": "cancel_accepted",
                    },
                )
        except ControlError:
            pass  # cancellation is still requested from the child below
        active.cancel.set()

    # ------------------------------------------------------------------ one dispatch

    def run_once(self) -> bool:
        """Dispatch (and run to completion) at most one job. False when nothing to do."""
        if self._stop.is_set():
            return False
        candidate = self.store.next_dispatchable()
        if candidate is None:
            return False
        job_id, revision = candidate
        if self.setup is not None and job_id.startswith("setup_"):
            return bool(self.setup.dispatch(job_id, revision, self.owner))
        snapshot = self.store.get_snapshot(job_id)
        reason = self.prestart(snapshot)
        if reason is None:
            try:
                plan_request(
                    snapshot, job_id=job_id, attempt_id="att_" + "0" * 32, job_dir=self.job_dir
                )
                directory, stem = job_output(snapshot, job_id)
                if stem is not None:
                    claim_output_dir(Path(directory), job_id)
            except PlanError as exc:
                reason = (
                    "output_unavailable"
                    if str(exc) == "output_unavailable"
                    else "worker_plan_unsupported"
                )
            except ValueError:
                reason = "worker_plan_unsupported"
        if reason is not None:
            with suppress(ControlError):  # changed concurrently: re-evaluated next round
                self.store.block_job(job_id, revision, reason)
            return True
        try:
            grant = self.store.begin_attempt(
                job_id, revision, self.owner, lease_seconds=self.lease_seconds
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
        active = _Active(job_id, grant.fencing_token, threading.Event())
        with self._lock:
            self._active = active
        try:
            self._run(active, snapshot, grant.attempt_id)
        finally:
            with self._lock:
                self._active = None
        return True

    def _heartbeat(self, active: _Active, done: threading.Event, lost: threading.Event) -> None:
        interval = max(self.lease_seconds / 3, 0.05)
        while not done.wait(interval):
            try:
                self.store.renew_lease(
                    active.job_id, active.fencing_token, lease_seconds=self.lease_seconds
                )
                if self.store.get(active.job_id).status == "cancelling":
                    active.cancel.set()
            except ControlError as exc:
                if exc.code is ErrorCode.FENCING_TOKEN_STALE:
                    lost.set()  # another owner: stop and never write terminal state
                    active.cancel.set()
                    return

    def _run(self, active: _Active, snapshot: JobSnapshot, attempt_id: str) -> None:
        job_id, fence = active.job_id, active.fencing_token
        spawned: list[Any] = []

        def on_spawn(process: Any) -> None:
            spawned.append(process)
            child = ProcessIdentity(pid=process.pid, start_identity=self.identity_of(process.pid))
            self.store.record_launch(job_id, fence, outcome="confirmed", child=child)

        done, lost = threading.Event(), threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(active, done, lost), daemon=True)
        beat.start()
        completed: WorkerCompleted | None = None
        error: JobErrorCode | None = None
        try:
            request = plan_request(
                snapshot, job_id=job_id, attempt_id=attempt_id, job_dir=self.job_dir
            )
            completed = self.runner.execute(request, cancel=active.cancel, on_spawn=on_spawn)
        except PlanError:
            error = "worker_plan_unsupported"
        except Exception as exc:
            code = getattr(exc, "code", None)
            value = getattr(code, "value", None)
            error = value if value in _WORKER_CODES else "worker_crash"
        finally:
            done.set()
            beat.join(timeout=5)
        if lost.is_set():
            return
        if spawned and spawned[0].poll() is None:
            # The child may still run: never release the slot on an unobserved exit.
            self._commit(lambda: self.store.flag_unconfirmed(job_id, fence))
            return
        if not spawned:
            self._commit(
                lambda: self.store.record_launch(job_id, fence, outcome="failed"),
                tolerate={ErrorCode.INVALID_TRANSITION},
            )
        if completed is not None:
            try:
                identity = capture_input(snapshot.input_identity.path)
            except AdmissionError:
                identity = None
            if identity != snapshot.input_identity:
                completed, error = None, "input_changed"
        if completed is not None:
            evidence = CompletionEvidence(
                core_job_id=completed.core_job_id,
                artifact_digests=tuple(item.sha256 for item in completed.artifacts),
                manifest_digest=completed.manifest.sha256,
            )
            self._commit(
                lambda: self.store.finish_attempt(job_id, fence, "succeeded", evidence=evidence)
            )
        elif error == "cancelled":
            self._commit(lambda: self.store.finish_attempt(job_id, fence, "cancelled"))
        else:
            self._commit(lambda: self.store.finish_attempt(job_id, fence, "failed", error=error))

    @staticmethod
    def _commit(
        action: Callable[[], object], tolerate: frozenset[ErrorCode] | set[ErrorCode] = frozenset()
    ) -> None:
        """Retry transient store faults; leave the lease for recovery when it stays down."""
        for attempt in range(5):
            try:
                action()
                return
            except ControlError as exc:
                if exc.code in tolerate:
                    return
                if exc.code is ErrorCode.FENCING_TOKEN_STALE:
                    return
                if exc.code not in {ErrorCode.STORE_BUSY, ErrorCode.STORE_UNAVAILABLE}:
                    LOGGER.warning("v1 terminal reconciliation refused: %s", exc.code.value)
                    return
            time.sleep(0.05 * 2**attempt)
        LOGGER.warning("v1 terminal reconciliation deferred to recovery")
