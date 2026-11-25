"""E-C private re-export records: one independent SQLite file (``reexport-v1``).

Separate from B5 ``settings-v1``, B6/B7 ``control-v1`` and E-B ``worksets-v1``: nothing here
reads, writes or prunes those files, and no job, snapshot or queue row is touched. A record
holds only what is needed to report and reconcile one re-export: the source job id and
revision, the verified source digest, the presentation options, the parent directory, the
new directory name, the state and the per-format size/SHA-256 of what was written. No
transcript text and no JSON copy are stored.

Rules shared with the E-B store: reads never create the file; the first write creates it
(O_EXCL creator protocol, 0700/0600, symlinks refused); every write is one ``BEGIN
IMMEDIATE`` transaction with ``synchronous=FULL`` and ``secure_delete``; an unknown
``user_version`` or a failed ``quick_check`` is refused and the file is never reinitialized.
Limits are refused explicitly (nothing is pruned).
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
from typing import Any
from uuid import uuid4

from utteran_settings.errors import SettingsError

STORE_VERSION = 1
#: Records in the whole store / per source job (measured, e-c-reexport.md §6). At the limit a
#: new re-export is refused (``reexport_limit``); no record is removed automatically.
MAX_RECORDS = 2000
MAX_RECORDS_PER_JOB = 100
#: Canonical options of one request. Labels dominate: 256 keys (displayed labels from the
#: source) and 256 values of up to 256 characters each, 4 UTF-8 bytes at most (512 KiB),
#: plus the parent path and the envelope.
MAX_REQUEST_BYTES = 1024 * 1024
#: Sum of the stored request texts of the whole store.
MAX_STORE_REQUEST_BYTES = 64 * 1024 * 1024
_RETRYABLE = frozenset({"store_busy", "store_unavailable", "reexport_busy", "store_initializing"})
_CREATOR_POLL_SECONDS = 0.02


class ReexportError(SettingsError):
    """A fixed, value-free failure of the re-export store or API (``reexport.<code>``)."""

    namespace = "reexport"

    def __init__(self, code: str, status: int = 503, *, retryable: bool | None = None) -> None:
        super().__init__(code, status)
        self.retryable = code in _RETRYABLE if retryable is None else retryable


def _storage_error(exc: sqlite3.Error | OSError) -> ReexportError:
    number = getattr(exc, "sqlite_errorcode", 0) & 255
    code = {
        sqlite3.SQLITE_BUSY: "store_busy",
        sqlite3.SQLITE_LOCKED: "store_busy",
        sqlite3.SQLITE_FULL: "store_full",
        sqlite3.SQLITE_READONLY: "store_readonly",
        sqlite3.SQLITE_CORRUPT: "store_corrupt",
        sqlite3.SQLITE_NOTADB: "store_corrupt",
    }.get(number, "store_unavailable")
    return ReexportError(code, 507 if code == "store_full" else 503)


class _CreatorPending(Exception):
    pass


def iso(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Record:
    id: str
    job_id: str
    job_revision: int
    key: str
    digest: str
    state: str  # pending | completed | failed (interrupted is derived, see ``public_state``)
    error_code: str | None
    partial: bool
    created_ms: int
    updated_ms: int
    owner: str
    request: str
    parent: str
    directory: str | None
    source_sha256: str
    source_size: int
    files: str | None
    location_verified: bool

    def options(self) -> dict[str, Any]:
        return json.loads(self.request)  # type: ignore[no-any-return]


_COLUMNS = (
    "id, job_id, job_revision, key, digest, state, error_code, partial, created_ms, updated_ms, "
    "owner, request, parent, directory, source_sha256, source_size, files, location_verified"
)


class ReexportStore:
    """Opened per process; ``owner`` tells this process's pending records from a crashed one's."""

    factory: type[sqlite3.Connection] = sqlite3.Connection

    def __init__(
        self, path: Path, *, timeout: float = 2.0, clock: Callable[[], float] = time.time
    ) -> None:
        self.path = path
        self.timeout = timeout
        self.clock = clock
        self.owner = uuid4().hex
        self._secret: bytes | None = None

    # ---- file and transaction ------------------------------------------------------------

    def exists(self) -> bool:
        try:
            return os.path.lexists(self.path)
        except OSError:
            raise ReexportError("store_unavailable") from None

    def _check_path(self) -> None:
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ReexportError("store_unsafe_path", retryable=False)

    def create_if_missing(self) -> None:
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
                    raise self._incomplete()
                time.sleep(_CREATOR_POLL_SECONDS)
        except (sqlite3.Error, OSError) as exc:
            raise _storage_error(exc) from None

    def _incomplete(self) -> ReexportError:
        """No schema after the creator's deadline. An empty file is a creation interrupted
        between the O_EXCL create and the schema commit (PR review P2-2): it is reported with
        its own fixed code and never initialized silently (owner action, e-c-reexport.md §6.2).
        """
        try:
            empty = self.path.stat().st_size == 0
        except OSError:
            return ReexportError("store_unavailable")
        if empty:
            return ReexportError("store_incomplete", 409, retryable=False)
        return ReexportError("store_schema_unsupported", retryable=False)

    def _verify(self, created: bool = False) -> None:
        with self._transaction(write=created, initialize=created) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and created:
                self._create_schema(db)
                version = STORE_VERSION
            if version == 0:
                raise _CreatorPending
            if version != STORE_VERSION:
                raise ReexportError("store_schema_unsupported", retryable=False)
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ReexportError("store_corrupt", retryable=False)
            identity = db.execute("SELECT secret FROM installation").fetchall()
            if len(identity) != 1 or len(identity[0][0]) != 32:
                raise ReexportError("store_corrupt", retryable=False)
            self._secret = identity[0][0]

    @staticmethod
    def _create_schema(db: sqlite3.Connection) -> None:
        for statement in (
            "CREATE TABLE installation (id TEXT NOT NULL, secret BLOB NOT NULL)",
            "CREATE TABLE reexports (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, "
            "job_revision INTEGER NOT NULL, key TEXT NOT NULL UNIQUE, digest TEXT NOT NULL, "
            "state TEXT NOT NULL CHECK (state IN ('pending','completed','failed')), "
            "error_code TEXT, partial INTEGER NOT NULL, created_ms INTEGER NOT NULL, "
            "updated_ms INTEGER NOT NULL, owner TEXT NOT NULL, request TEXT NOT NULL, "
            "parent TEXT NOT NULL, directory TEXT, source_sha256 TEXT NOT NULL, "
            "source_size INTEGER NOT NULL, files TEXT, location_verified INTEGER NOT NULL)",
            "CREATE INDEX reexports_job ON reexports(job_id, created_ms)",
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
            uri = self.path.resolve().as_uri() + "?mode=rw"
            db = sqlite3.connect(
                uri, uri=True, timeout=self.timeout, isolation_level=None, factory=self.factory
            )
            if initialize:
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
        self._load()
        with self._transaction(write=write) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != STORE_VERSION:
                raise ReexportError("store_schema_unsupported", retryable=False)
            yield db

    # ---- records --------------------------------------------------------------------------

    @staticmethod
    def _record(values: tuple[Any, ...]) -> Record:
        try:
            record = Record(*values)
            if record.state not in ("pending", "completed", "failed"):
                raise ValueError
            json.loads(record.request)
            if record.files is not None:
                json.loads(record.files)
            return Record(
                **{
                    **record.__dict__,
                    "partial": bool(record.partial),
                    "location_verified": bool(record.location_verified),
                }
            )
        except (TypeError, ValueError):
            raise ReexportError("store_corrupt", retryable=False) from None

    def _load(self) -> None:
        if self._secret is not None:
            return
        try:
            self._verify()
        except _CreatorPending:
            # A creator is still committing the schema, or was killed before it did: a fixed,
            # retryable code (never a raw exception, never an initialization by a reader).
            incomplete = self._incomplete()
            if incomplete.code == "store_incomplete":
                raise ReexportError("store_initializing", 503) from None
            raise incomplete from None

    def digest(self, value: Any) -> str:
        self._load()
        assert self._secret is not None
        return hmac.new(self._secret, canonical(value).encode("utf-8"), hashlib.sha256).hexdigest()

    def by_key(self, key: str) -> Record | None:
        if not self.exists():
            return None
        with self._open(write=False) as db:
            row = db.execute(f"SELECT {_COLUMNS} FROM reexports WHERE key = ?", (key,)).fetchone()
        return None if row is None else self._record(row)

    def get(self, reexport_id: str) -> Record | None:
        if not self.exists():
            return None
        with self._open(write=False) as db:
            row = db.execute(
                f"SELECT {_COLUMNS} FROM reexports WHERE id = ?", (reexport_id,)
            ).fetchone()
        return None if row is None else self._record(row)

    def for_job(self, job_id: str) -> list[Record]:
        if not self.exists():
            return []
        with self._open(write=False) as db:
            rows = db.execute(
                f"SELECT {_COLUMNS} FROM reexports WHERE job_id = ? "
                "ORDER BY created_ms DESC, id DESC",
                (job_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def usage(self) -> dict[str, int]:
        if not self.exists():
            return {"records": 0, "request_bytes": 0}
        with self._open(write=False) as db:
            count, size = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(request AS BLOB))), 0) FROM reexports"
            ).fetchone()
        return {"records": int(count), "request_bytes": int(size)}

    def begin(
        self,
        *,
        job_id: str,
        job_revision: int,
        key: str,
        digest: str,
        request: str,
        parent: str,
        source_sha256: str,
        source_size: int,
    ) -> tuple[Record, bool]:
        """Insert the ``pending`` record (before any file exists), or return the record that
        already holds ``key`` (``created`` False). Same key, other request: 409."""
        if len(request.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ReexportError("request_too_large", 413, retryable=False)
        self.create_if_missing()
        now = int(self.clock() * 1000)
        with self._open(write=True) as db:
            row = db.execute(f"SELECT {_COLUMNS} FROM reexports WHERE key = ?", (key,)).fetchone()
            if row is not None:
                existing = self._record(row)
                if not hmac.compare_digest(existing.digest, digest):
                    raise ReexportError("idempotency_conflict", 409, retryable=False)
                return existing, False
            total, size = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(request AS BLOB))), 0) FROM reexports"
            ).fetchone()
            per_job = db.execute(
                "SELECT COUNT(*) FROM reexports WHERE job_id = ?", (job_id,)
            ).fetchone()[0]
            if total >= MAX_RECORDS or per_job >= MAX_RECORDS_PER_JOB:
                raise ReexportError("reexport_limit", 409, retryable=False)
            if size + len(request.encode("utf-8")) > MAX_STORE_REQUEST_BYTES:
                raise ReexportError("store_quota_exceeded", 413, retryable=False)
            identifier = "rex_" + uuid4().hex
            db.execute(
                f"INSERT INTO reexports ({_COLUMNS}) VALUES "
                "(?,?,?,?,?,'pending',NULL,0,?,?,?,?,?,NULL,?,?,NULL,0)",
                (
                    identifier,
                    job_id,
                    job_revision,
                    key,
                    digest,
                    now,
                    now,
                    self.owner,
                    request,
                    parent,
                    source_sha256,
                    source_size,
                ),
            )
            row = db.execute(
                f"SELECT {_COLUMNS} FROM reexports WHERE id = ?", (identifier,)
            ).fetchone()
        return self._record(row), True

    def _update(self, reexport_id: str, sql: str, values: tuple[Any, ...]) -> Record:
        now = int(self.clock() * 1000)
        with self._open(write=True) as db:
            changed = db.execute(
                f"UPDATE reexports SET {sql}, updated_ms = ? "
                "WHERE id = ? AND state = 'pending' AND owner = ?",
                (*values, now, reexport_id, self.owner),
            ).rowcount
            if changed != 1:
                raise ReexportError("store_corrupt", retryable=False)
            row = db.execute(
                f"SELECT {_COLUMNS} FROM reexports WHERE id = ?", (reexport_id,)
            ).fetchone()
        return self._record(row)

    def claim(self, reexport_id: str, directory: str) -> Record:
        """The new directory exists (and is still empty): record its name before writing."""
        return self._update(reexport_id, "directory = ?", (directory,))

    def complete(
        self, reexport_id: str, files: list[dict[str, Any]], location_verified: bool
    ) -> Record:
        return self._update(
            reexport_id,
            "state = 'completed', files = ?, location_verified = ?",
            (canonical(files), int(location_verified)),
        )

    def fail(self, reexport_id: str, code: str, *, partial: bool) -> Record:
        return self._update(
            reexport_id, "state = 'failed', error_code = ?, partial = ?", (code, int(partial))
        )

    def physical(self) -> dict[str, int]:
        """Diagnostics for tests and the measurement tool (never exposed over HTTP)."""
        with self._open(write=False) as db:
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            free = db.execute("PRAGMA freelist_count").fetchone()[0]
            size = db.execute("PRAGMA page_size").fetchone()[0]
        return {"pages": pages, "free_pages": free, "page_size": size}
