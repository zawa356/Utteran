"""B9 public sync / diagnostics contracts and the pure client-side event reducer.

Pure DTOs and a pure reducer only: nothing here opens a database, a socket or a file.
Event envelopes reuse the B6 ``EventEnvelope`` (allowlisted columns of committed outbox
rows); this module adds the ``/sync`` snapshot, the ``resync_required`` control message and
sanitized diagnostic entries, which carry fixed codes only (no log text, path or argv).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictStr

from utteran_contracts.base import Contract, VersionedContract
from utteran_contracts.control import (
    Cursor,
    EventEnvelope,
    OperationId,
    OperationKind,
    QueueView,
    Revision,
    Status,
)

SNAPSHOT_TOKEN_PATTERN = r"^[0-9a-f]{32}$"
SnapshotToken = Annotated[StrictStr, Field(pattern=SNAPSHOT_TOKEN_PATTERN)]
DiagnosticCode = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]


class SyncResource(Contract):
    id: OperationId
    kind: OperationKind
    revision: Revision
    status: Status


class SyncSnapshot(VersionedContract):
    """Resources, queue, inventory revision and event watermark from ONE read transaction.

    ``inventory_revision`` is the B8 setup observation counter, or ``null`` when no setup
    observation exists (it is never a B4 inventory revision and never invented).
    """

    snapshot_token: SnapshotToken
    event_cursor: Cursor
    resources: tuple[SyncResource, ...]
    next_cursor: Annotated[StrictStr, Field(max_length=200)] | None
    queue: QueueView
    inventory_revision: Revision | None


class ResyncRequired(VersionedContract):
    """SSE control message (sent without an ``id:``) before the server closes the stream."""

    type: Literal["resync_required"]
    reason_code: Literal["event_cursor_expired", "stream_limit_reached"]


class DiagnosticEntry(Contract):
    timestamp: AwareDatetime
    code: DiagnosticCode
    message_key: Annotated[StrictStr, Field(pattern=r"^diagnostics\.[a-z][a-z0-9_]{0,63}$")]
    stage: Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")] | None
    attempt_id: Annotated[StrictStr, Field(pattern=r"^att_[0-9a-f]{32}$")] | None


class DiagnosticEntryList(VersionedContract):
    revision: Revision
    items: tuple[DiagnosticEntry, ...]
    # ``d2.*`` HMAC-bound snapshot cursor (PR #33 review fix); same bound as other lists.
    next_cursor: Annotated[StrictStr, Field(max_length=200)] | None


PUBLIC_MODELS: tuple[type[Contract], ...] = (
    SyncResource,
    SyncSnapshot,
    ResyncRequired,
    DiagnosticEntry,
    DiagnosticEntryList,
    EventEnvelope,
)


# --------------------------------------------------------------------------- reducer

Outcome = Literal["applied", "duplicate", "stale", "resync"]


def _seq(cursor: str) -> tuple[str, int]:
    epoch, _, seq = cursor.partition(":")
    return epoch, int(seq)


@dataclass
class EventReducer:
    """Client reducer for ``GET /sync`` + SSE, independent of arrival order.

    Rules (Phase A §6): apply sync first, then events after its cursor; duplicate IDs and
    events at or below the known revision are ignored; a seq gap is *not* an error (resource
    filters skip rows); an event from another epoch, or a GET older than what is known, is
    discarded / answered with ``resync``. Reconnecting never starts or retries work.
    """

    epoch: str | None = None
    cursor: int = 0
    revisions: dict[str, int] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    queue_revision: int = 0
    seen: set[int] = field(default_factory=set)

    def apply_sync(self, snapshot: SyncSnapshot) -> None:
        epoch, seq = _seq(snapshot.event_cursor)
        if self.epoch != epoch:
            self.epoch, self.cursor, self.seen = epoch, 0, set()
            self.revisions, self.statuses = {}, {}
        self.cursor = max(self.cursor, seq)
        for resource in snapshot.resources:
            self.apply_resource(resource.id, resource.revision, resource.status.value)
        self.queue_revision = max(self.queue_revision, snapshot.queue.revision)

    def apply_resource(self, resource_id: str, revision: int, status: str) -> Outcome:
        """A resource GET (or sync row). Older than known -> stale, never rolls back."""
        if revision <= self.revisions.get(resource_id, 0):
            return "stale"
        self.revisions[resource_id] = revision
        self.statuses[resource_id] = status
        return "applied"

    def apply_event(self, event: EventEnvelope) -> Outcome:
        epoch, seq = _seq(event.id)
        if self.epoch is None or epoch != self.epoch:
            return "resync"
        if seq in self.seen or seq <= self.cursor:
            return "duplicate"
        self.seen.add(seq)
        self.cursor = seq
        if event.resource_kind == "queue":
            if event.resource_revision <= self.queue_revision:
                return "stale"
            self.queue_revision = event.resource_revision
            return "applied"
        status = event.payload.status or self.statuses.get(event.resource_id, "")
        return self.apply_resource(event.resource_id, event.resource_revision, status)
