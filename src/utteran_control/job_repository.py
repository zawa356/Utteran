"""B7 job persistence on top of the B6 control store (storage layout v3).

``JobControlStore`` shares every B6 primitive and transaction: job records, immutable
private snapshots, queue items, revisions, outbox events and the idempotent response are
committed in one ``BEGIN IMMEDIATE``. Batches are all-or-nothing. Nothing here reads input
files, resolves candidates, starts processes or serves HTTP.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal

from pydantic import ValidationError

from utteran_contracts.control import OperationStatus, QueueView, ReasonCode
from utteran_contracts.jobs import (
    BlockReason,
    InputIdentity,
    Job,
    JobBatch,
    JobErrorCode,
    JobList,
    JobSnapshot,
    spec_view,
)
from utteran_control import transitions
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.store import (
    REQUIRED_TABLES,
    AcceptedQueue,
    ControlStore,
    IdempotencyScope,
    _check_private,
    _new_id,
    canonical,
)

S = OperationStatus
R = ReasonCode
E = ErrorCode

JOB_SCHEMA_VERSION: Final = 3
MAX_BATCH: Final = 100
MAX_LIST: Final = 100
_CURSOR = re.compile(r"j2\.([0-9a-f]{32})\.([1-9][0-9]{0,8})\.([1-9][0-9]{0,2})\.([0-9a-f]{64})")
LIST_TTL: Final = 300.0
LIST_CAPACITY: Final = 16
LIST_MAX_ITEMS: Final = 10_000
LIST_TOTAL_ITEMS: Final = 50_000

_DDL_V3: Final = (
    # One immutable private snapshot per B7 job. Retries copy the body verbatim.
    "CREATE TABLE job_snapshots (operation_id TEXT PRIMARY KEY REFERENCES operations(id), "
    "batch_id TEXT NOT NULL, body TEXT NOT NULL, digest TEXT NOT NULL "
    "CHECK (length(digest) = 64), retry_of TEXT REFERENCES operations(id), "
    "rerun_of TEXT REFERENCES operations(id), created_at TEXT NOT NULL, "
    "CHECK (retry_of IS NULL OR rerun_of IS NULL))",
    "CREATE INDEX job_snapshots_batch ON job_snapshots(batch_id)",
    # Mutable side state with fixed vocabularies only.
    "CREATE TABLE job_state (operation_id TEXT PRIMARY KEY REFERENCES operations(id), "
    "block_reason TEXT, error_code TEXT)",
    # Verified completion evidence (private digests). Written once, never rewritten.
    "CREATE TABLE job_results (operation_id TEXT PRIMARY KEY REFERENCES operations(id), "
    "attempt_id TEXT NOT NULL REFERENCES attempts(id), core_job_id TEXT NOT NULL, "
    "artifact_count INTEGER NOT NULL CHECK (artifact_count BETWEEN 1 AND 5), "
    "evidence TEXT NOT NULL, recorded_at TEXT NOT NULL)",
    # JobBatch responses; distinct from OperationView ``replays`` and QueueView replays.
    "CREATE TABLE batch_replays (installation TEXT NOT NULL, method TEXT NOT NULL, "
    "endpoint TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, "
    "status INTEGER NOT NULL, headers TEXT NOT NULL, body TEXT NOT NULL, "
    "batch_id TEXT NOT NULL, created_at TEXT NOT NULL, "
    "PRIMARY KEY (installation, method, endpoint, key))",
    # Single-Job responses (cancel / retry / revalidate).
    "CREATE TABLE job_replays (installation TEXT NOT NULL, method TEXT NOT NULL, "
    "endpoint TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, "
    "status INTEGER NOT NULL, headers TEXT NOT NULL, body TEXT NOT NULL, "
    "operation_id TEXT NOT NULL REFERENCES operations(id), created_at TEXT NOT NULL, "
    "PRIMARY KEY (installation, method, endpoint, key))",
    *(
        f"CREATE TRIGGER {table}_immutable_{verb} BEFORE {verb} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
        for table in ("job_snapshots", "job_results", "batch_replays", "job_replays")
        for verb in ("UPDATE", "DELETE")
    ),
)
REQUIRED_TABLES_V3: Final = REQUIRED_TABLES | {
    "job_snapshots",
    "job_state",
    "job_results",
    "batch_replays",
    "job_replays",
}
RETRYABLE: Final = frozenset({S.FAILED, S.CANCELLED, S.INTERRUPTED})


@dataclass(frozen=True)
class AcceptedBatch:
    status: int
    headers: Mapping[str, str]
    batch: JobBatch
    replayed: bool


@dataclass(frozen=True)
class AcceptedJob:
    status: int
    headers: Mapping[str, str]
    job: Job
    replayed: bool


@dataclass(frozen=True)
class CompletionEvidence:
    """Private, already verified completion facts recorded with the terminal state."""

    core_job_id: str
    artifact_digests: tuple[str, ...]
    manifest_digest: str


@dataclass(frozen=True)
class _ListSnapshot:
    id: str
    scope: str
    revision: int
    items: tuple[Any, ...]  # allowlisted public views only (Job, or B8 SetupOperation)


class _ListSnapshots:
    """Process-local, TTL- and capacity-bounded registry of immutable public listings.

    Holds only allowlisted public ``Job`` views (no private snapshot, prompt or path).
    Lost on restart by design; the oldest entry is evicted explicitly when full.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None,
        ttl: float,
        capacity: int,
        max_items: int,
        total_items: int,
    ) -> None:
        self.clock = clock or time.monotonic
        self.ttl, self.capacity = ttl, capacity
        self.max_items, self.total_items = max_items, total_items
        self._entries: OrderedDict[str, tuple[float, _ListSnapshot]] = OrderedDict()
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for key in [k for k, (t, _) in self._entries.items() if now - t >= self.ttl]:
            del self._entries[key]

    def put(self, snapshot: _ListSnapshot) -> None:
        with self._lock:
            now = self.clock()
            self._purge(now)
            while self._entries and (
                len(self._entries) >= self.capacity
                or sum(len(s.items) for _, s in self._entries.values()) + len(snapshot.items)
                > self.total_items
            ):
                self._entries.popitem(last=False)  # its cursors become cursor_expired
            self._entries[snapshot.id] = (now, snapshot)

    def get(self, snapshot_id: str, scope: str) -> _ListSnapshot:
        with self._lock:
            now = self.clock()
            self._purge(now)
            entry = self._entries.get(snapshot_id)
            if entry is None:
                raise ControlError(E.LIST_CURSOR_EXPIRED)
            if not hmac.compare_digest(entry[1].scope, scope):
                raise ControlError(E.CURSOR_INVALID)
            return entry[1]


