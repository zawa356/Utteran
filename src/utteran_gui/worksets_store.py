"""E-B private worksets store: one independent SQLite file, latest-state upsert with CAS.

Separate from B5 ``settings-v1/resources.sqlite3`` and B6/B7 ``control-v1/operations.sqlite3``
(nothing here reads, writes or prunes them). Rules:

- Reads never write: a missing store file is reported as ``exists: false`` and is created only
  by the explicit first-run ``bootstrap`` (the same O_EXCL creator protocol as B5).
- Every write is one ``BEGIN IMMEDIATE`` transaction with ``synchronous=FULL`` (DELETE journal,
  so no WAL sidecars stay behind); a crash before commit leaves the previous state byte for
  byte. Any SQLite/OS failure rolls back and maps to a fixed, value-free code.
- An unknown ``user_version``, a failed ``quick_check`` or an unreadable record is refused
  (``store_schema_unsupported`` / ``store_corrupt``); the file is never reinitialized.
- ``secure_delete`` and incremental auto-vacuum keep deleted private values out of free pages
  and return them to the file system, so repeated upserts do not grow the file linearly.
- Updates replace the current state. A recovery checkpoint (the previous state) is kept only
  when the newest one is older than ``CHECKPOINT_INTERVAL_SECONDS`` (or before an explicit
  restore), at most ``RECOVERY_GENERATIONS`` per workset and within ``RECOVERY_BUDGET_BYTES``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from utteran_gui.worksets_model import (
    CHECKPOINT_INTERVAL_SECONDS,
    MAX_INPUTS,
    MAX_NAME,
    MAX_STATE_BYTES,
    MAX_STORE_STATE_BYTES,
    MAX_WORKSETS,
    RECOVERY_BUDGET_BYTES,
    RECOVERY_GENERATIONS,
    WorksetState,
    canonical,
    empty_state,
    wire_state,
)
from utteran_settings.errors import SettingsError

STORE_VERSION = 1
MAX_REPLAYS = 64
Mode = Literal["normal", "expert"]
_RETRYABLE = frozenset({"store_busy", "store_unavailable"})
_CREATOR_POLL_SECONDS = 0.02


class WorksetError(SettingsError):
    """A fixed, value-free failure of the worksets store or API (``worksets.<code>``)."""

    namespace = "worksets"

    def __init__(self, code: str, status: int = 503, *, retryable: bool | None = None) -> None:
        super().__init__(code, status)
        self.retryable = code in _RETRYABLE if retryable is None else retryable


def _storage_error(exc: sqlite3.Error | OSError) -> WorksetError:
    number = getattr(exc, "sqlite_errorcode", 0) & 255
    code = {
        sqlite3.SQLITE_BUSY: "store_busy",
        sqlite3.SQLITE_LOCKED: "store_busy",
        sqlite3.SQLITE_FULL: "store_full",
        sqlite3.SQLITE_READONLY: "store_readonly",
        sqlite3.SQLITE_CORRUPT: "store_corrupt",
        sqlite3.SQLITE_NOTADB: "store_corrupt",
    }.get(number, "store_unavailable")
    return WorksetError(code, 507 if code == "store_full" else 503)


class _CreatorPending(Exception):
    pass


def _iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat().replace("+00:00", "Z")


def check_name(name: Any) -> str:
    if not isinstance(name, str):
        raise WorksetError("name_invalid", 422, retryable=False)
    trimmed = name.strip()
    if not trimmed or len(trimmed) > MAX_NAME or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise WorksetError("name_invalid", 422, retryable=False)
    return trimmed


def check_state(value: Any) -> tuple[str, int]:
    """Validate one state; return its canonical text and UTF-8 size (refused above the limit)."""
    try:
        state = WorksetState.model_validate(value)
    except ValidationError:
        raise WorksetError("state_invalid", 422, retryable=False) from None
    text = canonical(wire_state(state))
    size = len(text.encode("utf-8"))
    if size > MAX_STATE_BYTES:
        raise WorksetError("state_too_large", 413, retryable=False)
    return text, size


@dataclass(frozen=True)
class Row:
    id: str
    revision: int
    name: str
    created_ms: int
    updated_ms: int
    state: str
    state_bytes: int

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "revision": self.revision,
            "created_at": _iso(self.created_ms),
            "updated_at": _iso(self.updated_ms),
            "state_bytes": self.state_bytes,
            "input_count": len(json.loads(self.state)["inputs"]),
        }

    def to_wire(self) -> dict[str, Any]:
        return {"schema_version": 1, **self.summary(), "state": json.loads(self.state)}


class WorksetStore:
    """Opened per process on first use; every operation is its own short transaction."""

    #: Connection class (tests and the measurement tool observe commits through a subclass).
    factory: type[sqlite3.Connection] = sqlite3.Connection

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = 2.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.clock = clock
        self._secret: bytes | None = None

    # ---- file and transaction ------------------------------------------------------------

    def exists(self) -> bool:
        try:
            return os.path.lexists(self.path)
        except OSError:
            raise WorksetError("store_unavailable") from None

    def _check_path(self) -> None:
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise WorksetError("store_unsafe_path", retryable=False)

    def create_if_missing(self) -> None:
        """The only file creation (first-run bootstrap). Never touches an existing file."""
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._check_path()
            if os.name != "nt":
                self.path.parent.chmod(0o700)
            created = False
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                created = True
            except FileExistsError:
                pass
            if os.name != "nt":
                self.path.chmod(0o600)
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._verify(created)
                    return
                except _CreatorPending:
                    pass
                if time.monotonic() >= deadline:
                    raise WorksetError("store_schema_unsupported", retryable=False)
                time.sleep(_CREATOR_POLL_SECONDS)
        except (sqlite3.Error, OSError) as exc:
            raise _storage_error(exc) from None

    def _verify(self, created: bool = False) -> None:
        with self._transaction(write=created, initialize=created) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and created:
                self._create_schema(db)
                version = STORE_VERSION
            if version == 0:
                raise _CreatorPending
            if version != STORE_VERSION:
                raise WorksetError("store_schema_unsupported", retryable=False)
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise WorksetError("store_corrupt", retryable=False)
            identity = db.execute("SELECT secret FROM installation").fetchall()
            if len(identity) != 1 or len(identity[0][0]) != 32:
                raise WorksetError("store_corrupt", retryable=False)
            self._secret = identity[0][0]

    @staticmethod
    def _create_schema(db: sqlite3.Connection) -> None:
        for statement in (
            "CREATE TABLE installation (id TEXT NOT NULL, secret BLOB NOT NULL)",
            "CREATE TABLE worksets (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
            "name TEXT NOT NULL, created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL, "
            "ordinal INTEGER NOT NULL, state TEXT NOT NULL, state_bytes INTEGER NOT NULL)",
            "CREATE TABLE recovery (workset_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "name TEXT NOT NULL, saved_ms INTEGER NOT NULL, state TEXT NOT NULL, "
            "state_bytes INTEGER NOT NULL, PRIMARY KEY(workset_id, revision))",
            "CREATE TABLE active (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
            "workset_id TEXT NOT NULL, mode TEXT NOT NULL, revision INTEGER NOT NULL)",
            "CREATE TABLE replays (key TEXT PRIMARY KEY, digest TEXT NOT NULL, "
            "status INTEGER NOT NULL, body TEXT NOT NULL, created_ms INTEGER NOT NULL)",
        ):
            db.execute(statement)
        db.execute("INSERT INTO installation VALUES (?,?)", (uuid4().hex, secrets.token_bytes(32)))
        db.execute(f"PRAGMA user_version={STORE_VERSION}")

    @contextmanager
    def _transaction(
        self, *, write: bool = True, initialize: bool = False
    ) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            # mode=rw: a store file that disappeared is an error, never silently recreated.
            uri = self.path.resolve().as_uri() + "?mode=rw"
            db = sqlite3.connect(
                uri, uri=True, timeout=self.timeout, isolation_level=None, factory=self.factory
            )
            if initialize:
                # Part of the header of a still-empty file: written by the creator's first
                # CREATE TABLE in the same transaction (a rollback leaves the file empty).
                db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except (sqlite3.Error, OSError) as exc:
            if db is not None:
                db.rollback()
            raise _storage_error(exc) from None
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    @contextmanager
    def _open(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if self._secret is None:
            self._verify()
        with self._transaction(write=write) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != STORE_VERSION:
                raise WorksetError("store_schema_unsupported", retryable=False)
            yield db

    # ---- reading ---------------------------------------------------------------------------

    @staticmethod
    def _row(values: tuple[Any, ...]) -> Row:
        row = Row(*values)
        try:
            WorksetState.model_validate_json(row.state)
        except ValidationError:
            raise WorksetError("store_corrupt", retryable=False) from None
        return row

    def _rows(self, db: sqlite3.Connection) -> list[Row]:
        return [
            self._row(values)
            for values in db.execute(
                "SELECT id,revision,name,created_ms,updated_ms,state,state_bytes "
                "FROM worksets ORDER BY ordinal, id"
            ).fetchall()
        ]

    def _get(self, db: sqlite3.Connection, workset_id: str) -> Row:
        values = db.execute(
            "SELECT id,revision,name,created_ms,updated_ms,state,state_bytes "
            "FROM worksets WHERE id=?",
            (workset_id,),
        ).fetchone()
        if values is None:
            raise WorksetError("workset_not_found", 404, retryable=False)
        return self._row(values)

    @staticmethod
    def _active(db: sqlite3.Connection) -> dict[str, Any] | None:
        row = db.execute("SELECT workset_id,mode,revision FROM active").fetchone()
        if row is None:
            return None
        if row[1] not in ("normal", "expert"):
            raise WorksetError("store_corrupt", retryable=False)
        return {"workset_id": row[0], "view": {"mode": row[1]}, "revision": row[2]}

    def _listing(self, db: sqlite3.Connection) -> dict[str, Any]:
        rows = self._rows(db)
        active = self._active(db)
        if rows and (active is None or all(r.id != active["workset_id"] for r in rows)):
            raise WorksetError("store_corrupt", retryable=False)
        return {
            "schema_version": 1,
            "exists": True,
            "items": [row.summary() for row in rows],
            "active": active,
            "limits": limits(),
        }

    def list(self) -> dict[str, Any]:
        if not self.exists():
            return {
                "schema_version": 1,
                "exists": False,
                "items": [],
                "active": None,
                "limits": limits(),
            }
        with self._open(write=False) as db:
            return self._listing(db)

    def get(self, workset_id: str) -> Row:
        if not self.exists():
            raise WorksetError("workset_not_found", 404, retryable=False)
        with self._open(write=False) as db:
            return self._get(db, workset_id)

    # ---- writing ---------------------------------------------------------------------------

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    @staticmethod
    def _quota(db: sqlite3.Connection, size: int, replacing: str | None = None) -> None:
        others = db.execute(
            "SELECT coalesce(sum(state_bytes),0) FROM worksets WHERE id IS NOT ?", (replacing,)
        ).fetchone()[0]
        if others + size > MAX_STORE_STATE_BYTES:
            raise WorksetError("store_quota_exceeded", 413, retryable=False)

    def _insert(self, db: sqlite3.Connection, name: str, text: str, size: int) -> Row:
        count = db.execute("SELECT count(*) FROM worksets").fetchone()[0]
        if count >= MAX_WORKSETS:
            raise WorksetError("workset_limit", 409, retryable=False)
        self._quota(db, size)
        ordinal = db.execute("SELECT coalesce(max(ordinal), 0) + 1 FROM worksets").fetchone()[0]
        now = self._now_ms()
        row = Row("ws_" + uuid4().hex, 1, name, now, now, text, size)
        db.execute(
            "INSERT INTO worksets VALUES (?,?,?,?,?,?,?,?)",
            (row.id, 1, name, now, now, ordinal, text, size),
        )
        return row

    def bootstrap(self, name: Any) -> dict[str, Any]:
        """First run: create the store and exactly one default workset (idempotent, race-free)."""
        name = check_name(name)
        self.create_if_missing()
        with self._open(write=True) as db:
            if db.execute("SELECT count(*) FROM worksets").fetchone()[0] == 0:
                text, size = check_state(empty_state())
                row = self._insert(db, name, text, size)
                db.execute("DELETE FROM active")
                db.execute("INSERT INTO active VALUES (1,?,?,1)", (row.id, "normal"))
            return self._listing(db)

    def create(self, key: str, name: Any, state: Any) -> tuple[int, Row]:
        """Explicit new workset (Idempotency-Key: same key + same body = same answer, once)."""
        body = {"name": name, "state": state}
        if not self.exists():
            raise WorksetError("store_not_initialized", 409, retryable=False)
        with self._open(write=True) as db:
            assert self._secret is not None
            digest = hmac.new(self._secret, canonical(body).encode(), hashlib.sha256).hexdigest()
            replay = db.execute(
                "SELECT digest,status,body FROM replays WHERE key=?", (key,)
            ).fetchone()
            if replay is not None:
                if not hmac.compare_digest(replay[0], digest):
                    raise WorksetError("idempotency_conflict", 409, retryable=False)
                created_id = replay[2]
                try:
                    return replay[1], self._get(db, created_id)
                except WorksetError as exc:
                    if exc.status == 404:
                        # The created workset was deleted later; the replay still answers once.
                        raise WorksetError("workset_deleted", 410, retryable=False) from None
                    raise
            text, size = check_state(empty_state() if state is None else state)
            row = self._insert(db, check_name(name), text, size)
            db.execute(
                "INSERT INTO replays VALUES (?,?,?,?,?)", (key, digest, 201, row.id, self._now_ms())
            )
            db.execute(
                "DELETE FROM replays WHERE key NOT IN "
                "(SELECT key FROM replays ORDER BY created_ms DESC, key LIMIT ?)",
                (MAX_REPLAYS,),
            )
            return 201, row

    def _checkpoint(self, db: sqlite3.Connection, current: Row, *, force: bool) -> None:
        newest = db.execute(
            "SELECT max(saved_ms) FROM recovery WHERE workset_id=?", (current.id,)
        ).fetchone()[0]
        due = newest is None or self._now_ms() - newest >= CHECKPOINT_INTERVAL_SECONDS * 1000
        if not (force or due):
            return
        db.execute(
            "INSERT OR REPLACE INTO recovery VALUES (?,?,?,?,?,?)",
            (
                current.id,
                current.revision,
                current.name,
                current.updated_ms,
                current.state,
                current.state_bytes,
            ),
        )
        db.execute(
            "DELETE FROM recovery WHERE workset_id=? AND revision NOT IN "
            "(SELECT revision FROM recovery WHERE workset_id=? ORDER BY revision DESC LIMIT ?)",
            (current.id, current.id, RECOVERY_GENERATIONS),
        )
        # Store-wide recovery budget: drop the oldest checkpoints of this store only.
        while (
            db.execute("SELECT coalesce(sum(state_bytes),0) FROM recovery").fetchone()[0]
            > RECOVERY_BUDGET_BYTES
        ):
            db.execute(
                "DELETE FROM recovery WHERE rowid = "
                "(SELECT rowid FROM recovery ORDER BY saved_ms, workset_id, revision LIMIT 1)"
            )

    @staticmethod
    def _compact(db: sqlite3.Connection, *, always: bool = False) -> None:
        """Return free pages of this store to the file system, inside the same transaction.

        Autosave reuses free pages, so they are released only when they exceed a quarter of
        the file (and 1 MiB), or after a deletion. Python steps a pragma once per execute(),
        and each step of ``incremental_vacuum`` frees one page; the loop is bounded.
        """
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        total = db.execute("PRAGMA page_count").fetchone()[0]
        if free == 0 or not (always or free > max(256, total // 4)):
            return
        for _ in range(free):
            db.execute("PRAGMA incremental_vacuum(1)")
            if db.execute("PRAGMA freelist_count").fetchone()[0] == 0:
                break

    def update(self, workset_id: str, expected: int, name: Any, state: Any) -> Row:
        """CAS replace of name + state. An identical body changes nothing (no new revision)."""
        trimmed = check_name(name)
        text, size = check_state(state)
        if not self.exists():
            raise WorksetError("workset_not_found", 404, retryable=False)
        with self._open(write=True) as db:
            current = self._get(db, workset_id)
            if current.revision != expected:
                raise WorksetError("revision_conflict", 412, retryable=False)
            if current.name == trimmed and current.state == text:
                return current
            self._quota(db, size, workset_id)
            self._checkpoint(db, current, force=False)
            now = max(self._now_ms(), current.updated_ms)
            db.execute(
                "UPDATE worksets SET revision=?,name=?,updated_ms=?,state=?,state_bytes=? "
                "WHERE id=?",
                (current.revision + 1, trimmed, now, text, size, workset_id),
            )
            self._compact(db)
            return Row(
                workset_id, current.revision + 1, trimmed, current.created_ms, now, text, size
            )

    def delete(self, workset_id: str, expected: int) -> dict[str, Any]:
        """Remove one workset and its recovery checkpoints (never inputs, outputs or B7 data)."""
        if not self.exists():
            raise WorksetError("workset_not_found", 404, retryable=False)
        with self._open(write=True) as db:
            current = self._get(db, workset_id)
            if current.revision != expected:
                raise WorksetError("revision_conflict", 412, retryable=False)
            if db.execute("SELECT count(*) FROM worksets").fetchone()[0] <= 1:
                raise WorksetError("workset_last", 409, retryable=False)
            db.execute("DELETE FROM worksets WHERE id=?", (workset_id,))
            db.execute("DELETE FROM recovery WHERE workset_id=?", (workset_id,))
            active = self._active(db)
            if active is not None and active["workset_id"] == workset_id:
                successor = db.execute(
                    "SELECT id FROM worksets ORDER BY ordinal, id LIMIT 1"
                ).fetchone()[0]
                db.execute("UPDATE active SET workset_id=?, revision=revision+1", (successor,))
            self._compact(db, always=True)
            return self._listing(db)

    def set_active(self, workset_id: str, mode: Any) -> dict[str, Any]:
        """Remember the last chosen workset and display mode (absolute value, safe to resend)."""
        if mode not in ("normal", "expert"):
            raise WorksetError("mode_invalid", 422, retryable=False)
        if not self.exists():
            raise WorksetError("workset_not_found", 404, retryable=False)
        with self._open(write=True) as db:
            self._get(db, workset_id)
            active = self._active(db)
            if active is None:
                db.execute("INSERT INTO active VALUES (1,?,?,1)", (workset_id, mode))
            elif active["workset_id"] != workset_id or active["view"]["mode"] != mode:
                db.execute(
                    "UPDATE active SET workset_id=?, mode=?, revision=revision+1",
                    (workset_id, mode),
                )
            result = self._active(db)
            assert result is not None
            return {"schema_version": 1, **result}

    def recovery(self, workset_id: str) -> dict[str, Any]:
        if not self.exists():
            raise WorksetError("workset_not_found", 404, retryable=False)
        with self._open(write=False) as db:
            self._get(db, workset_id)
            rows = db.execute(
                "SELECT revision,name,saved_ms,state,state_bytes FROM recovery "
                "WHERE workset_id=? ORDER BY revision DESC",
                (workset_id,),
            ).fetchall()
            items = []
            for revision, name, saved_ms, state, size in rows:
                try:
                    count = len(WorksetState.model_validate_json(state).inputs)
                except ValidationError:
                    raise WorksetError("store_corrupt", retryable=False) from None
                items.append(
                    {
                        "revision": revision,
                        "name": name,
                        "saved_at": _iso(saved_ms),
                        "state_bytes": size,
                        "input_count": count,
                    }
                )
            return {"schema_version": 1, "workset_id": workset_id, "items": items}

    def restore_recovery(self, workset_id: str, revision: int, expected: int) -> Row:
        """Make one recovery checkpoint the current state (CAS); the replaced state is kept."""
        if not self.exists():
            raise WorksetError("workset_not_found", 404, retryable=False)
        with self._open(write=True) as db:
            current = self._get(db, workset_id)
            if current.revision != expected:
                raise WorksetError("revision_conflict", 412, retryable=False)
            source = db.execute(
                "SELECT name,state,state_bytes FROM recovery WHERE workset_id=? AND revision=?",
                (workset_id, revision),
            ).fetchone()
            if source is None:
                raise WorksetError("recovery_not_found", 404, retryable=False)
            name, text, size = source
            try:
                WorksetState.model_validate_json(text)
            except ValidationError:
                raise WorksetError("store_corrupt", retryable=False) from None
            self._quota(db, size, workset_id)
            self._checkpoint(db, current, force=True)
            now = max(self._now_ms(), current.updated_ms)
            db.execute(
                "UPDATE worksets SET revision=?,name=?,updated_ms=?,state=?,state_bytes=? "
                "WHERE id=?",
                (current.revision + 1, name, now, text, size, workset_id),
            )
            self._compact(db)
            return Row(workset_id, current.revision + 1, name, current.created_ms, now, text, size)

    # ---- operations ------------------------------------------------------------------------

    def backup(self, destination: Path) -> None:
        """Consistent online copy through the SQLite backup API (never a raw file copy)."""
        with self._open(write=False) as db:
            target = sqlite3.connect(destination)
            try:
                db.backup(target)
            finally:
                target.close()
        if os.name != "nt":
            destination.chmod(0o600)

    def physical(self) -> dict[str, int]:
        """Diagnostics for tests and measurements only (no values)."""
        with self._open(write=False) as db:
            return {
                "page_size": db.execute("PRAGMA page_size").fetchone()[0],
                "page_count": db.execute("PRAGMA page_count").fetchone()[0],
                "freelist_count": db.execute("PRAGMA freelist_count").fetchone()[0],
                "file_bytes": self.path.stat().st_size,
                "recovery_rows": db.execute("SELECT count(*) FROM recovery").fetchone()[0],
                "replay_rows": db.execute("SELECT count(*) FROM replays").fetchone()[0],
            }


def limits() -> dict[str, int]:
    return {
        "max_worksets": MAX_WORKSETS,
        "max_inputs": MAX_INPUTS,
        "max_state_bytes": MAX_STATE_BYTES,
        "max_store_state_bytes": MAX_STORE_STATE_BYTES,
        "recovery_generations": RECOVERY_GENERATIONS,
    }
