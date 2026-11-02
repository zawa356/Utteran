"""Kind-specific state tables derived from docs/architecture/phase-a/lifecycles.md.

Generic status overwrite is impossible: every change goes through ``check``. Entering
``running`` is only possible through ``ControlStore.begin_attempt`` (lease + journal).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from utteran_contracts.control import OperationKind
from utteran_contracts.control import OperationStatus as S
from utteran_control.errors import ControlError, ErrorCode

TERMINAL: frozenset[S] = frozenset({S.SUCCEEDED, S.FAILED, S.CANCELLED})
# States with an active lease / occupied slot.
ACTIVE: frozenset[S] = frozenset({S.RUNNING, S.CANCELLING})
# States that keep a waiting queue item.
WAITING: frozenset[S] = frozenset({S.QUEUED, S.HELD, S.BLOCKED})

_Table = Mapping[S, frozenset[S]]


def _table(rows: dict[S, set[S]]) -> _Table:
    return MappingProxyType({key: frozenset(value) for key, value in rows.items()})


TRANSITIONS: Mapping[str, _Table] = MappingProxyType(
    {
        "job": _table(
            {
                S.QUEUED: {S.HELD, S.BLOCKED, S.CANCELLED, S.RUNNING},
                S.HELD: {S.QUEUED, S.CANCELLED},
                S.BLOCKED: {S.QUEUED, S.HELD, S.CANCELLED},
                S.RUNNING: {S.CANCELLING, S.SUCCEEDED, S.FAILED, S.INTERRUPTED},
                S.CANCELLING: {S.CANCELLED, S.FAILED, S.INTERRUPTED},
                # Job interrupted is final for this resource; retry is a new job (B7).
            }
        ),
        "setup_operation": _table(
            {
                S.QUEUED: {S.CANCELLED, S.RUNNING},
                S.RUNNING: {
                    S.WAITING_USER,
                    S.CANCELLING,
                    S.SUCCEEDED,
                    S.FAILED,
                    S.INTERRUPTED,
                },
                # B8: a waiting/interrupted setup can also be discarded (cancel) explicitly.
                S.WAITING_USER: {S.QUEUED, S.CANCELLED},
                S.CANCELLING: {S.CANCELLED, S.INTERRUPTED},
                # Explicit resume appends a new attempt later; never back to running.
                S.INTERRUPTED: {S.QUEUED, S.CANCELLED},
            }
        ),
        "refresh_operation": _table(
            {
                S.QUEUED: {S.CANCELLED, S.RUNNING},
                S.RUNNING: {S.CANCELLING, S.SUCCEEDED, S.FAILED, S.INTERRUPTED},
                S.CANCELLING: {S.CANCELLED, S.INTERRUPTED},
            }
        ),
    }
)

# Kinds that occupy the single resource-heavy queue slot.
QUEUED_KINDS: frozenset[str] = frozenset({"job", "setup_operation"})
SLOT: Mapping[str, str] = MappingProxyType(
    {"job": "heavy", "setup_operation": "heavy", "refresh_operation": "refresh"}
)
ALL_STATUSES: frozenset[S] = frozenset(S)


def allowed(kind: OperationKind | str, current: S) -> frozenset[S]:
    return TRANSITIONS[kind].get(current, frozenset())


def check(kind: OperationKind | str, current: S, target: S) -> None:
    if target not in allowed(kind, current):
        raise ControlError(ErrorCode.INVALID_TRANSITION)


def is_final(kind: OperationKind | str, status: S) -> bool:
    return not allowed(kind, status)
