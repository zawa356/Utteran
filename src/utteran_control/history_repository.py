"""Layout v7: per-job history visibility (hide / show), additive to v6.

Hiding a job is a presentation attribute of the history list, not a state of the
operation: it never changes the job's status, revision, snapshot, attempts, replays or
the outbox, and nothing is deleted. It lives in its own small table with its own
revision (CAS), so every existing reader keeps seeing exactly the rows it saw in v6.

Consistency model (docs/architecture/b7/history-visibility.md):
- CAS: ``If-Match`` on the *visibility* revision (0 = never set), independent of the job
  revision. A request for the state that is already stored is a no-op (idempotent PUT).
- Outbox: no event is written. The history list is read with GET; SSE/sync carry
  operation state only, exactly as before.
"""

from __future__ import annotations

import sqlite3
from typing import ClassVar, Final, Literal

from utteran_contracts.control import Status as S
from utteran_contracts.jobs import JobVisibility
from utteran_control import transitions
from utteran_control.errors import ControlError
from utteran_control.errors import ErrorCode as E
from utteran_control.events_repository import (
    REQUIRED_TABLES_V6,
    ManagedControlStore,
)

HISTORY_SCHEMA_VERSION: Final = 7
VisibilityFilter = Literal["visible", "hidden", "all"]

_DDL_V7: Final = (
    # Mutable side table (like job_state). Existing tables, rows and triggers are untouched.
    "CREATE TABLE job_visibility (operation_id TEXT PRIMARY KEY REFERENCES operations(id), "
    "hidden INTEGER NOT NULL CHECK (hidden IN (0,1)), "
    "revision INTEGER NOT NULL CHECK (revision >= 1), updated_at TEXT NOT NULL)",
    # Hide/show only: a row is never removed, so a revision can never be reused.
    "CREATE TRIGGER job_visibility_no_delete BEFORE DELETE ON job_visibility "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
)
REQUIRED_TABLES_V7: Final = REQUIRED_TABLES_V6 | {"job_visibility"}
# Only finished jobs are history; a waiting or running job stays visible.
HIDEABLE: Final = transitions.TERMINAL | {S.INTERRUPTED}


class HistoryControlStore(ManagedControlStore):
    """Layout v7 = v6 + ``job_visibility``. One-way migration; v6 classes refuse v7."""

    TARGET_VERSION: ClassVar[int] = HISTORY_SCHEMA_VERSION

    @classmethod
    def _layout(cls, version: int) -> tuple[tuple[str, ...], frozenset[str]]:
        if version == HISTORY_SCHEMA_VERSION:
            return _DDL_V7, REQUIRED_TABLES_V7
        return super()._layout(version)

    def _visibility(self, db: sqlite3.Connection, job_id: str) -> JobVisibility:
        self._snapshot_row(db, job_id)  # 404 for anything that is not a B7 job
        row = db.execute(
            "SELECT hidden, revision, updated_at FROM job_visibility WHERE operation_id = ?",
            (job_id,),
        ).fetchone()
        return JobVisibility.model_validate(
            {
                "schema_version": 1,
                "job_id": job_id,
                "hidden": bool(row[0]) if row else False,
                "revision": int(row[1]) if row else 0,
                "updated_at": row[2] if row else None,
            }
        )

    def visibility(self, job_id: str) -> JobVisibility:
        with self._transaction(write=False) as db:
            return self._visibility(db, job_id)

    def set_visibility(self, job_id: str, expected_revision: int, hidden: bool) -> JobVisibility:
        """Hide or show one finished job in the history list. Nothing else changes.

        Asking for the stored state returns it unchanged whatever ``expected_revision``
        is (safe to repeat after an unknown outcome). A real change needs the current
        visibility revision (412 otherwise) and, for hiding, a finished job (409).
        """
        if type(hidden) is not bool or type(expected_revision) is not int:
            raise ControlError(E.REQUEST_INVALID)
        with self._transaction() as db:
            current = self._visibility(db, job_id)
            if current.hidden == hidden:
                return current
            if current.revision != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            if hidden and self._row(db, job_id)[1] not in HIDEABLE:
                raise ControlError(E.INVALID_TRANSITION)
            db.execute(
                "INSERT INTO job_visibility VALUES (?,?,?,?) ON CONFLICT(operation_id) DO "
                "UPDATE SET hidden = excluded.hidden, revision = excluded.revision, "
                "updated_at = excluded.updated_at",
                (job_id, int(hidden), current.revision + 1, self._now()),
            )
            return self._visibility(db, job_id)

    def _listed_jobs(self, db: sqlite3.Connection, visibility: str | None) -> tuple[str, list[str]]:
        selected = "visible" if visibility is None else visibility
        if selected not in ("visible", "hidden", "all"):
            raise ControlError(E.REQUEST_INVALID)
        clause = {
            "all": "",
            "hidden": "WHERE operation_id IN (SELECT operation_id FROM job_visibility "
            "WHERE hidden = 1) ",
            "visible": "WHERE operation_id NOT IN (SELECT operation_id FROM job_visibility "
            "WHERE hidden = 1) ",
        }[selected]
        rows = db.execute(
            f"SELECT operation_id FROM job_snapshots {clause}ORDER BY rowid"
        ).fetchall()
        return selected, [row[0] for row in rows]
