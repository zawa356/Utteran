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
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from utteran_contracts.control import RecoveryReport
from utteran_control.job_repository import JobControlStore
from utteran_control.recovery import ProcessVerifier
from utteran_gui.job_admission import JobAdmission, ResolutionProvider
from utteran_gui.job_dispatcher import JobDispatcher, WorkerRunner
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.process_verifier import OsProcessVerifier, current_owner

OWNER = "utteran-v1-jobs"


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
        self.state: Literal["inactive", "active"] = "inactive"
        self.store: JobControlStore | None = None
        self.admission: JobAdmission | None = None
        self.dispatcher: JobDispatcher | None = None
        self.last_backup: Path | None = None
        self._lock = threading.Lock()

    def _backup(self, store: JobControlStore, label: str) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = self.store_path.with_name(f"operations-{label}-{stamp}.sqlite3")
        store.backup(target)
        self.last_backup = target
        return target

    def activate(self) -> RecoveryReport:
        with self._lock:
            if self.state == "active":
                raise RuntimeStateError("already_active")
            if not self.legacy_queue.close_for(OWNER):
                raise RuntimeStateError("legacy_busy")  # pending/active legacy work exists
            try:
                existed = self.store_path.exists()
                store = JobControlStore(self.store_path, clock=self.clock)
                if existed:
                    self._backup(store, "pre-v1")
                report = store.recover(self.verifier)
            except BaseException:
                self.legacy_queue.reopen(OWNER)
                raise
            owner = current_owner()
            admission = JobAdmission(
                store=store,
                settings_path=self.settings_path,
                provider=self.provider,
                clock=self.clock,
            )
            dispatcher = JobDispatcher(
                store,
                prestart=admission.prestart_check,
                runner=self.runner,
                owner=owner,
                job_dir=self.job_dir,
                lease_seconds=self.lease_seconds,
                poll_interval=self.poll_interval,
            )
            self.store, self.admission, self.dispatcher = store, admission, dispatcher
            self.state = "active"
            if self.autostart_dispatcher:
                dispatcher.start()
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
            self.legacy_queue.reopen(OWNER)
            self.state = "inactive"
            self.store = self.admission = self.dispatcher = None
            return backup

    def shutdown(self, timeout: float = 30.0) -> None:
        """App shutdown: same as rollback, but an active attempt is left for recovery."""
        with contextlib.suppress(RuntimeStateError):
            self.deactivate(timeout)
