"""Explicit opt-in v1 job runtime: cutover from and rollback to the legacy queue.

Activation (cutover) requires an idle legacy OperationQueue, which is then closed so the
two systems never dispatch concurrently. The B6/B7 control DB is backed up when it already
existed, recovery runs (dispatch paused, waiting items held, unknown leases kept), and only
then does the dispatcher thread start. Nothing is migrated from the legacy queue.

Deactivation (rollback) stops new dispatch, cooperatively stops the child, and refuses to
reopen the legacy queue while any attempt is active or unverified.
"""

from __future__ import annotations

import contextlib
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from utteran_contracts.control import RecoveryReport
from utteran_control.errors import ControlError
from utteran_control.job_repository import JobControlStore
from utteran_control.recovery import ProcessVerifier
from utteran_control.store import ControlStore
from utteran_gui.job_admission import JobAdmission, ResolutionProvider
from utteran_gui.job_dispatcher import JobDispatcher, WorkerRunner
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.process_verifier import OsProcessVerifier, current_owner


@dataclass(frozen=True)
class SetupComponents:
    """B8 opt-in parts: read-only observer, fixed step adapter and child runner."""

    observer: Any
    adapter: Any
    runner: Any
    work_dir: Path | None = None


class RuntimeStateError(Exception):
    """Fixed reason code only."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class JobRuntime:
    def __init__(
        self,
        *,
        store_path: Path,
        settings_path: Path,
        legacy_queue: OperationQueue,
        provider: ResolutionProvider,
        runner: WorkerRunner,
        job_dir: Path,
        verifier: ProcessVerifier | None = None,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 60,
        poll_interval: float = 0.5,
        autostart_dispatcher: bool = True,
        setup: SetupComponents | None = None,
    ) -> None:
        self.store_path = store_path
        self.settings_path = settings_path
        self.legacy_queue = legacy_queue
        self.provider = provider
        self.runner = runner
        self.job_dir = job_dir
        self.verifier = verifier or OsProcessVerifier()
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.poll_interval = poll_interval
        self.autostart_dispatcher = autostart_dispatcher
        # B8 opt-in: when set, the store opens at layout v4 (one-way; see b8/storage.md).
        self.setup = setup
        self.setup_executor: Any = None
        self.last_compaction: Any = None
        self.state: Literal["inactive", "active"] = "inactive"
        self.store: JobControlStore | None = None
        self.admission: JobAdmission | None = None
        self.dispatcher: JobDispatcher | None = None
        self.last_backup: Path | None = None
        self._lock = threading.Lock()
        # Unpredictable, per-runtime ownership of the legacy queue; never shared.
        self._queue_token: str | None = None

    def _backup_target(self, label: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        return self.store_path.with_name(f"operations-{label}-{stamp}.sqlite3")

    def _backup(self, store: JobControlStore, label: str) -> Path:
        target = self._backup_target(label)
        store.backup(target)
        self.last_backup = target
        return target

    def activate(self) -> RecoveryReport:
        with self._lock:
            if self.state == "active":
                raise RuntimeStateError("already_active")
            if self.legacy_queue.closed:
                raise RuntimeStateError("legacy_queue_owned")  # another runtime owns it
            token = secrets.token_hex(32)
            if not self.legacy_queue.close_for(token):
                code = "legacy_queue_owned" if self.legacy_queue.closed else "legacy_busy"
                raise RuntimeStateError(code)  # pending/active legacy work, or lost the race
            dispatcher: JobDispatcher | None = None
            try:
                existed = self.store_path.exists()
                if existed:
                    # Before construction: opening with a newer class migrates in place
                    # (one way), so this is the only point where the pre-cutover layout
                    # (e.g. v3/v4/v5 before v6) can still be preserved for rollback.
                    target = self._backup_target("pre-v1")
                    ControlStore.backup_before_open(self.store_path, target)
                    self.last_backup = target
                store_type: type[JobControlStore] = JobControlStore
                if self.setup is not None:
                    # B8/B9: ManagedControlStore = layout v6 (v4 setup tables, v5 guarded
                    # outbox compaction, v6 diagnostics attempt sidecar). One-way migration.
                    from utteran_control.events_repository import ManagedControlStore

                    store_type = ManagedControlStore
                store = store_type(self.store_path, clock=self.clock)
                report = store.recover(self.verifier)
                compact = getattr(store, "compact_events", None)
                if compact is not None:
                    # Bounded maintenance at explicit activation only (no background loop).
                    # A busy/readonly/full store is not reported as compacted.
                    with contextlib.suppress(ControlError):
                        self.last_compaction = compact()
                admission = JobAdmission(
                    store=store,
                    settings_path=self.settings_path,
                    provider=self.provider,
                    clock=self.clock,
                )
                executor = None
                if self.setup is not None:
                    from utteran_gui.setup_executor import SetupExecutor

                    executor = SetupExecutor(
                        store,  # type: ignore[arg-type]
                        adapter=self.setup.adapter,
                        observer=self.setup.observer,
                        runner=self.setup.runner,
                        lease_seconds=self.lease_seconds,
                        work_dir=self.setup.work_dir,
                    )
                dispatcher = JobDispatcher(
                    store,
                    prestart=admission.prestart_check,
                    runner=self.runner,
                    owner=current_owner(),
                    job_dir=self.job_dir,
                    lease_seconds=self.lease_seconds,
                    poll_interval=self.poll_interval,
                    setup=executor,
                )
                if self.autostart_dispatcher:
                    dispatcher.start()
            except BaseException:
                if dispatcher is not None:
                    dispatcher.stop(timeout=5)
                self.legacy_queue.reopen(token)  # release only what this call acquired
                raise
            self._queue_token = token
            self.store, self.admission, self.dispatcher = store, admission, dispatcher
            self.setup_executor = executor
            self.state = "active"
            return report

    def deactivate(self, timeout: float = 30.0) -> Path:
        """Rollback to the legacy queue. Refused while an attempt is active/unknown."""
        with self._lock:
            if self.state != "active" or self.store is None or self.dispatcher is None:
                raise RuntimeStateError("not_active")
            stopped = self.dispatcher.stop(timeout)
            if not stopped or self.store.active_job() is not None:
                raise RuntimeStateError("active_attempt")  # slot kept; legacy stays closed
            backup = self._backup(self.store, "rollback")
            if self._queue_token is None or not self.legacy_queue.reopen(self._queue_token):
                raise RuntimeStateError("ownership_lost")  # never reopen a queue we do not own
            self._queue_token = None
            self.state = "inactive"
            self.store = self.admission = self.dispatcher = None
            self.setup_executor = None
            return backup

    def shutdown(self, timeout: float = 30.0) -> None:
        """App shutdown: same as rollback, but an active attempt is left for recovery."""
        with contextlib.suppress(RuntimeStateError):
            self.deactivate(timeout)
