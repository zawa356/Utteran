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
import sqlite3
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
# v6 (PR #33 review fix B9-P2-2): immutable diagnostics attempt sidecar.
DIAGNOSTICS_SCHEMA_VERSION: Final = 6
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
_DIAG_CURSOR = re.compile(
    r"d2\.([0-9a-f]{32})\.([1-9][0-9]{0,8})\.([1-9][0-9]{0,2})\.([0-9a-f]{64})"
)
DIAGNOSTIC_TTL: Final = LIST_TTL
DIAGNOSTIC_CAPACITY: Final = LIST_CAPACITY
DIAGNOSTIC_TOTAL: Final = 4 * DIAGNOSTIC_MAX
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
# Audit metadata that must survive outbox compaction. ``attempt_state`` distinguishes a
# value recorded with the revision, one recovered from a retained outbox row during the
# v5->v6 backfill, and one already lost to compaction before v6 (kept NULL, never guessed).
_DDL_V6: Final = (
    "CREATE TABLE operation_history_attempts ("
    "operation_id TEXT NOT NULL REFERENCES operations(id), revision INTEGER NOT NULL, "
    "outbox_seq INTEGER NOT NULL, attempt_id TEXT, attempt_state TEXT NOT NULL "
    "CHECK (attempt_state IN ('recorded','backfilled','lost')), "
    "CHECK (attempt_state != 'lost' OR attempt_id IS NULL), "
    "PRIMARY KEY (operation_id, revision), "
    "FOREIGN KEY (operation_id, revision) REFERENCES operation_history(operation_id, revision))",
    "INSERT INTO operation_history_attempts "
    "SELECT h.operation_id, h.revision, h.outbox_seq, o.attempt_id, "
    "CASE WHEN o.seq IS NULL THEN 'lost' ELSE 'backfilled' END "
    "FROM operation_history h LEFT JOIN outbox o ON o.seq = h.outbox_seq",
    *(
        f"CREATE TRIGGER operation_history_attempts_immutable_{verb} BEFORE {verb} "
        "ON operation_history_attempts BEGIN SELECT RAISE(ABORT, 'immutable'); END"
        for verb in ("UPDATE", "DELETE")
    ),
)
REQUIRED_TABLES_V6: Final = REQUIRED_TABLES_V5 | {"operation_history_attempts"}


@dataclass(frozen=True)
class CompactionReport:
    deleted: int
    kept_protected: int
    floor_cursor: str


class EventsControlStore(SetupControlStore):
    """Layout v5 (as first submitted in B9): setup store plus outbox compaction.

    Kept so v5 files have an exact owning class (tests, rollback analysis). Runtime code
    uses ``ManagedControlStore`` (v6); this class refuses a v6 file.
    """

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


class ManagedControlStore(EventsControlStore):
    """Layout v6: v5 plus the immutable diagnostics attempt sidecar.

    Every revision is written by ``_journal``; the sidecar row (operation, revision,
    outbox seq, attempt ID) is committed in that same transaction, so diagnostics never
    depend on the retained outbox. v1-v5 migrate to v6 in one ``BEGIN IMMEDIATE``.
    """

    TARGET_VERSION: ClassVar[int] = DIAGNOSTICS_SCHEMA_VERSION

    @classmethod
    def _layout(cls, version: int) -> tuple[tuple[str, ...], frozenset[str]]:
        if version == DIAGNOSTICS_SCHEMA_VERSION:
            return _DDL_V6, REQUIRED_TABLES_V6
        return super()._layout(version)

    def _journal(
        self,
        db: sqlite3.Connection,
        operation_id: str,
        revision: int,
        status: Any,
        reason: Any,
        recovery_required: Any,
        attempt_id: str | None,
    ) -> None:
        super()._journal(db, operation_id, revision, status, reason, recovery_required, attempt_id)
        seq = db.execute(
            "SELECT outbox_seq FROM operation_history WHERE operation_id = ? AND revision = ?",
            (operation_id, revision),
        ).fetchone()[0]
        db.execute(
            "INSERT INTO operation_history_attempts VALUES (?,?,?,?,'recorded')",
            (operation_id, revision, seq, attempt_id),
        )


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


_Row = tuple[str, int, int, str, "str | None", "str | None"]


def _diagnostic_rows(
    db: sqlite3.Connection, store: ControlStore, operation_id: str, kind: str
) -> list[_Row]:
    """Rows in a stable, unique order: (recorded_at, source, source-monotonic id).

    ``operation_history`` (source 0, revision) carries the attempt ID from the v6 sidecar
    when present; v3/v4 stores (no compaction) still read it from the outbox row. The
    setup step journal (source 1, seq) contributes *result* / *launch_failed* rows only.
    """
    tables = store._tables(db)
    if "operation_history_attempts" in tables:
        query = (
            "SELECT h.recorded_at, h.revision, h.reason_code, a.attempt_id "
            "FROM operation_history h LEFT JOIN operation_history_attempts a "
            "ON a.operation_id = h.operation_id AND a.revision = h.revision "
            "WHERE h.operation_id = ? ORDER BY h.revision"
        )
    elif "outbox_compaction" in tables:
        raise ControlError(E.STORE_SCHEMA_UNSUPPORTED)  # v5 without sidecar: unstable
    else:
        query = (
            "SELECT h.recorded_at, h.revision, h.reason_code, o.attempt_id "
            "FROM operation_history h LEFT JOIN outbox o ON o.seq = h.outbox_seq "
            "WHERE h.operation_id = ? ORDER BY h.revision"
        )
    rows: list[_Row] = [
        (r[0], 0, r[1], r[2], None, r[3]) for r in db.execute(query, (operation_id,))
    ]
    if kind == "setup_operation" and "setup_step_journal" in tables:
        rows += [
            (r[0], 1, r[1], f"step_{r[2] or r[3]}", r[4], r[5])
            for r in db.execute(
                "SELECT recorded_at, seq, detail, event, step_id, attempt_id "
                "FROM setup_step_journal WHERE operation_id = ? "
                "AND event IN ('result','launch_failed') ORDER BY seq",
                (operation_id,),
            )
        ]
    rows.sort(key=lambda e: (e[0], e[1], e[2]))
    return rows


