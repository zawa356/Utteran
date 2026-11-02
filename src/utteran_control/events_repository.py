"""B9: consistent sync snapshots, sanitized diagnostics and bounded outbox compaction.

``read_sync`` / ``SyncService`` work on any B7+ store (layout v3 or later) and read the
resources, the queue, the setup observation counter and the outbox watermark in ONE SQLite
read transaction. ``ManagedControlStore`` (layout v5 = v4 + a guarded outbox delete) is the
only class that can compact events; B6/B7/B8 classes refuse a v5 file.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar, Final, cast

from pydantic import ValidationError

from utteran_contracts.control import QueueView
from utteran_contracts.events import (
    DiagnosticEntry,
    DiagnosticEntryList,
    SyncResource,
    SyncSnapshot,
)
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.job_repository import (
    LIST_CAPACITY,
    LIST_TTL,
    JobControlStore,
    _ListSnapshot,
    _ListSnapshots,
)
from utteran_control.setup_repository import REQUIRED_TABLES_V4, SetupControlStore
from utteran_control.store import ControlStore, _ts

E = ErrorCode

EVENTS_SCHEMA_VERSION: Final = 5
RETENTION_SECONDS: Final = 7 * 24 * 3600
KEEP_LATEST: Final = 100_000
MAX_DELETE: Final = 50_000
SYNC_PAGE: Final = 500
SYNC_MAX_ITEMS: Final = 50_000
DIAGNOSTIC_PAGE: Final = 200
DIAGNOSTIC_MAX: Final = 20_000
_SYNC_CURSOR = re.compile(
    r"y1\.([0-9a-f]{32})\.([1-9][0-9]{0,8})\.([1-9][0-9]{0,2})\.([0-9a-f]{64})"
)
_DIAG_CURSOR = re.compile(r"d1\.([1-9][0-9]{0,8})\.([1-9][0-9]{0,2})")
# Final states per kind: their events may be compacted after the retention window.
_FINAL = {
    "job": ("succeeded", "failed", "cancelled", "interrupted"),
    "setup_operation": ("succeeded", "failed", "cancelled"),
    "refresh_operation": ("succeeded", "failed", "cancelled", "interrupted"),
}

_DDL_V5: Final = (
    "CREATE TABLE outbox_compaction (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
    "active INTEGER NOT NULL CHECK (active IN (0,1)), floor_seq INTEGER NOT NULL "
    "CHECK (floor_seq >= 0), compacted_at TEXT)",
    "INSERT INTO outbox_compaction VALUES (1,0,0,NULL)",
    # Outbox rows stay immutable; DELETE is possible only inside a compaction transaction.
    "DROP TRIGGER outbox_immutable_DELETE",
    "CREATE TRIGGER outbox_guarded_delete BEFORE DELETE ON outbox "
    "WHEN (SELECT active FROM outbox_compaction) = 0 "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
)
REQUIRED_TABLES_V5: Final = REQUIRED_TABLES_V4 | {"outbox_compaction"}


@dataclass(frozen=True)
class CompactionReport:
    deleted: int
    kept_protected: int
    floor_cursor: str


class ManagedControlStore(SetupControlStore):
    """Layout v5: B8 setup store plus bounded, crash-safe outbox compaction."""

    TARGET_VERSION: ClassVar[int] = EVENTS_SCHEMA_VERSION

    @classmethod
    def _layout(cls, version: int) -> tuple[tuple[str, ...], frozenset[str]]:
        if version == EVENTS_SCHEMA_VERSION:
            return _DDL_V5, REQUIRED_TABLES_V5
        return super()._layout(version)

    def compact_events(
        self,
        *,
        retention_seconds: int = RETENTION_SECONDS,
        keep_latest: int = KEEP_LATEST,
        max_delete: int = MAX_DELETE,
    ) -> CompactionReport:
        """Delete a bounded, old prefix of the outbox and raise the cursor floor atomically.

        Only rows older than ``retention_seconds`` (>= 7 days), outside the newest
        ``keep_latest`` rows, and never rows of a resource that is not final or still needs
        recovery are deleted. The epoch floor is raised in the same transaction, so a
        cursor below it is ``event_cursor_expired`` (never silently served newer data).
        A crash rolls back the whole step; calling it again is safe.
        """
        if retention_seconds < RETENTION_SECONDS or keep_latest < 0 or not max_delete >= 1:
            raise ControlError(E.REQUEST_INVALID)
        with self._transaction() as db:
            epoch, floor = self._epoch(db)
            high = int(
                db.execute("SELECT value FROM counters WHERE name = 'outbox_seq'").fetchone()[0]
            )
            cutoff = _ts(self._clock() - timedelta(seconds=retention_seconds))
            young = db.execute("SELECT min(seq) FROM outbox WHERE recorded_at >= ?", (cutoff,))
            first_young = young.fetchone()[0]
            limit = min(
                high - keep_latest,
                floor + max_delete,
                (first_young - 1) if first_young is not None else high,
            )
            if limit <= floor:
                return CompactionReport(0, 0, f"{epoch}:{floor}")
            protected = [
                row[0]
                for row in db.execute(
                    "SELECT id, kind, status, recovery_required FROM operations"
                ).fetchall()
                if row[3] is not None or row[2] not in _FINAL.get(row[1], ())
            ]
            marks = ",".join("?" * len(protected))
            keep_clause = f"AND resource_id NOT IN ({marks})" if protected else ""
            kept = db.execute(
                f"SELECT count(*) FROM outbox WHERE seq > ? AND seq <= ? "
                f"AND resource_id IN ({marks or 'NULL'})",
                (floor, limit, *protected),
            ).fetchone()[0]
            db.execute("UPDATE outbox_compaction SET active = 1")
            deleted = db.execute(
                f"DELETE FROM outbox WHERE seq > ? AND seq <= ? {keep_clause}",
                (floor, limit, *protected),
            ).rowcount
            db.execute(
                "UPDATE outbox_compaction SET active = 0, floor_seq = ?, compacted_at = ?",
                (limit, self._now()),
            )
            db.execute("UPDATE installation SET epoch_floor = ?", (limit,))
            if self._fault is not None:
                self._fault("compaction_before_commit")
            return CompactionReport(int(deleted), int(kept), f"{epoch}:{limit}")


# --------------------------------------------------------------------------- sync


@dataclass(frozen=True)
class _SyncSnapshot(_ListSnapshot):
    cursor: str = ""
    queue: QueueView | None = None
    inventory: int | None = None


def read_sync(store: ControlStore) -> tuple[str, tuple[SyncResource, ...], QueueView, int | None]:
    """Resources, queue, inventory counter and watermark from one read transaction."""
    with store._transaction(write=False) as db:
        epoch, _, high = store._parse_cursor(db, None)
        total = db.execute("SELECT count(*) FROM operations").fetchone()[0]
        if total > SYNC_MAX_ITEMS:
            raise ControlError(E.LIST_SNAPSHOT_TOO_LARGE)
        rows = db.execute(
            "SELECT id, kind, revision, status FROM operations ORDER BY rowid"
        ).fetchall()
        queue = store._queue_view(db)
        inventory = None
        if "setup_inventory" in store._tables(db):
            row = db.execute("SELECT revision FROM setup_inventory").fetchone()
            inventory = None if row is None else int(row[0])
        try:
            resources = tuple(
                SyncResource(id=r[0], kind=r[1], revision=r[2], status=r[3]) for r in rows
            )
        except ValidationError:
            raise ControlError(E.STORE_CORRUPT) from None
        return f"{epoch}:{high}", resources, queue, inventory


class SyncService:
    """Point-in-time ``GET /sync`` paging (process-local snapshots, TTL/capacity bounded).

    Every page of one snapshot repeats the same ``snapshot_token``, ``event_cursor``,
    queue and inventory revision; cursors are HMAC-bound to snapshot, position, page size
    and the viewer. Lost on restart: 409 ``cursor_expired``, start a new sync.
    """

    def __init__(
        self,
        store: JobControlStore,
        *,
        clock: Any = None,
        ttl: float = LIST_TTL,
        capacity: int = LIST_CAPACITY,
    ) -> None:
        self.store = store
        self._snapshots = _ListSnapshots(
            clock=clock,
            ttl=ttl,
            capacity=capacity,
            max_items=SYNC_MAX_ITEMS,
            total_items=SYNC_MAX_ITEMS * 2,
        )

    def _mac(self, token: str, offset: int, limit: int, scope: str) -> str:
        message = f"sync:v1:{token}:{offset}:{limit}:{scope}".encode()
        return hmac.new(self.store._secret, message, hashlib.sha256).hexdigest()

    def _page(self, snap: _SyncSnapshot, offset: int, limit: int, scope: str) -> SyncSnapshot:
        end = offset + limit
        next_cursor = None
        if end < len(snap.items):
            next_cursor = f"y1.{snap.id}.{end}.{limit}.{self._mac(snap.id, end, limit, scope)}"
        assert snap.queue is not None
        return SyncSnapshot(
            schema_version=1,
            snapshot_token=snap.id,
            event_cursor=snap.cursor,
            resources=snap.items[offset:end],
            next_cursor=next_cursor,
            queue=snap.queue,
            inventory_revision=snap.inventory,
        )

    def page(
        self,
        cursor: str | None = None,
        *,
        snapshot_token: str | None = None,
        limit: int = SYNC_PAGE,
        scope: str = "",
    ) -> SyncSnapshot:
        if type(limit) is not int or not 1 <= limit <= SYNC_PAGE:
            raise ControlError(E.REQUEST_INVALID)
        if cursor is None:
            if snapshot_token is not None:
                raise ControlError(E.CURSOR_INVALID)  # a token only continues its cursor
            event_cursor, resources, queue, inventory = read_sync(self.store)
            snap = _SyncSnapshot(
                id=secrets.token_hex(16),
                scope=scope,
                revision=1,
                items=resources,
                cursor=event_cursor,
                queue=queue,
                inventory=inventory,
            )
            if len(resources) > limit:
                self._snapshots.put(snap)
            return self._page(snap, 0, limit, scope)
        match = _SYNC_CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
        if match is None:
            raise ControlError(E.CURSOR_INVALID)
        token, offset, page = match.group(1), int(match.group(2)), int(match.group(3))
        if (
            page != limit
            or (snapshot_token is not None and not hmac.compare_digest(snapshot_token, token))
            or not hmac.compare_digest(match.group(4), self._mac(token, offset, page, scope))
        ):
            raise ControlError(E.CURSOR_INVALID)
        snap = cast(_SyncSnapshot, self._snapshots.get(token, scope))
        return self._page(snap, offset, limit, scope)


# --------------------------------------------------------------------------- diagnostics


def diagnostics(
    store: ControlStore,
    operation_id: str,
    kind: str,
    cursor: str | None = None,
    *,
    limit: int = DIAGNOSTIC_PAGE,
) -> DiagnosticEntryList:
    """Sanitized, append-only history: fixed reason codes, timestamps, stage, attempt.

    Sources are ``operation_history`` (+ the attempt ID of its outbox row while retained)
    and, for setups, the *result* rows of the private step journal (step id + fixed reason).
    No worker output, exit text, path, argv, prompt or token is ever read here. Both
    sources are append-only, so an offset cursor is stable.
    """
    if type(limit) is not int or not 1 <= limit <= DIAGNOSTIC_PAGE:
        raise ControlError(E.REQUEST_INVALID)
    offset = 0
    if cursor is not None:
        match = _DIAG_CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
        if match is None or int(match.group(2)) != limit:
            raise ControlError(E.CURSOR_INVALID)
        offset = int(match.group(1))
    with store._transaction(write=False) as db:
        row = db.execute(
            "SELECT kind, revision FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()
        if row is None or row[0] != kind:
            raise ControlError(E.RESOURCE_NOT_FOUND)
        entries: list[tuple[str, int, int, str, str | None, str | None]] = [
            (r[0], 0, r[1], r[2], None, r[3])
            for r in db.execute(
                "SELECT h.recorded_at, h.revision, h.reason_code, o.attempt_id "
                "FROM operation_history h LEFT JOIN outbox o ON o.seq = h.outbox_seq "
                "WHERE h.operation_id = ? ORDER BY h.revision",
                (operation_id,),
            )
        ]
        if kind == "setup_operation" and "setup_step_journal" in store._tables(db):
            entries += [
                (r[0], 1, r[1], f"step_{r[2] or r[3]}", r[4], r[5])
                for r in db.execute(
                    "SELECT recorded_at, seq, detail, event, step_id, attempt_id "
                    "FROM setup_step_journal WHERE operation_id = ? "
                    "AND event IN ('result','launch_failed') ORDER BY seq",
                    (operation_id,),
                )
            ]
        if len(entries) > DIAGNOSTIC_MAX:
            raise ControlError(E.LIST_SNAPSHOT_TOO_LARGE)
        entries.sort(key=lambda e: (e[0], e[1], e[2]))
        page = entries[offset : offset + limit]
        try:
            items = tuple(
                DiagnosticEntry(
                    timestamp=e[0],  # type: ignore[arg-type]
                    code=e[3],
                    message_key=f"diagnostics.{e[3]}",
                    stage=e[4],
                    attempt_id=e[5],
                )
                for e in page
            )
        except ValidationError:
            raise ControlError(E.STORE_CORRUPT) from None
        end = offset + limit
        return DiagnosticEntryList(
            schema_version=1,
            revision=int(row[1]),
            items=items,
            next_cursor=f"d1.{end}.{limit}" if end < len(entries) else None,
        )


def watermark(store: ControlStore) -> str:
    """Current ``epoch:seq`` high watermark (a stream without a cursor starts here)."""
    with store._transaction(write=False) as db:
        epoch, _, high = store._parse_cursor(db, None)
        return f"{epoch}:{high}"
