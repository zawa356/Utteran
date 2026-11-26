"""Process-local exclusion between heavy execution and runtime probes (束F, PR #91 P2-3).

The B7/B8 dispatcher holds the gate for the whole of one dispatch (a job or a setup,
including its children); the setup diagnostics hold it for the whole runtime probe
(``utteran devices`` children in a profile venv). Both sides only *try* to acquire, so the
check and the start are one atomic step: a probe never starts while a setup/job runs, and a
queued setup/job is not dispatched while a probe runs (the dispatcher retries next round).
"""

from __future__ import annotations

import threading


class ExecutionGate:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None

    def try_acquire(self, holder: str) -> bool:
        with self._lock:
            if self._holder is not None:
                return False
            self._holder = holder
            return True

    def release(self, holder: str) -> None:
        with self._lock:
            if self._holder == holder:
                self._holder = None

    @property
    def holder(self) -> str | None:
        with self._lock:
            return self._holder
