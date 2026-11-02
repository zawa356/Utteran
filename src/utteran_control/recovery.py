"""Conservative crash-recovery decisions. Pure functions plus an injectable verifier.

B6 never kills, signals or spawns processes. A verifier only *observes*; anything it
cannot prove becomes ``recovery_required`` so that no second worker is started.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from utteran_contracts.control import LaunchState, ProcessIdentity, ReasonCode


class Liveness(StrEnum):
    ALIVE = "alive"  # the exact recorded process identity is still running
    GONE = "gone"  # verified absent (PID absent, or creation identity proves it ended)
    PID_REUSED = "pid_reused"  # PID exists but belongs to a different process
    UNKNOWN = "unknown"  # cannot be observed on this platform / permission denied


class ProcessVerifier(Protocol):
    """Observe one recorded identity. Must not mutate, signal or terminate processes."""

    def check(self, process: ProcessIdentity) -> Liveness: ...


class UnknownVerifier:
    """Default: nothing can be verified, so every active record needs explicit review."""

    def check(self, process: ProcessIdentity) -> Liveness:
        return Liveness.UNKNOWN


class Outcome(StrEnum):
    OWNER_ALIVE = "owner_alive"
    INTERRUPTED = "interrupted"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: ReasonCode | None


def safe_check(verifier: ProcessVerifier, process: ProcessIdentity) -> Liveness:
    """Verifier failures are UNKNOWN; their exception text is never forwarded."""
    try:
        result = verifier.check(process)
    except Exception:
        return Liveness.UNKNOWN
    return result if isinstance(result, Liveness) else Liveness.UNKNOWN


def decide(
    owner: Liveness,
    launch: LaunchState,
    child: Liveness | None,
) -> Decision:
    """Map observations to a conservative outcome.

    ``child`` is the observation of the recorded child process, or ``None`` when no
    child identity was recorded. ``launch == "failed"`` is positive evidence that no
    child exists; ``launch == "intended"`` without evidence means a child may exist.
    """
    if owner is Liveness.ALIVE:
        return Decision(Outcome.OWNER_ALIVE, None)
    if owner is Liveness.PID_REUSED:
        return Decision(Outcome.RECOVERY_REQUIRED, ReasonCode.RECOVERY_PID_REUSED)
    if owner is not Liveness.GONE:
        return Decision(Outcome.RECOVERY_REQUIRED, ReasonCode.RECOVERY_OWNER_UNVERIFIED)
    if launch == "failed":
        return Decision(Outcome.INTERRUPTED, ReasonCode.OWNER_LOST)
    if launch == "intended" or child is None:
        return Decision(Outcome.RECOVERY_REQUIRED, ReasonCode.RECOVERY_LAUNCH_UNCONFIRMED)
    if child is Liveness.GONE:
        return Decision(Outcome.INTERRUPTED, ReasonCode.OWNER_LOST)
    if child is Liveness.ALIVE:
        return Decision(Outcome.RECOVERY_REQUIRED, ReasonCode.RECOVERY_CHILD_ALIVE)
    if child is Liveness.PID_REUSED:
        return Decision(Outcome.RECOVERY_REQUIRED, ReasonCode.RECOVERY_PID_REUSED)
    return Decision(Outcome.RECOVERY_REQUIRED, ReasonCode.RECOVERY_OWNER_UNVERIFIED)