class DiagnosticsService:
    """Point-in-time diagnostics paging (same design as B7 ``GET /jobs`` and B9 ``/sync``).

    The first page materializes every public ``DiagnosticEntry`` of one resource and its
    revision in ONE read transaction; later pages slice that immutable snapshot (never
    re-read or re-sorted), so new revisions, step results or compaction cannot shift them.
    Snapshots are process-local, TTL- and capacity-bounded; ``d2`` cursors are HMAC-bound to
    snapshot, offset, page size, resource ID, kind and viewer. Lost/expired: 409
    ``cursor_expired``; tampered or re-scoped: 400 ``cursor_invalid``; too large: 503.
    """

    def __init__(
        self,
        store: JobControlStore,
        *,
        clock: Any = None,
        ttl: float = DIAGNOSTIC_TTL,
        capacity: int = DIAGNOSTIC_CAPACITY,
        max_items: int = DIAGNOSTIC_MAX,
        total_items: int = DIAGNOSTIC_TOTAL,
    ) -> None:
        self.store = store
        self._snapshots = _ListSnapshots(
            clock=clock,
            ttl=ttl,
            capacity=capacity,
            max_items=max_items,
            total_items=total_items,
        )

    @staticmethod
    def _scope(operation_id: str, kind: str, viewer: str) -> str:
        return f"{kind}|{operation_id}|{viewer}"

    def _mac(self, snapshot_id: str, offset: int, limit: int, scope: str) -> str:
        message = f"diagnostics:v2:{snapshot_id}:{offset}:{limit}:{scope}".encode()
        return hmac.new(self.store._secret, message, hashlib.sha256).hexdigest()

    def _page(self, snap: _ListSnapshot, offset: int, limit: int) -> DiagnosticEntryList:
        end = offset + limit
        next_cursor = None
        if end < len(snap.items):
            mac = self._mac(snap.id, end, limit, snap.scope)
            next_cursor = f"d2.{snap.id}.{end}.{limit}.{mac}"
        return DiagnosticEntryList(
            schema_version=1,
            revision=snap.revision,
            items=snap.items[offset:end],
            next_cursor=next_cursor,
        )

    def page(
        self,
        operation_id: str,
        kind: str,
        cursor: str | None = None,
        *,
        limit: int = DIAGNOSTIC_PAGE,
        viewer: str = "",
    ) -> DiagnosticEntryList:
        if type(limit) is not int or not 1 <= limit <= DIAGNOSTIC_PAGE:
            raise ControlError(E.REQUEST_INVALID)
        scope = self._scope(operation_id, kind, viewer)
        if cursor is not None:
            match = _DIAG_CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
            if match is None:
                raise ControlError(E.CURSOR_INVALID)
            snapshot_id, offset, page = match.group(1), int(match.group(2)), int(match.group(3))
            expected = self._mac(snapshot_id, offset, page, scope)
            if page != limit or not hmac.compare_digest(match.group(4), expected):
                raise ControlError(E.CURSOR_INVALID)
            return self._page(self._snapshots.get(snapshot_id, scope), offset, limit)
        with self.store._transaction(write=False) as db:
            row = db.execute(
                "SELECT kind, revision FROM operations WHERE id = ?", (operation_id,)
            ).fetchone()
            if row is None or row[0] != kind:
                raise ControlError(E.RESOURCE_NOT_FOUND)
            rows = _diagnostic_rows(db, self.store, operation_id, kind)
            if len(rows) > self._snapshots.max_items:
                raise ControlError(E.LIST_SNAPSHOT_TOO_LARGE)  # never truncate silently
            try:
                items = tuple(
                    DiagnosticEntry(
                        timestamp=e[0],  # type: ignore[arg-type]
                        code=e[3],
                        message_key=f"diagnostics.{e[3]}",
                        stage=e[4],
                        attempt_id=e[5],
                    )
                    for e in rows
                )
            except ValidationError:
                raise ControlError(E.STORE_CORRUPT) from None
            snapshot = _ListSnapshot(
                id=secrets.token_hex(16), scope=scope, revision=int(row[1]), items=items
            )
        if len(items) > limit:
            self._snapshots.put(snapshot)  # only multi-page listings are retained
        return self._page(snapshot, 0, limit)


def watermark(store: ControlStore) -> str:
    """Current ``epoch:seq`` high watermark (a stream without a cursor starts here)."""
    with store._transaction(write=False) as db:
        epoch, _, high = store._parse_cursor(db, None)
        return f"{epoch}:{high}"