class JobControlStore(ControlStore):
    """B6 store opened at layout v3. Migrates v1/v2 explicitly; v3 is unreadable to B6."""

    TARGET_VERSION: ClassVar[int] = JOB_SCHEMA_VERSION
    REPLAY_TABLES: ClassVar[tuple[str, ...]] = (
        "replays",
        "queue_replays",
        "batch_replays",
        "job_replays",
    )

    def __init__(
        self,
        path: Path,
        *,
        list_clock: Callable[[], float] | None = None,
        list_ttl: float = LIST_TTL,
        list_capacity: int = LIST_CAPACITY,
        list_max_items: int = LIST_MAX_ITEMS,
        list_total_items: int = LIST_TOTAL_ITEMS,
        **kwargs: Any,
    ) -> None:
        self._lists = _ListSnapshots(
            clock=list_clock,
            ttl=list_ttl,
            capacity=list_capacity,
            max_items=list_max_items,
            total_items=list_total_items,
        )
        super().__init__(path, **kwargs)

    @classmethod
    def _layout(cls, version: int) -> tuple[tuple[str, ...], frozenset[str]]:
        if version == JOB_SCHEMA_VERSION:
            return _DDL_V3, REQUIRED_TABLES_V3
        return super()._layout(version)

    # ------------------------------------------------------------------ identities

    def public_input_id(self, identity: InputIdentity) -> str:
        """Opaque, installation-keyed identity digest; never reveals the path."""
        text = canonical(identity.to_wire()).encode()
        return "in_" + hmac.new(self._secret, text, hashlib.sha256).hexdigest()[:32]

    def request_digest(self, body: Any) -> str:
        return self._digest(body)

    # ------------------------------------------------------------------ views

    def _snapshot_row(self, db: sqlite3.Connection, job_id: str) -> tuple[Any, ...]:
        row = db.execute(
            "SELECT body, digest, retry_of, rerun_of FROM job_snapshots WHERE operation_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise ControlError(E.RESOURCE_NOT_FOUND)  # not a B7 job
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise ControlError(E.STORE_CORRUPT)
        return tuple(row)

    def _snapshot(self, db: sqlite3.Connection, job_id: str) -> JobSnapshot:
        body = self._snapshot_row(db, job_id)[0]
        try:
            return JobSnapshot.model_validate_json(body)
        except (ValidationError, ValueError):
            raise ControlError(E.STORE_CORRUPT) from None

    def _job_view(self, db: sqlite3.Connection, job_id: str) -> Job:
        operation = self._view(db, job_id)
        if operation.kind != "job":
            raise ControlError(E.RESOURCE_NOT_FOUND)
        body, _, retry_of, rerun_of = self._snapshot_row(db, job_id)
        try:
            snapshot = JobSnapshot.model_validate_json(body)
        except (ValidationError, ValueError):
            raise ControlError(E.STORE_CORRUPT) from None
        state = db.execute(
            "SELECT block_reason, error_code FROM job_state WHERE operation_id = ?", (job_id,)
        ).fetchone()
        result = db.execute(
            "SELECT core_job_id, artifact_count FROM job_results WHERE operation_id = ?",
            (job_id,),
        ).fetchone()
        attempts = db.execute(
            "SELECT id, number, state, launch, started_at, ended_at FROM attempts "
            "WHERE operation_id = ? ORDER BY number",
            (job_id,),
        ).fetchall()
        try:
            return Job.model_validate(
                {
                    "schema_version": 1,
                    "id": job_id,
                    "revision": operation.revision,
                    "status": operation.status,
                    "held": operation.held,
                    "blockers": operation.blockers,
                    "block_reason": state[0] if state else None,
                    "recovery_required": operation.recovery_required,
                    "reason_code": operation.reason_code,
                    "snapshot": {
                        "created_at": snapshot.created_at,
                        "client_file_id": snapshot.file.client_file_id,
                        "preset_ref": snapshot.preset_ref,
                        "policy_ref": snapshot.policy_ref,
                        "resume_mode": snapshot.resume_mode,
                        "input_identity": self.public_input_id(snapshot.input_identity),
                        "spec": spec_view(snapshot.resolved),
                    },
                    "attempts": [
                        {
                            "id": a[0],
                            "number": a[1],
                            "state": a[2],
                            "launch": a[3],
                            "started_at": a[4],
                            "ended_at": a[5],
                        }
                        for a in attempts
                    ],
                    "retry_of": retry_of,
                    "rerun_of": rerun_of,
                    "result": None
                    if result is None
                    else {
                        "legacy_core_job_id": result[0],
                        "artifact_count": result[1],
                        "verified": True,
                    },
                    "error": {"code": state[1]} if state and state[1] else None,
                    "created_at": operation.created_at,
                    "updated_at": operation.updated_at,
                }
            )
        except ValidationError:
            raise ControlError(E.STORE_CORRUPT) from None

    def get_job(self, job_id: str) -> Job:
        with self._transaction(write=False) as db:
            return self._job_view(db, job_id)

    def get_snapshot(self, job_id: str) -> JobSnapshot:
        """Private read. Callers must not log, publish or copy this into events."""
        with self._transaction(write=False) as db:
            self._row(db, job_id)
            return self._snapshot(db, job_id)

    def _list_mac(self, snapshot_id: str, offset: int, limit: int, scope: str) -> str:
        # Bound to the snapshot, position, page size, viewer scope and the (only) filter.
        message = f"jobs:v2:{snapshot_id}:{offset}:{limit}:filter=all:{scope}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def _list_page(self, snapshot: _ListSnapshot, offset: int, limit: int, scope: str) -> JobList:
        end = offset + limit
        next_cursor = None
        if end < len(snapshot.items):
            mac = self._list_mac(snapshot.id, end, limit, scope)
            next_cursor = f"j2.{snapshot.id}.{end}.{limit}.{mac}"
        return JobList(
            schema_version=1,
            revision=snapshot.revision,
            items=snapshot.items[offset:end],
            next_cursor=next_cursor,
        )

    def list_jobs(
        self, cursor: str | None = None, *, limit: int = MAX_LIST, scope: str = ""
    ) -> JobList:
        """Point-in-time paging over allowlisted public Job views.

        The first page materializes every public Job, the order and the collection
        revision from one read transaction; later pages slice that immutable snapshot,
        so state changes or new jobs never leak into an existing cursor. Snapshots are
        process-local, TTL- and capacity-bounded: a lost/expired one is 409
        ``cursor_expired``; a tampered or re-scoped cursor is ``cursor_invalid``.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_LIST or not isinstance(scope, str):
            raise ControlError(E.REQUEST_INVALID)
        if cursor is not None:
            match = _CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
            if match is None:
                raise ControlError(E.CURSOR_INVALID)
            snapshot_id, offset, page = match.group(1), int(match.group(2)), int(match.group(3))
            expected = self._list_mac(snapshot_id, offset, page, scope)
            if page != limit or not hmac.compare_digest(match.group(4), expected):
                raise ControlError(E.CURSOR_INVALID)
            snapshot = self._lists.get(snapshot_id, scope)
            return self._list_page(snapshot, offset, limit, scope)
        with self._transaction(write=False) as db:
            total = db.execute("SELECT count(*) FROM job_snapshots").fetchone()[0]
            if total > self._lists.max_items:
                raise ControlError(E.LIST_SNAPSHOT_TOO_LARGE)  # never truncate silently
            rows = db.execute("SELECT operation_id FROM job_snapshots ORDER BY rowid").fetchall()
            revision = db.execute(
                "SELECT value FROM counters WHERE name = 'operations_revision'"
            ).fetchone()[0]
            snapshot = _ListSnapshot(
                id=secrets.token_hex(16),
                scope=scope,
                revision=max(revision, 1),
                items=tuple(self._job_view(db, row[0]) for row in rows),
            )
        if len(snapshot.items) > limit:
            self._lists.put(snapshot)  # only multi-page listings need to be retained
        return self._list_page(snapshot, 0, limit, scope)

    # ------------------------------------------------------------------ replays

    @staticmethod
    def _headers(raw: str) -> dict[str, str]:
        headers = json.loads(raw)
        if not isinstance(headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
        ):
            raise ValueError
        return headers

    def _batch_replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> AcceptedBatch | None:
        row = db.execute(
            "SELECT digest, status, headers, body FROM batch_replays WHERE installation = ? "
            "AND method = 'POST' AND endpoint = ? AND key = ?",
            (self._installation, scope.endpoint, scope.key),
        ).fetchone()
        if row is None:
            self._scope_free(db, "batch_replays", scope)
            return None
        if not hmac.compare_digest(row[0], digest):
            raise ControlError(E.IDEMPOTENCY_CONFLICT)
        try:
            batch = JobBatch.model_validate_json(row[3])
            return AcceptedBatch(row[1], MappingProxyType(self._headers(row[2])), batch, True)
        except (ValueError, ValidationError):
            raise ControlError(E.STORE_CORRUPT) from None

    def _job_replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> AcceptedJob | None:
        row = db.execute(
            "SELECT digest, status, headers, body FROM job_replays WHERE installation = ? "
            "AND method = 'POST' AND endpoint = ? AND key = ?",
            (self._installation, scope.endpoint, scope.key),
        ).fetchone()
        if row is None:
            self._scope_free(db, "job_replays", scope)
            return None
        if not hmac.compare_digest(row[0], digest):
            raise ControlError(E.IDEMPOTENCY_CONFLICT)
        try:
            job = Job.model_validate_json(row[3])
            return AcceptedJob(row[1], MappingProxyType(self._headers(row[2])), job, True)
        except (ValueError, ValidationError):
            raise ControlError(E.STORE_CORRUPT) from None

    def _remember_job(
        self,
        db: sqlite3.Connection,
        scope: IdempotencyScope,
        digest: str,
        status: int,
        job: Job,
        *,
        location: bool = False,
    ) -> AcceptedJob:
        headers = {"ETag": f'"{job.revision}"'}
        if location:
            headers["Location"] = f"/api/v1/jobs/{job.id}"
        db.execute(
            "INSERT INTO job_replays VALUES (?,'POST',?,?,?,?,?,?,?,?)",
            (
                self._installation,
                scope.endpoint,
                scope.key,
                digest,
                status,
                canonical(headers),
                canonical(job.to_wire()),
                job.id,
                self._now(),
            ),
        )
        return AcceptedJob(status, MappingProxyType(headers), job, False)

    def _remember_queue(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> AcceptedQueue:
        view = self._queue_view(db)
        headers = {"ETag": f'"{view.revision}"'}
        db.execute(
            "INSERT INTO queue_replays VALUES (?,'POST',?,?,?,?,?,?,?,?)",
            (
                self._installation,
                scope.endpoint,
                scope.key,
                digest,
                200,
                canonical(headers),
                canonical(view.to_wire()),
                view.revision,
                self._now(),
            ),
        )
        return AcceptedQueue(200, MappingProxyType(headers), view, False)

    def lookup_batch(self, scope: IdempotencyScope, digest: str) -> AcceptedBatch | None:
        """Replay lookup only; lets callers replay before token / admission checks."""
        self._validate_scope(scope)
        with self._transaction(write=False) as db:
            return self._batch_replay(db, scope, digest)

    def lookup_job(self, scope: IdempotencyScope, digest: str) -> AcceptedJob | None:
        self._validate_scope(scope)
        with self._transaction(write=False) as db:
            return self._job_replay(db, scope, digest)

    # ------------------------------------------------------------------ batch accept

    def _insert_job(
        self,
        db: sqlite3.Connection,
        snapshot_body: str,
        batch_id: str,
        *,
        retry_of: str | None = None,
        rerun_of: str | None = None,
    ) -> str:
        job_id = _new_id("job_")
        now = self._now()
        db.execute(
            "INSERT INTO operations VALUES (?,'job',?,1,?,NULL,?,?)",
            (job_id, S.QUEUED.value, R.SUBMITTED.value, now, now),
        )
        # B6 private record holds only a digest marker; the snapshot has its own table.
        digest = hashlib.sha256(snapshot_body.encode()).hexdigest()
        db.execute(
            "INSERT INTO operation_private VALUES (?,?)",
            (job_id, canonical({"b7_snapshot_digest": digest})),
        )
        db.execute(
            "INSERT INTO job_snapshots VALUES (?,?,?,?,?,?,?)",
            (job_id, batch_id, snapshot_body, digest, retry_of, rerun_of, now),
        )
        db.execute("INSERT INTO job_state VALUES (?,NULL,NULL)", (job_id,))
        self._journal(db, job_id, 1, S.QUEUED, R.SUBMITTED, None, None)
        self._enqueue(db, job_id)
        return job_id

    @staticmethod
    def _body(snapshot: JobSnapshot) -> str:
        wire = snapshot.to_wire()
        _check_private(wire)
        return canonical(wire)

    def accept_batch(
        self,
        scope: IdempotencyScope,
        digest: str,
        snapshots: Sequence[JobSnapshot],
        *,
        rerun_of: str | None = None,
    ) -> AcceptedBatch:
        """Commit every job of one submission, or none of them.

        Replay is checked first inside the same write transaction, so a lost response is
        answered with the original JobBatch, Location and ETag and never duplicates jobs.
        """
        self._validate_scope(scope)
        if not 1 <= len(snapshots) <= MAX_BATCH:
            raise ControlError(E.REQUEST_INVALID)
        bodies = [self._body(item) for item in snapshots]
        with self._transaction() as db:
            replay = self._batch_replay(db, scope, digest)
            if replay is not None:
                return replay
            if rerun_of is not None:
                kind, status, _, _ = self._row(db, rerun_of)
                self._snapshot_row(db, rerun_of)
                if kind != "job" or status not in transitions.TERMINAL | {S.INTERRUPTED}:
                    raise ControlError(E.INVALID_TRANSITION)
            batch_id = _new_id("bat_")
            ids = [self._insert_job(db, body, batch_id, rerun_of=rerun_of) for body in bodies]
            self._queue_changed(db, R.SUBMITTED)
            jobs = tuple(self._job_view(db, job_id) for job_id in ids)
            queue_revision = db.execute("SELECT revision FROM queue").fetchone()[0]
            batch = JobBatch(schema_version=1, jobs=jobs, queue_revision=queue_revision)
            headers = {"ETag": f'"{queue_revision}"', "Location": "/api/v1/jobs"}
            if self._fault is not None:
                self._fault("batch_before_replay")
            db.execute(
                "INSERT INTO batch_replays VALUES (?,'POST',?,?,?,?,?,?,?,?)",
                (
                    self._installation,
                    scope.endpoint,
                    scope.key,
                    digest,
                    202,
                    canonical(headers),
                    canonical(batch.to_wire()),
                    batch_id,
                    self._now(),
                ),
            )
            return AcceptedBatch(202, MappingProxyType(headers), batch, False)

    # ------------------------------------------------------------------ job commands

    def _cas_job(self, db: sqlite3.Connection, job_id: str, expected: int) -> S:
        kind, status, revision, _ = self._row(db, job_id)
        self._snapshot_row(db, job_id)
        if kind != "job":
            raise ControlError(E.RESOURCE_NOT_FOUND)
        if revision != expected:
            raise ControlError(E.REVISION_CONFLICT)
        return status

    def cancel_job(
        self, job_id: str, expected_revision: int, scope: IdempotencyScope
    ) -> AcceptedJob:
        """Waiting jobs end atomically; running jobs become ``cancelling`` (202 != stopped).

        The lease stays with the running attempt until its exit is observed and reconciled.
        """
        self._validate_scope(scope)
        digest = self._digest(
            {"op": "cancel", "job": job_id, "expected_revision": expected_revision}
        )
        with self._transaction() as db:
            replay = self._job_replay(db, scope, digest)
            if replay is not None:
                return replay
            status = self._cas_job(db, job_id, expected_revision)
            if status in transitions.TERMINAL or status is S.INTERRUPTED:
                return self._remember_job(db, scope, digest, 200, self._job_view(db, job_id))
            if status is S.CANCELLING:
                return self._remember_job(db, scope, digest, 202, self._job_view(db, job_id))
            if status in transitions.WAITING:
                self._release(db, job_id, "released")
                self._record(db, job_id, S.CANCELLED, R.USER_CANCEL)
                self._queue_changed(db, R.USER_CANCEL)
            elif status is S.RUNNING:
                lease = db.execute(
                    "SELECT attempt_id FROM leases WHERE operation_id = ?", (job_id,)
                ).fetchone()
                if lease is None:
                    raise ControlError(E.STORE_CORRUPT)
                self._record(db, job_id, S.CANCELLING, R.CANCEL_ACCEPTED, attempt_id=lease[0])
            else:
                raise ControlError(E.INVALID_TRANSITION)
            return self._remember_job(db, scope, digest, 202, self._job_view(db, job_id))

    def retry_job(
        self, job_id: str, expected_revision: int, scope: IdempotencyScope
    ) -> AcceptedJob:
        """New job, identical snapshot body, ``retry_of`` link. The source is untouched.

        Callers verify the input identity first (409 when changed); this only copies.
        """
        self._validate_scope(scope)
        digest = self._digest(
            {"op": "retry", "job": job_id, "expected_revision": expected_revision}
        )
        with self._transaction() as db:
            replay = self._job_replay(db, scope, digest)
            if replay is not None:
                return replay
            status = self._cas_job(db, job_id, expected_revision)
            if status not in RETRYABLE:
                raise ControlError(E.INVALID_TRANSITION)
            body = self._snapshot_row(db, job_id)[0]
            new_id = self._insert_job(db, body, _new_id("bat_"), retry_of=job_id)
            self._queue_changed(db, R.SUBMITTED)
            view = self._job_view(db, new_id)
            return self._remember_job(db, scope, digest, 202, view, location=True)

    def block_job(self, job_id: str, expected_revision: int, reason: BlockReason) -> Job:
        """Dispatcher pre-start revalidation failed: queued/held -> blocked, same snapshot."""
        with self._transaction() as db:
            status = self._cas_job(db, job_id, expected_revision)
            if status not in (S.QUEUED, S.HELD, S.BLOCKED):
                raise ControlError(E.INVALID_TRANSITION)
            db.execute(
                "UPDATE queue_items SET blockers = ? WHERE operation_id = ?",
                (R.PRESTART_REVALIDATION_FAILED.value, job_id),
            )
            db.execute(
                "UPDATE job_state SET block_reason = ? WHERE operation_id = ?", (reason, job_id)
            )
            if status is not S.BLOCKED:
                self._record(db, job_id, S.BLOCKED, R.PRESTART_REVALIDATION_FAILED)
                self._queue_changed(db, R.PRESTART_REVALIDATION_FAILED)
            return self._job_view(db, job_id)

    def revalidated(
        self,
        job_id: str,
        expected_revision: int,
        scope: IdempotencyScope,
        *,
        failure: BlockReason | None,
    ) -> AcceptedJob:
        """Record the outcome of re-checking the *same* frozen snapshot of a blocked job.

        ``failure is None`` clears the block (back to queued, or held when the user hold
        is still set). A failure keeps the job blocked and raises 409 without replay.
        """
        self._validate_scope(scope)
        digest = self._digest(
            {"op": "revalidate", "job": job_id, "expected_revision": expected_revision}
        )
        with self._transaction() as db:
            replay = self._job_replay(db, scope, digest)
            if replay is not None:
                return replay
            status = self._cas_job(db, job_id, expected_revision)
            if status is not S.BLOCKED:
                raise ControlError(E.INVALID_TRANSITION)
            if failure is not None:
                db.execute(
                    "UPDATE job_state SET block_reason = ? WHERE operation_id = ?",
                    (failure, job_id),
                )
                raise ControlError(E.NOT_DISPATCHABLE)
            held = db.execute(
                "SELECT held FROM queue_items WHERE operation_id = ?", (job_id,)
            ).fetchone()[0]
            db.execute("UPDATE queue_items SET blockers = '' WHERE operation_id = ?", (job_id,))
            db.execute("UPDATE job_state SET block_reason = NULL WHERE operation_id = ?", (job_id,))
            self._record(db, job_id, S.HELD if held else S.QUEUED, R.RESOURCES_AVAILABLE)
            self._queue_changed(db, R.RESOURCES_AVAILABLE)
            return self._remember_job(db, scope, digest, 202, self._job_view(db, job_id))

    # ------------------------------------------------------------------ queue commands

    def reorder(
        self, expected_revision: int, waiting_item_ids: Sequence[str], scope: IdempotencyScope
    ) -> AcceptedQueue:
        """Replace the order of *all* waiting items (held included) in one transaction."""
        self._validate_scope(scope)
        ids = list(waiting_item_ids)
        digest = self._digest({"op": "reorder", "expected_revision": expected_revision, "ids": ids})
        with self._transaction() as db:
            replay = self._queue_replay(db, scope, digest)
            if replay is not None:
                return replay
            if len(set(ids)) != len(ids) or not all(isinstance(i, str) for i in ids):
                raise ControlError(E.REQUEST_INVALID)
            if db.execute("SELECT revision FROM queue").fetchone()[0] != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            rows = db.execute("SELECT id, active, ord FROM queue_items").fetchall()
            active = {row[0] for row in rows if row[1]}
            if active & set(ids):
                raise ControlError(E.INVALID_TRANSITION)
            waiting = sorted((row[2], row[0]) for row in rows if not row[1])
            if set(ids) != {item for _, item in waiting}:
                raise ControlError(E.REQUEST_INVALID)
            if ids != [item for _, item in waiting]:
                # Reuse the existing ord values (all below the enqueue counter) and move
                # through negatives first, so the UNIQUE(ord) index never collides.
                slots = [ord_ for ord_, _ in waiting]
                for index, item in enumerate(ids):
                    db.execute("UPDATE queue_items SET ord = ? WHERE id = ?", (-(index + 1), item))
                for slot, item in zip(slots, ids, strict=True):
                    db.execute("UPDATE queue_items SET ord = ? WHERE id = ?", (slot, item))
                self._queue_changed(db, R.USER_REORDER)
            return self._remember_queue(db, scope, digest)

    def set_item_hold(
        self, item_id: str, expected_revision: int, held: bool, scope: IdempotencyScope
    ) -> AcceptedQueue:
        """Queue-revision CAS hold/release of one waiting item (not the B6 operation CAS)."""
        self._validate_scope(scope)
        if type(held) is not bool:
            raise ControlError(E.REQUEST_INVALID)
        digest = self._digest(
            {"op": "hold", "item": item_id, "expected_revision": expected_revision, "held": held}
        )
        with self._transaction() as db:
            replay = self._queue_replay(db, scope, digest)
            if replay is not None:
                return replay
            if db.execute("SELECT revision FROM queue").fetchone()[0] != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            item = db.execute(
                "SELECT q.operation_id, q.active, q.held, q.blockers, o.kind, o.status "
                "FROM queue_items q JOIN operations o ON o.id = q.operation_id WHERE q.id = ?",
                (item_id,),
            ).fetchone()
            if item is None:
                raise ControlError(E.RESOURCE_NOT_FOUND)
            operation_id, is_active, was_held, blockers, kind, status = item
            if is_active or S(status) not in transitions.WAITING:
                raise ControlError(E.INVALID_TRANSITION)
            if bool(was_held) != held:
                db.execute("UPDATE queue_items SET held = ? WHERE id = ?", (int(held), item_id))
                target = S(status)
                if kind == "job" and not blockers:
                    target = S.HELD if held else S.QUEUED
                reason = R.USER_HOLD if held else R.USER_RELEASE
                self._record(db, operation_id, target, reason)
                self._queue_changed(db, reason)
            return self._remember_queue(db, scope, digest)

    # ------------------------------------------------------------------ dispatch

    def _dispatch_guard(self, db: sqlite3.Connection, operation_id: str) -> None:
        """Only a B7 job that is the *first* eligible waiting item may start."""
        if (
            db.execute(
                "SELECT 1 FROM job_snapshots WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            is None
        ):
            raise ControlError(E.NOT_DISPATCHABLE)
        first = db.execute(
            "SELECT operation_id FROM queue_items WHERE active = 0 AND held = 0 "
            "AND blockers = '' ORDER BY ord LIMIT 1"
        ).fetchone()
        if first is None or first[0] != operation_id:
            raise ControlError(E.NOT_DISPATCHABLE)

    def next_dispatchable(self) -> tuple[str, int] | None:
        """First eligible waiting B7 job (id, revision), or None. Read-only hint."""
        with self._transaction(write=False) as db:
            if db.execute("SELECT dispatch FROM queue").fetchone()[0] != "running":
                return None
            if db.execute("SELECT 1 FROM leases WHERE slot = 'heavy'").fetchone():
                return None
            row = db.execute(
                "SELECT q.operation_id, o.revision, s.operation_id FROM queue_items q "
                "JOIN operations o ON o.id = q.operation_id "
                "LEFT JOIN job_snapshots s ON s.operation_id = q.operation_id "
                "WHERE q.active = 0 AND q.held = 0 AND q.blockers = '' ORDER BY q.ord LIMIT 1"
            ).fetchone()
            if row is None or row[2] is None:
                return None  # nothing, or a non-B7 item is first: never skip past it
            return str(row[0]), int(row[1])

    def finish_attempt(
        self,
        job_id: str,
        fencing_token: int,
        outcome: Literal["succeeded", "failed", "cancelled"],
        *,
        error: JobErrorCode | None = None,
        evidence: CompletionEvidence | None = None,
    ) -> Job:
        """Fenced terminal reconciliation after the child exit has been observed.

        A cancel request recorded before this commit wins: even a verified completion then
        converges to ``cancelled`` and no result is linked. A stale owner is rejected.
        """
        if (outcome == "succeeded") != (evidence is not None):
            raise ControlError(E.REQUEST_INVALID)
        with self._transaction() as db:
            attempt_id = self._fenced(db, job_id, fencing_token)
            _, status, _, _ = self._row(db, job_id)
            if status not in transitions.ACTIVE:
                raise ControlError(E.STORE_CORRUPT)
            if status is S.CANCELLING and outcome != "failed":
                target, reason = S.CANCELLED, R.EXIT_RECONCILED
                evidence = None
            elif status is S.CANCELLING:
                target, reason = S.FAILED, R.CLASSIFIED_FAILURE
            elif outcome == "succeeded":
                target, reason = S.SUCCEEDED, R.COMPLETION_VERIFIED
            else:
                # A worker "cancelled" without a recorded user cancel is a failure.
                target, reason = S.FAILED, R.CLASSIFIED_FAILURE
                error = error or "cancelled"
            transitions.check("job", status, target)
            self._release(db, job_id, "released")
            if evidence is not None and target is S.SUCCEEDED:
                db.execute(
                    "INSERT INTO job_results VALUES (?,?,?,?,?,?)",
                    (
                        job_id,
                        attempt_id,
                        evidence.core_job_id,
                        len(evidence.artifact_digests),
                        canonical(
                            {
                                "artifacts": list(evidence.artifact_digests),
                                "manifest": evidence.manifest_digest,
                            }
                        ),
                        self._now(),
                    ),
                )
            if target is S.FAILED:
                db.execute(
                    "UPDATE job_state SET error_code = ? WHERE operation_id = ?",
                    (error or "core_failure", job_id),
                )
            self._record(db, job_id, target, reason, attempt_id=attempt_id)
            self._queue_changed(db, reason)
            return self._job_view(db, job_id)

    def flag_unconfirmed(self, job_id: str, fencing_token: int) -> Job:
        """Exit could not be confirmed: keep the slot, mark recovery_required."""
        with self._transaction() as db:
            attempt_id = self._fenced(db, job_id, fencing_token)
            _, status, _, flagged = self._row(db, job_id)
            if flagged != R.TERMINATION_FAILED.value:
                self._record(
                    db,
                    job_id,
                    status,
                    R.TERMINATION_FAILED,
                    recovery_required=R.TERMINATION_FAILED,
                    attempt_id=attempt_id,
                )
            return self._job_view(db, job_id)

    def active_job(self) -> tuple[str, int] | None:
        """(job_id, fencing_token) holding the heavy slot, if any."""
        with self._transaction(write=False) as db:
            row = db.execute(
                "SELECT operation_id, fencing_token FROM leases WHERE slot = 'heavy'"
            ).fetchone()
            return None if row is None else (str(row[0]), int(row[1]))

    def queue_view(self) -> QueueView:
        return self.queue()
