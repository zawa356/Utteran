"""B6 durable control-store contracts: public projections, requests and private records.

Pure DTOs only. Nothing here opens a database, inspects a process or dispatches work.
Public projections (``*View``, ``EventEnvelope``, ``ControlSnapshot``, ``RecoveryReport``)
are built field by field from typed columns; private payloads and owner nonces live only
in ``Private*`` / identity types that are never part of an event or snapshot.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
)

from utteran_contracts.base import Contract, VersionedContract

OperationKind = Literal["job", "setup_operation", "refresh_operation"]
QueueResourceKind = Literal["job", "setup_operation"]
EventResourceKind = Literal["job", "setup_operation", "refresh_operation", "queue"]
EventType = Literal["state_changed", "queue_changed"]
DispatchState = Literal["running", "paused"]
LaunchState = Literal["intended", "confirmed", "failed"]
AttemptState = Literal["active", "released", "interrupted"]

OPERATION_ID_PATTERN = r"^(job|setup|refresh)_[0-9a-f]{32}$"
ATTEMPT_ID_PATTERN = r"^att_[0-9a-f]{32}$"
OWNER_ID_PATTERN = r"^own_[0-9a-f]{32}$"
QUEUE_ITEM_ID_PATTERN = r"^qi_[0-9a-f]{32}$"
CURSOR_PATTERN = r"^[0-9a-f]{32}:(0|[1-9][0-9]{0,18})$"

OperationId = Annotated[StrictStr, Field(pattern=OPERATION_ID_PATTERN)]
AttemptId = Annotated[StrictStr, Field(pattern=ATTEMPT_ID_PATTERN)]
OwnerId = Annotated[StrictStr, Field(pattern=OWNER_ID_PATTERN)]
QueueItemId = Annotated[StrictStr, Field(pattern=QUEUE_ITEM_ID_PATTERN)]
Cursor = Annotated[StrictStr, Field(pattern=CURSOR_PATTERN, max_length=52)]
Revision = Annotated[StrictInt, Field(ge=1)]
Count = Annotated[StrictInt, Field(ge=0)]


class OperationStatus(StrEnum):
    QUEUED = "queued"
    HELD = "held"
    BLOCKED = "blocked"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ReasonCode(StrEnum):
    """Fixed reason vocabulary. Free-form caller text is never stored or echoed."""

    SUBMITTED = "submitted"
    USER_HOLD = "user_hold"
    USER_RELEASE = "user_release"
    USER_REORDER = "user_reorder"
    USER_CANCEL = "user_cancel"
    CANCEL_ACCEPTED = "cancel_accepted"
    PRESTART_REVALIDATION_FAILED = "prestart_revalidation_failed"
    RESOURCES_AVAILABLE = "resources_available"
    DISPATCH_STARTED = "dispatch_started"
    LAUNCH_CONFIRMED = "launch_confirmed"
    LAUNCH_FAILED = "launch_failed"
    COMPLETION_VERIFIED = "completion_verified"
    CLASSIFIED_FAILURE = "classified_failure"
    TERMINATION_FAILED = "termination_failed"
    EXIT_RECONCILED = "exit_reconciled"
    CREDENTIAL_REQUIRED = "credential_required"
    AGREEMENT_REQUIRED = "agreement_required"
    EXPLICIT_RESUME = "explicit_resume"
    OWNER_LOST = "owner_lost"
    RECOVERY_HELD = "recovery_held"
    RECOVERY_PAUSED = "recovery_paused"
    RECOVERY_OWNER_UNVERIFIED = "recovery_owner_unverified"
    RECOVERY_PID_REUSED = "recovery_pid_reused"
    RECOVERY_LAUNCH_UNCONFIRMED = "recovery_launch_unconfirmed"
    RECOVERY_CHILD_ALIVE = "recovery_child_alive"
    QUEUE_PAUSED = "queue_paused"
    QUEUE_RESUMED = "queue_resumed"
    # B8 setup operations
    STEP_PROGRESS = "step_progress"
    REAPPROVAL_REQUIRED = "reapproval_required"


Status = Annotated[OperationStatus, Field(strict=False)]
EventStatus = Literal[
    "queued",
    "held",
    "blocked",
    "running",
    "waiting_user",
    "cancelling",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "paused",
]
Reason = Annotated[ReasonCode, Field(strict=False)]


def _utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("timestamps must be UTC")
    return value


UtcTimestamp = Annotated[AwareDatetime, Field()]


class _Timestamped(Contract):
    @field_validator("*", mode="after")
    @classmethod
    def require_utc(cls, value: Any) -> Any:
        return _utc(value) if isinstance(value, datetime) else value


# --------------------------------------------------------------------------- requests


class SubmitOperation(VersionedContract):
    """Accept one operation record. ``private_payload`` is opaque and never published."""

    kind: OperationKind
    private_payload: dict[StrictStr, JsonValue]


class TransitionRequest(VersionedContract):
    expected_revision: Revision
    to_status: Status
    reason_code: Reason


class ProcessIdentity(Contract):
    """OS process identity; PID alone is never trusted as proof of ownership."""

    pid: Annotated[StrictInt, Field(ge=1, le=2**31 - 1)]
    start_identity: Annotated[StrictStr, Field(min_length=1, max_length=128)] | None


class OwnerIdentity(Contract):
    """Private owner identity. ``nonce`` must not be logged or published."""

    owner_id: OwnerId
    nonce: Annotated[StrictStr, Field(min_length=32, max_length=128, pattern=r"^[0-9a-f]+$")]
    process: ProcessIdentity


# --------------------------------------------------------------------------- public views


class AttemptView(_Timestamped):
    attempt_id: AttemptId
    number: Revision
    owner_id: OwnerId
    state: AttemptState
    launch: LaunchState
    started_at: UtcTimestamp
    ended_at: UtcTimestamp | None


class OperationView(VersionedContract, _Timestamped):
    """Public metadata only. Not a complete B7 Job or B8 SetupOperation resource."""

    id: OperationId
    kind: OperationKind
    status: Status
    revision: Revision
    held: StrictBool
    blocked: StrictBool
    blockers: tuple[Reason, ...]
    recovery_required: Reason | None
    reason_code: Reason
    attempt: AttemptView | None
    created_at: UtcTimestamp
    updated_at: UtcTimestamp


class QueueItemView(Contract):
    id: QueueItemId
    resource_id: OperationId
    resource_kind: QueueResourceKind
    position: Annotated[StrictInt, Field(ge=1)] | None
    held: StrictBool
    blockers: tuple[Reason, ...]


class QueueView(VersionedContract):
    revision: Revision
    dispatch: DispatchState
    active_item_id: QueueItemId | None
    items: tuple[QueueItemView, ...]


class EventPayload(Contract):
    # Operation status for resource events, dispatch state ("running"/"paused") for queue.
    status: EventStatus | None
    reason_code: Reason | None
    attempt_id: AttemptId | None


class EventEnvelope(VersionedContract, _Timestamped):
    id: Cursor
    resource_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    resource_kind: EventResourceKind
    resource_revision: Revision
    timestamp: UtcTimestamp
    type: EventType
    payload: EventPayload


class EventPage(VersionedContract):
    events: tuple[EventEnvelope, ...]
    next_cursor: Cursor
    has_more: StrictBool


class ControlSnapshot(VersionedContract):
    """Resources, queue and the outbox watermark read in one SQLite read transaction."""

    event_cursor: Cursor
    operations_revision: Revision
    operations: tuple[OperationView, ...]
    queue: QueueView


class RecoveryReport(VersionedContract):
    queue_paused: StrictBool
    held: tuple[OperationId, ...]
    interrupted: tuple[OperationId, ...]
    recovery_required: tuple[OperationId, ...]
    owner_alive: tuple[OperationId, ...]
    changed: StrictBool
    event_cursor: Cursor


# --------------------------------------------------------------------------- private


class LeaseGrant(Contract):
    """Returned only to the caller that committed the attempt intent."""

    operation: OperationView
    attempt_id: AttemptId
    fencing_token: Annotated[StrictInt, Field(ge=1)]


class PrivateOperation(VersionedContract):
    """Private record: never part of an event, snapshot, schema example or log line."""

    id: OperationId
    kind: OperationKind
    private_payload: dict[StrictStr, JsonValue]


PUBLIC_MODELS: tuple[type[Contract], ...] = (
    SubmitOperation,
    TransitionRequest,
    AttemptView,
    OperationView,
    QueueItemView,
    QueueView,
    EventPayload,
    EventEnvelope,
    EventPage,
    ControlSnapshot,
    RecoveryReport,
)
