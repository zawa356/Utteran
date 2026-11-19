"""Layout v8: tombstoning a finished job's history record (bundle C2c), additive to v7.

A deletion keeps the *skeleton* every other reader relies on -- the operation row, its
immutable state journal and outbox events, attempts, queue and visibility rows, and the
``retry_of``/``rerun_of`` links -- and replaces the private content (the frozen snapshot
body and its digest, the completion evidence, private side rows) with fixed tombstone
values. Nothing here touches a file. The owned-artifact deletion (④) is refused on every
platform (PR #58 owner review): ``complete_deletion`` rejects that mode, and nothing writes
a new ``artifacts_pending`` journal row. The v8 columns for it stay (layout unchanged); a
row of that state written by the pre-review build blocks the history deletion of its job.

Immutability (docs/architecture/program/c2c-deletion-design.md §3):
- ``job_snapshots`` and ``job_results`` keep their ``BEFORE DELETE`` triggers. Their
  ``BEFORE UPDATE`` triggers are replaced by guarded ones that pass *only* while the job's
  row is in ``job_deletion_gate``, the job is finished, every other column is unchanged and
  the new value is the fixed tombstone literal. The gate is filled and emptied inside the
  one deletion transaction; a store whose committed gate is not empty is ``store_corrupt``.
- A main-schema trigger cannot see a TEMP table (measured), hence a persistent gate table,
  the same pattern as the v5 ``outbox_compaction`` guard.

After deletion every job read (``GET /jobs/{id}``, visibility, artifacts, retry, rerun,
cancel, revalidate) and every replay whose body names the job is ``410 job_deleted``. The
job leaves every list; B9 sync/events/diagnostics keep the value-free skeleton and no event
is written (deletion is not a state transition). ``PRAGMA secure_delete`` is on for the
deletion transaction. Backups and the rollback journal's freed blocks are out of reach and
are documented, never described as erased.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal

from pydantic import ValidationError

from utteran_contracts.control import Status as S
from utteran_contracts.jobs import JobDeletion, JobSnapshot
from utteran_control import transitions
from utteran_control.errors import ControlError
from utteran_control.errors import ErrorCode as E
from utteran_control.history_repository import REQUIRED_TABLES_V7, HistoryControlStore
from utteran_control.job_repository import AcceptedBatch, AcceptedJob
from utteran_control.store import Accepted, IdempotencyScope, canonical

DELETION_SCHEMA_VERSION: Final = 8
TOMBSTONE_SNAPSHOT: Final = canonical({"b7_snapshot": "deleted", "schema_version": 1})
TOMBSTONE_DIGEST: Final = hashlib.sha256(TOMBSTONE_SNAPSHOT.encode()).hexdigest()
TOMBSTONE_EVIDENCE: Final = canonical({"b7_evidence": "deleted", "schema_version": 1})
TOMBSTONE_PRIVATE: Final = canonical({"b7_snapshot_digest": TOMBSTONE_DIGEST})
FINISHED: Final = transitions.TERMINAL | {S.INTERRUPTED}
_FINISHED_SQL: Final = "('succeeded','failed','cancelled','interrupted')"
DeletionMode = Literal["history", "history_and_artifacts"]
DeletionState = Literal["artifacts_pending", "completed"]

for _literal in (TOMBSTONE_SNAPSHOT, TOMBSTONE_DIGEST, TOMBSTONE_EVIDENCE):
    assert "'" not in _literal  # embedded verbatim in the trigger SQL below

_DDL_V8: Final = (
    "CREATE TABLE job_deletions (operation_id TEXT PRIMARY KEY REFERENCES operations(id), "
    "mode TEXT NOT NULL CHECK (mode IN ('history','history_and_artifacts')), "
    "state TEXT NOT NULL CHECK (state IN ('artifacts_pending','completed')), "
    "targets TEXT, requested_at TEXT NOT NULL, completed_at TEXT, "
    "CHECK ((state = 'completed') = (completed_at IS NOT NULL)), "
    "CHECK (state = 'artifacts_pending' OR targets IS NULL), "
    "CHECK (mode = 'history_and_artifacts' OR state = 'completed'))",
    # Only a finished B7 job can be marked; a record is never removed and a completed
    # deletion never changes again (pending -> pending/completed only, same mode).
    "CREATE TRIGGER job_deletions_finished_only BEFORE INSERT ON job_deletions "
    "WHEN (SELECT status FROM operations WHERE id = NEW.operation_id) NOT IN "
    f"{_FINISHED_SQL} OR NOT EXISTS (SELECT 1 FROM job_snapshots "
    "WHERE operation_id = NEW.operation_id) "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER job_deletions_no_delete BEFORE DELETE ON job_deletions "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER job_deletions_guarded_update BEFORE UPDATE ON job_deletions WHEN NOT ("
    "OLD.state = 'artifacts_pending' AND NEW.operation_id IS OLD.operation_id "
    "AND NEW.mode IS OLD.mode AND NEW.requested_at IS OLD.requested_at) "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    # Filled and emptied inside one deletion transaction; never committed non-empty.
    "CREATE TABLE job_deletion_gate (operation_id TEXT PRIMARY KEY "
    "REFERENCES job_deletions(operation_id))",
    "CREATE TRIGGER job_deletion_gate_finished_only BEFORE INSERT ON job_deletion_gate "
    "WHEN (SELECT status FROM operations WHERE id = NEW.operation_id) NOT IN "
    f"{_FINISHED_SQL} BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    # JobDeletion responses (their own response kind, like the other replay tables).
    "CREATE TABLE deletion_replays (installation TEXT NOT NULL, method TEXT NOT NULL, "
    "endpoint TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, "
    "status INTEGER NOT NULL, headers TEXT NOT NULL, body TEXT NOT NULL, "
    "operation_id TEXT NOT NULL REFERENCES operations(id), created_at TEXT NOT NULL, "
    "PRIMARY KEY (installation, method, endpoint, key))",
    *(
        f"CREATE TRIGGER deletion_replays_immutable_{verb} BEFORE {verb} ON deletion_replays "
        "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
        for verb in ("UPDATE", "DELETE")
    ),
    # The two narrow exceptions. BEFORE DELETE triggers of both tables stay unchanged.
    "DROP TRIGGER job_snapshots_immutable_UPDATE",
    "CREATE TRIGGER job_snapshots_guarded_update BEFORE UPDATE ON job_snapshots WHEN NOT ("
    "OLD.operation_id IN (SELECT operation_id FROM job_deletion_gate) "
    "AND (SELECT status FROM operations WHERE id = OLD.operation_id) IN "
    f"{_FINISHED_SQL} "
    "AND NEW.operation_id IS OLD.operation_id AND NEW.batch_id IS OLD.batch_id "
    "AND NEW.retry_of IS OLD.retry_of AND NEW.rerun_of IS OLD.rerun_of "
    "AND NEW.created_at IS OLD.created_at "
    f"AND NEW.body = '{TOMBSTONE_SNAPSHOT}' AND NEW.digest = '{TOMBSTONE_DIGEST}') "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "DROP TRIGGER job_results_immutable_UPDATE",
    "CREATE TRIGGER job_results_guarded_update BEFORE UPDATE ON job_results WHEN NOT ("
    "OLD.operation_id IN (SELECT operation_id FROM job_deletion_gate) "
    "AND (SELECT status FROM operations WHERE id = OLD.operation_id) IN "
    f"{_FINISHED_SQL} "
    "AND NEW.operation_id IS OLD.operation_id AND NEW.attempt_id IS OLD.attempt_id "
    "AND NEW.core_job_id IS OLD.core_job_id AND NEW.artifact_count IS OLD.artifact_count "
    "AND NEW.recorded_at IS OLD.recorded_at "
    f"AND NEW.evidence = '{TOMBSTONE_EVIDENCE}') "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
)
REQUIRED_TABLES_V8: Final = REQUIRED_TABLES_V7 | {
    "job_deletions",
    "job_deletion_gate",
    "deletion_replays",
}


@dataclass(frozen=True)
class AcceptedDeletion:
    status: int
    headers: Mapping[str, str]
    deletion: JobDeletion
    replayed: bool


@dataclass(frozen=True)
class DeletionContext:
    """Private, point-in-time facts a deletion plan is computed from (one read)."""

    job_id: str
    revision: int
    status: S
    # Finished, no queue item, no lease, no active attempt, no recovery flag.
    finished: bool
    snapshot: JobSnapshot
    # format -> (sha256, size); None without completion evidence; {} without per-file record.
    evidence: Mapping[str, tuple[str, int]] | None
    # Journal of an artifact deletion that stopped part-way, else None.
    pending: Mapping[str, Any] | None
    related: int


class DeletionControlStore(HistoryControlStore):
    """Layout v8 = v7 + deletion journal, gate and replays. One-way; v7 classes refuse v8."""

    TARGET_VERSION: ClassVar[int] = DELETION_SCHEMA_VERSION
    REPLAY_TABLES: ClassVar[tuple[str, ...]] = (
        *HistoryControlStore.REPLAY_TABLES,
        "deletion_replays",
    )
    TOMBSTONE_SNAPSHOT: ClassVar[str] = TOMBSTONE_SNAPSHOT

    def __init__(self, path: Any, **kwargs: Any) -> None:
        self._deleting = False
        super().__init__(path, **kwargs)

    @classmethod
    def _layout(cls, version: int) -> tuple[tuple[str, ...], frozenset[str]]:
        if version == DELETION_SCHEMA_VERSION:
            return _DDL_V8, REQUIRED_TABLES_V8
        return super()._layout(version)

    def _verify(self, db: sqlite3.Connection, *, version: int | None = None) -> tuple[str, bytes]:
        result = super()._verify(db, version=version)
        layout = self.TARGET_VERSION if version is None else version
        # A committed gate row means a deletion bypassed its own transaction.
        if (
            layout >= DELETION_SCHEMA_VERSION
            and db.execute("SELECT count(*) FROM job_deletion_gate").fetchone()[0]
        ):
            raise ControlError(E.STORE_CORRUPT)
        return result

    # ------------------------------------------------------------------ reads

    @staticmethod
    def _deletion_row(db: sqlite3.Connection, job_id: str) -> tuple[str, str, str | None] | None:
        row = db.execute(
            "SELECT mode, state, targets FROM job_deletions WHERE operation_id = ?", (job_id,)
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]), row[2])

    def _deleted(self, db: sqlite3.Connection, job_id: str) -> bool:
        row = self._deletion_row(db, job_id)
        return row is not None and row[1] == "completed"

    def _snapshot_row(self, db: sqlite3.Connection, job_id: str) -> tuple[Any, ...]:
        # Every job read goes through here: a tombstone is never parsed as a snapshot.
        if self._deleted(db, job_id):
            raise ControlError(E.JOB_DELETED)
        return super()._snapshot_row(db, job_id)

    def _listed_jobs(self, db: sqlite3.Connection, visibility: str | None) -> tuple[str, list[str]]:
        selected, ids = super()._listed_jobs(db, visibility)
        gone = {
            row[0]
            for row in db.execute(
                "SELECT operation_id FROM job_deletions WHERE state = 'completed'"
            ).fetchall()
        }
        return selected, [job_id for job_id in ids if job_id not in gone]

    def deletion_state(self, job_id: str) -> DeletionState | None:
        with self._transaction(write=False) as db:
            self._row(db, job_id)
            row = self._deletion_row(db, job_id)
        return None if row is None else row[1]  # type: ignore[return-value]

    @staticmethod
    def _finished(db: sqlite3.Connection, job_id: str, status: S, flagged: str | None) -> bool:
        if status not in FINISHED or flagged is not None:
            return False
        busy = db.execute(
            "SELECT (SELECT count(*) FROM queue_items WHERE operation_id = ?) "
            "+ (SELECT count(*) FROM leases WHERE operation_id = ?) "
            "+ (SELECT count(*) FROM attempts WHERE operation_id = ? AND state = 'active')",
            (job_id, job_id, job_id),
        ).fetchone()[0]
        return int(busy) == 0

    @staticmethod
    def _related(db: sqlite3.Connection, job_id: str) -> int:
        row = db.execute(
            "SELECT count(*) FROM job_snapshots s WHERE s.operation_id != :id "
            "AND s.operation_id NOT IN (SELECT operation_id FROM job_deletions "
            "WHERE state = 'completed') AND (s.retry_of = :id OR s.rerun_of = :id "
            "OR s.batch_id = (SELECT batch_id FROM job_snapshots WHERE operation_id = :id) "
            "OR s.operation_id = (SELECT retry_of FROM job_snapshots WHERE operation_id = :id) "
            "OR s.operation_id = (SELECT rerun_of FROM job_snapshots WHERE operation_id = :id))",
            {"id": job_id},
        ).fetchone()
        return int(row[0])

    def deletion_context(self, job_id: str) -> DeletionContext:
        """Private read for the deletion planner. Never published or logged."""
        with self._transaction(write=False) as db:
            kind, status, revision, flagged = self._row(db, job_id)
            if kind != "job":
                raise ControlError(E.RESOURCE_NOT_FOUND)
            snapshot = self._snapshot(db, job_id)  # 404 non-B7, 410 deleted
            row = self._deletion_row(db, job_id)
            pending = None
            if row is not None and row[1] == "artifacts_pending":
                try:
                    pending = json.loads(row[2] or "null")
                    if not isinstance(pending, dict):
                        raise ValueError
                except ValueError:
                    raise ControlError(E.STORE_CORRUPT) from None
            finished = self._finished(db, job_id, status, flagged)
            related = self._related(db, job_id)
        evidence = self.artifact_evidence(job_id) if status is S.SUCCEEDED else None
        return DeletionContext(
            job_id=job_id,
            revision=revision,
            status=status,
            finished=finished,
            snapshot=snapshot,
            evidence=evidence,
            pending=MappingProxyType(pending) if pending is not None else None,
            related=related,
        )

    # ------------------------------------------------------------------ replays

    def _job_replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> AcceptedJob | None:
        found = super()._job_replay(db, scope, digest)
        if found is not None and self._deleted(db, found.job.id):
            raise ControlError(E.JOB_DELETED)  # never re-publish a deleted job's view
        return found

    def _batch_replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> AcceptedBatch | None:
        found = super()._batch_replay(db, scope, digest)
        if found is not None and any(self._deleted(db, job.id) for job in found.batch.jobs):
            raise ControlError(E.JOB_DELETED)
        return found

    def _replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> Accepted | None:
        found = super()._replay(db, scope, digest)
        if found is not None and self._deleted(db, found.operation.id):
            raise ControlError(E.JOB_DELETED)
        return found

    def _deletion_replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> AcceptedDeletion | None:
        row = db.execute(
            "SELECT digest, status, headers, body FROM deletion_replays WHERE installation = ? "
            "AND method = 'POST' AND endpoint = ? AND key = ?",
            (self._installation, scope.endpoint, scope.key),
        ).fetchone()
        if row is None:
            self._scope_free(db, "deletion_replays", scope)
            return None
        if not hmac.compare_digest(row[0], digest):
            raise ControlError(E.IDEMPOTENCY_CONFLICT)
        try:
            deletion = JobDeletion.model_validate_json(row[3])
            headers = MappingProxyType(self._headers(row[2]))
        except (ValueError, ValidationError):
            raise ControlError(E.STORE_CORRUPT) from None
        return AcceptedDeletion(row[1], headers, deletion, True)

    def lookup_deletion(self, scope: IdempotencyScope, digest: str) -> AcceptedDeletion | None:
        """Replay lookup only; lets the service answer a lost response before any check."""
        self._validate_scope(scope)
        with self._transaction(write=False) as db:
            return self._deletion_replay(db, scope, digest)

    # ------------------------------------------------------------------ writes

    def _check_deletable(self, db: sqlite3.Connection, job_id: str, expected_revision: int) -> None:
        kind, status, revision, flagged = self._row(db, job_id)
        if kind != "job":
            raise ControlError(E.RESOURCE_NOT_FOUND)
        self._snapshot_row(db, job_id)  # 404 non-B7, 410 already deleted
        if revision != expected_revision:
            raise ControlError(E.REVISION_CONFLICT)
        if not self._finished(db, job_id, status, flagged):
            raise ControlError(E.INVALID_TRANSITION)

    def complete_deletion(
        self,
        job_id: str,
        expected_revision: int,
        mode: DeletionMode,
        scope: IdempotencyScope,
        digest: str,
        *,
        retained: Sequence[str] = (),
    ) -> AcceptedDeletion:
        """Tombstone the job's private record in ONE transaction (③ only).

        ``history_and_artifacts`` (④) is refused here too (defence in depth: no caller can
        complete it, PR #58 review). ``history`` is refused while a journal row of an
        artifact deletion exists (written by the pre-review build): its files may still
        exist or have been moved, so the record that names them is never tombstoned.
        """
        self._validate_scope(scope)
        if mode == "history_and_artifacts":
            raise ControlError(E.ARTIFACT_DELETION_DISABLED)
        if mode != "history" or type(expected_revision) is not int:
            raise ControlError(E.REQUEST_INVALID)
        self._deleting = True
        try:
            with self._transaction() as db:
                # Overwrite freed and rewritten pages of this transaction with zeros.
                db.execute("PRAGMA secure_delete=ON")
                replay = self._deletion_replay(db, scope, digest)
                if replay is not None:
                    return replay
                self._check_deletable(db, job_id, expected_revision)
                row = self._deletion_row(db, job_id)
                now = self._now()
                if row is not None:
                    raise ControlError(E.DELETION_IN_PROGRESS)
                db.execute(
                    "INSERT INTO job_deletions VALUES (?, 'history', 'completed', NULL, ?, ?)",
                    (job_id, now, now),
                )
                self._tombstone(db, job_id)
                deletion = JobDeletion.model_validate(
                    {
                        "schema_version": 1,
                        "job_id": job_id,
                        "mode": mode,
                        "outcome": "completed",
                        "deleted_at": now,
                        "artifacts_removed": 0,
                        "artifacts_remaining": 0,
                        "retained": list(retained),
                    }
                )
                headers: dict[str, str] = {"Cache-Control": "no-store"}
                db.execute(
                    "INSERT INTO deletion_replays VALUES (?,'POST',?,?,?,?,?,?,?,?)",
                    (
                        self._installation,
                        scope.endpoint,
                        scope.key,
                        digest,
                        200,
                        canonical(headers),
                        canonical(deletion.to_wire()),
                        job_id,
                        now,
                    ),
                )
                accepted = AcceptedDeletion(200, MappingProxyType(headers), deletion, False)
        finally:
            self._deleting = False
        # Listings materialized before the deletion hold the job's public view: drop them.
        self._lists.clear()
        return accepted

    @staticmethod
    def _tombstone(db: sqlite3.Connection, job_id: str) -> None:
        db.execute("INSERT INTO job_deletion_gate VALUES (?)", (job_id,))
        db.execute(
            "UPDATE job_snapshots SET body = ?, digest = ? WHERE operation_id = ?",
            (TOMBSTONE_SNAPSHOT, TOMBSTONE_DIGEST, job_id),
        )
        db.execute(
            "UPDATE job_results SET evidence = ? WHERE operation_id = ?",
            (TOMBSTONE_EVIDENCE, job_id),
        )
        db.execute(
            "UPDATE operation_private SET body = ? WHERE operation_id = ?",
            (TOMBSTONE_PRIVATE, job_id),
        )
        db.execute(
            "DELETE FROM attempt_private WHERE attempt_id IN "
            "(SELECT id FROM attempts WHERE operation_id = ?)",
            (job_id,),
        )
        db.execute("DELETE FROM job_deletion_gate WHERE operation_id = ?", (job_id,))
