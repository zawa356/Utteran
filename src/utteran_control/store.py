"""SQLite control store: the only source of truth for B6 operation records.

Every accepted mutation commits resource state, queue metadata, revisions, the replay
record and its outbox events in one ``BEGIN IMMEDIATE`` transaction. Nothing is published
before commit, and nothing here dispatches work, starts processes or serves HTTP.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal

from pydantic import ValidationError

from utteran_contracts.control import (
    AttemptView,
    ControlSnapshot,
    DispatchState,
    EventEnvelope,
    EventPage,
    EventPayload,
    LeaseGrant,
    OperationStatus,
    OperationView,
    OwnerIdentity,
    PrivateOperation,
    ProcessIdentity,
    QueueItemView,
    QueueView,
    ReasonCode,
    RecoveryReport,
    SubmitOperation,
    TransitionRequest,
)
from utteran_control import transitions
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.recovery import (
    Liveness,
    Outcome,
    ProcessVerifier,
    UnknownVerifier,
    decide,
    safe_check,
)

S = OperationStatus
R = ReasonCode
E = ErrorCode

# SQLite PRAGMA user_version (storage layout), not the wire DTO ``schema_version``.
# v1: initial B6 layout (PR #28 a037857). v2: adds ``queue_replays`` (additive migration).
# v3 (B7 ``JobControlStore`` only) adds job snapshot / batch tables; the plain B6 store
# keeps targeting v2 and refuses a v3 file, so B6-only code never misreads B7 jobs.
SCHEMA_VERSION: Final = 2
DEFAULT_TIMEOUT: Final = 2.0
MAX_REQUEST_BYTES: Final = 256 * 1024
MAX_PAYLOAD_DEPTH: Final = 32
MAX_EVENT_PAGE: Final = 500
QUEUE_RESOURCE_ID: Final = "queue"
# Idempotency-Key: Phase A length 16..128; B6 additionally limits it to visible ASCII.
KEY_PATTERN: Final = re.compile(r"[\x21-\x7e]{16,128}")
ENDPOINT_PATTERN: Final = re.compile(r"/api/v1(/[a-z0-9_-]{1,64}){1,6}")
CURSOR_RE: Final = re.compile(r"([0-9a-f]{32}):(0|[1-9][0-9]{0,18})")
# Credentials are never persisted, even inside the private payload.
FORBIDDEN_KEY: Final = re.compile(
    r"token|secret|passw(or)?d|api[_-]?key|authori[sz]ation|cookie|credential|bearer",
    re.IGNORECASE,
)
FORBIDDEN_VALUE: Final = re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")
ID_PREFIX: Final = MappingProxyType(
    {"job": "job_", "setup_operation": "setup_", "refresh_operation": "refresh_"}
)
# Reason codes that only the store itself (recovery / lease) may write.
RESERVED_REASONS: Final = frozenset(
    {
        R.SUBMITTED,
        R.DISPATCH_STARTED,
        R.OWNER_LOST,
        R.RECOVERY_HELD,
        R.RECOVERY_PAUSED,
        R.RECOVERY_OWNER_UNVERIFIED,
        R.RECOVERY_PID_REUSED,
        R.RECOVERY_LAUNCH_UNCONFIRMED,
        R.RECOVERY_CHILD_ALIVE,
        R.QUEUE_PAUSED,
        R.QUEUE_RESUMED,
        R.LAUNCH_CONFIRMED,
        R.LAUNCH_FAILED,
        R.STEP_PROGRESS,
        R.REAPPROVAL_REQUIRED,
    }
)
REQUIRED_TABLES_V1: Final = frozenset(
    {
        "installation",
        "counters",
        "operations",
        "operation_history",
        "operation_private",
        "queue",
        "queue_items",
        "attempts",
        "attempt_private",
        "leases",
        "outbox",
        "replays",
    }
)

_DDL_V1: Final = (
    "CREATE TABLE installation (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
    "id TEXT NOT NULL, secret BLOB NOT NULL CHECK (length(secret) = 32), "
    "epoch TEXT NOT NULL CHECK (length(epoch) = 32), "
    "epoch_floor INTEGER NOT NULL CHECK (epoch_floor >= 0), created_at TEXT NOT NULL)",
    "CREATE TABLE counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL CHECK (value >= 0))",
    "CREATE TABLE operations (id TEXT PRIMARY KEY, "
    "kind TEXT NOT NULL CHECK (kind IN ('job','setup_operation','refresh_operation')), "
    "status TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision >= 1), "
    "reason_code TEXT NOT NULL, recovery_required TEXT, "
    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE operation_history (operation_id TEXT NOT NULL REFERENCES operations(id), "
    "revision INTEGER NOT NULL, status TEXT NOT NULL, reason_code TEXT NOT NULL, "
    "recovery_required TEXT, outbox_seq INTEGER NOT NULL UNIQUE, recorded_at TEXT NOT NULL, "
    "PRIMARY KEY (operation_id, revision))",
    "CREATE TABLE operation_private (operation_id TEXT PRIMARY KEY REFERENCES operations(id), "
    "body TEXT NOT NULL)",
    "CREATE TABLE queue (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
    "revision INTEGER NOT NULL CHECK (revision >= 1), "
    "dispatch TEXT NOT NULL CHECK (dispatch IN ('running','paused')))",
    "CREATE TABLE queue_items (id TEXT PRIMARY KEY, "
    "operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id), "
    "ord INTEGER UNIQUE, active INTEGER NOT NULL CHECK (active IN (0,1)), "
    "held INTEGER NOT NULL CHECK (held IN (0,1)), blockers TEXT NOT NULL, "
    "CHECK ((active = 1 AND ord IS NULL) OR (active = 0 AND ord IS NOT NULL)))",
    # The single resource-heavy slot: at most one active queue item, enforced by the DB.
    "CREATE UNIQUE INDEX queue_one_active ON queue_items(active) WHERE active = 1",
    "CREATE TABLE attempts (id TEXT PRIMARY KEY, "
    "operation_id TEXT NOT NULL REFERENCES operations(id), number INTEGER NOT NULL, "
    "fencing_token INTEGER NOT NULL UNIQUE, owner_id TEXT NOT NULL, "
    "state TEXT NOT NULL CHECK (state IN ('active','released','interrupted')), "
    "launch TEXT NOT NULL CHECK (launch IN ('intended','confirmed','failed')), "
    "started_at TEXT NOT NULL, ended_at TEXT, UNIQUE (operation_id, number))",
    "CREATE TABLE attempt_private (attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), "
    "owner_nonce TEXT NOT NULL, owner_pid INTEGER NOT NULL, owner_start TEXT, "
    "child_pid INTEGER, child_start TEXT)",
    "CREATE TABLE leases (slot TEXT PRIMARY KEY CHECK (slot IN ('heavy','refresh')), "
    "operation_id TEXT NOT NULL UNIQUE REFERENCES operations(id), "
    "attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(id), "
    "fencing_token INTEGER NOT NULL UNIQUE, expires_at TEXT NOT NULL, "
    "last_seen_at TEXT NOT NULL)",
    "CREATE TABLE outbox (seq INTEGER PRIMARY KEY CHECK (seq >= 1), epoch TEXT NOT NULL, "
    "type TEXT NOT NULL CHECK (type IN ('state_changed','queue_changed')), "
    "resource_kind TEXT NOT NULL CHECK (resource_kind IN "
    "('job','setup_operation','refresh_operation','queue')), "
    "resource_id TEXT NOT NULL, resource_revision INTEGER NOT NULL, status TEXT, "
    "reason_code TEXT, attempt_id TEXT, recorded_at TEXT NOT NULL)",
    "CREATE INDEX outbox_resource ON outbox(resource_id, seq)",
    "CREATE TABLE replays (installation TEXT NOT NULL, method TEXT NOT NULL, "
    "endpoint TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, "
    "status INTEGER NOT NULL, headers TEXT NOT NULL, body TEXT NOT NULL, "
    "operation_id TEXT NOT NULL REFERENCES operations(id), created_at TEXT NOT NULL, "
    "PRIMARY KEY (installation, method, endpoint, key))",
    # Immutable journals: revisions and published events are append-only.
    *(
        f"CREATE TRIGGER {table}_immutable_{verb} BEFORE {verb} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
        for table in ("operation_history", "outbox", "replays")
        for verb in ("UPDATE", "DELETE")
    ),
)
REQUIRED_TABLES: Final = REQUIRED_TABLES_V1 | {"queue_replays"}
# v2 additions. Operation replays stay in ``replays`` unchanged; queue responses get their
# own table so no fictitious operation ID or OperationView body is ever stored.
_DDL_V2: Final = (
    "CREATE TABLE queue_replays (installation TEXT NOT NULL, method TEXT NOT NULL, "
    "endpoint TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, "
    "status INTEGER NOT NULL, headers TEXT NOT NULL, body TEXT NOT NULL, "
    "queue_revision INTEGER NOT NULL CHECK (queue_revision >= 1), created_at TEXT NOT NULL, "
    "PRIMARY KEY (installation, method, endpoint, key))",
    *(
        f"CREATE TRIGGER queue_replays_immutable_{verb} BEFORE {verb} ON queue_replays "
        "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
        for verb in ("UPDATE", "DELETE")
    ),
)
_COUNTERS: Final = ("outbox_seq", "fencing", "operations_revision", "enqueue_order")

Clock = Callable[[], datetime]
FaultHook = Callable[[str], None]
ConnectHook = Callable[[sqlite3.Connection], None]


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _storage_error(exc: BaseException) -> ControlError:
    number = getattr(exc, "sqlite_errorcode", 0) & 255
    code = {
        sqlite3.SQLITE_BUSY: E.STORE_BUSY,
        sqlite3.SQLITE_LOCKED: E.STORE_BUSY,
        sqlite3.SQLITE_FULL: E.STORE_FULL,
        sqlite3.SQLITE_READONLY: E.STORE_READONLY,
        sqlite3.SQLITE_CORRUPT: E.STORE_CORRUPT,
        sqlite3.SQLITE_NOTADB: E.STORE_CORRUPT,
        sqlite3.SQLITE_CANTOPEN: E.STORE_UNAVAILABLE,
    }.get(number, E.STORE_UNAVAILABLE)
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:  # ENOSPC
        code = E.STORE_FULL
    return ControlError(code)


def _ts(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return timezone-aware datetimes")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _system_clock() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return prefix + secrets.token_hex(16)


def _check_private(value: Any, depth: int = 0) -> None:
    if depth > MAX_PAYLOAD_DEPTH:
        raise ControlError(E.REQUEST_INVALID)
    if isinstance(value, dict):
        for key, item in value.items():
            if FORBIDDEN_KEY.search(key):
                raise ControlError(E.PRIVATE_PAYLOAD_FORBIDDEN)
            _check_private(item, depth + 1)
    elif isinstance(value, list | tuple):
        for item in value:
            _check_private(item, depth + 1)
    elif isinstance(value, str) and FORBIDDEN_VALUE.search(value):
        raise ControlError(E.PRIVATE_PAYLOAD_FORBIDDEN)


def _posix_owned(info: os.stat_result) -> bool:
    if sys.platform == "win32":
        return True
    return info.st_uid == os.geteuid()


def _validate_location(path: Path) -> None:
    if not path.is_absolute() or path.suffix != ".sqlite3" or ".." in path.parts:
        raise ControlError(E.STORE_UNSAFE_PATH)


def _prepare_directory(directory: Path) -> None:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or not _posix_owned(info):
        raise ControlError(E.STORE_UNSAFE_PATH)
    if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o700:
        directory.chmod(0o700)


def _check_file(path: Path) -> bool:
    """Return whether the DB file exists; refuse symlinks, non-files and foreign owners."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or not _posix_owned(info):
        raise ControlError(E.STORE_UNSAFE_PATH)
    if os.name != "nt" and stat.S_IMODE(info.st_mode) != 0o600:
        path.chmod(0o600)
    return True


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_database(
    source_path: Path,
    destination: Path,
    timeout: float,
    hook: ConnectHook | None,
    *,
    verify: Callable[[sqlite3.Connection], object],
) -> None:
    """SQLite online backup into a new exclusive 0600 file, verified, else removed."""
    _validate_location(destination)
    if destination.resolve() == source_path.resolve():
        raise ControlError(E.STORE_UNSAFE_PATH)
    try:
        info = destination.parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ControlError(E.STORE_UNSAFE_PATH)
        try:
            descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ControlError(E.STORE_EXISTS) from None
        os.close(descriptor)
    except ControlError:
        raise
    except OSError as exc:
        raise _storage_error(exc) from None
    try:
        with (
            closing(_connect(source_path, timeout, hook)) as source,
            closing(_connect(destination, timeout, None)) as target,
        ):
            source.backup(target)
        with closing(_connect(destination, timeout, None)) as check:
            check.execute("BEGIN")
            verify(check)
            check.rollback()
    except BaseException as exc:
        # Only the file this call created is removed; the live store is untouched.
        for leftover in (destination, destination.with_name(destination.name + "-journal")):
            with suppress(FileNotFoundError):
                leftover.unlink()
        if isinstance(exc, ControlError):
            raise
        if isinstance(exc, sqlite3.Error | OSError):
            raise _storage_error(exc) from None
        raise


def _connect(path: Path, timeout: float, hook: ConnectHook | None) -> sqlite3.Connection:
    # mode=rw: never let a later connection silently create a fresh empty database.
    db = sqlite3.connect(
        path.as_uri() + "?mode=rw", uri=True, timeout=timeout, isolation_level=None
    )
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        if hook is not None:
            hook(db)
    except BaseException:
        db.close()
        raise
    return db


@dataclass(frozen=True)
class Accepted:
    """Accepted mutation result; replays reproduce status/headers/body exactly."""

    status: int
    headers: Mapping[str, str]
    operation: OperationView
    replayed: bool


@dataclass(frozen=True)
class AcceptedQueue:
    """Accepted queue mutation; a replay returns the first committed QueueView verbatim."""

    status: int
    headers: Mapping[str, str]
    queue: QueueView
    replayed: bool


@dataclass(frozen=True)
class IdempotencyScope:
    endpoint: str
    key: str


@dataclass(frozen=True)
class _ActiveLease:
    operation_id: str
    attempt_id: str
    fencing_token: int
    launch: Literal["intended", "confirmed", "failed"]
    owner_nonce: str
    owner: ProcessIdentity
    child: ProcessIdentity | None


class ControlStore:
    """Explicitly constructed store. Default GUI/CLI startup never constructs one."""

    # Storage layout this class opens. Subclasses extend it with additive DDL only.
    TARGET_VERSION: ClassVar[int] = SCHEMA_VERSION
    # Replay tables sharing one idempotency scope namespace (one key, one response kind).
    REPLAY_TABLES: ClassVar[tuple[str, ...]] = ("replays", "queue_replays")

    @classmethod
    def _layout(cls, version: int) -> tuple[tuple[str, ...], frozenset[str]]:
        """DDL that upgrades ``version - 1`` to ``version`` and the tables it requires."""
        if version == 1:
            return _DDL_V1, REQUIRED_TABLES_V1
        if version == 2:
            return _DDL_V2, REQUIRED_TABLES
        raise ControlError(E.STORE_SCHEMA_UNSUPPORTED)

    @classmethod
    def _required(cls, version: int) -> frozenset[str]:
        return cls._layout(version)[1]

    def __init__(
        self,
        path: Path,
        *,
        create: bool = True,
        clock: Clock | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        _fault: FaultHook | None = None,
        _connect_hook: ConnectHook | None = None,
    ) -> None:
        self.path = path
        self._clock = clock or _system_clock
        self._timeout = timeout
        self._fault = _fault
        self._connect_hook = _connect_hook
        _validate_location(path)
        try:
            if create:
                _prepare_directory(path.parent)
            elif not path.parent.is_dir():
                raise ControlError(E.STORE_UNAVAILABLE)
            if not _check_file(path):
                if not create:
                    raise ControlError(E.STORE_UNAVAILABLE)
                self._initialize()
                _check_file(path)
            self._migrate()
            with self._transaction(write=False) as db:
                self._installation, self._secret = self._verify(db)
        except ControlError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise _storage_error(exc) from None

    # ------------------------------------------------------------------ lifecycle

    def _initialize(self) -> None:
        """Build the schema in a private temp file, then publish it without overwrite."""
        temporary = self.path.with_name(f".{self.path.name}.init-{secrets.token_hex(8)}")
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with closing(_connect(temporary, self._timeout, None)) as db:
                db.execute("BEGIN IMMEDIATE")
                for version in range(1, self.TARGET_VERSION + 1):
                    for statement in self._layout(version)[0]:
                        db.execute(statement)
                now = _ts(self._clock())
                db.execute(
                    "INSERT INTO installation VALUES (1,?,?,?,0,?)",
                    (secrets.token_hex(16), secrets.token_bytes(32), secrets.token_hex(16), now),
                )
                db.executemany("INSERT INTO counters VALUES (?,0)", [(n,) for n in _COUNTERS])
                db.execute("INSERT INTO queue VALUES (1,1,'running')")
                db.execute(f"PRAGMA user_version={self.TARGET_VERSION}")
                db.commit()
            with open(temporary, "rb+") as handle:
                os.fsync(handle.fileno())
            try:
                if os.name == "nt":
                    os.rename(temporary, self.path)  # fails if the target exists
                else:
                    os.link(temporary, self.path)  # atomic, never overwrites
            except FileExistsError:
                pass  # a concurrent initializer won; open its complete database
            _fsync_directory(self.path.parent)
        finally:
            for leftover in (temporary, temporary.with_name(temporary.name + "-journal")):
                with suppress(FileNotFoundError):
                    leftover.unlink()

    def _migrate(self) -> None:
        """Explicit, versioned, additive migrations in one BEGIN IMMEDIATE.

        Only a structurally valid older store is migrated, one step at a time inside the
        same transaction; the target version is left alone; anything else (0, unknown,
        newer) fails closed without touching the file. A crash or failure before commit
        leaves the complete older store, since DDL and user_version roll back.
        """
        target = self.TARGET_VERSION
        with self._transaction(write=False) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == target:
            return
        if not isinstance(version, int) or not 1 <= version < target:
            raise ControlError(E.STORE_SCHEMA_UNSUPPORTED)
        with self._transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == target:
                return  # a concurrent opener migrated first
            if not isinstance(version, int) or not 1 <= version < target:
                raise ControlError(E.STORE_SCHEMA_UNSUPPORTED)
            self._verify(db, version=version)
            tables = self._tables(db)
            for step in range(version + 1, target + 1):
                statements, required = self._layout(step)
                if (required - self._required(step - 1)) & tables:
                    raise ControlError(E.STORE_CORRUPT)  # old header, newer layout: refuse
                for statement in statements:
                    db.execute(statement)
            db.execute(f"PRAGMA user_version={target}")

    @staticmethod
    def _tables(db: sqlite3.Connection) -> set[str]:
        return {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }

    def _verify(self, db: sqlite3.Connection, *, version: int | None = None) -> tuple[str, bytes]:
        version = self.TARGET_VERSION if version is None else version
        if db.execute("PRAGMA user_version").fetchone()[0] != version:
            # Includes 0 (empty / foreign file). Never initialize or downgrade.
            raise ControlError(E.STORE_SCHEMA_UNSUPPORTED)
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ControlError(E.STORE_CORRUPT)
        if not self._tables(db) >= self._required(version):
            raise ControlError(E.STORE_CORRUPT)
        identity = db.execute("SELECT id, secret FROM installation").fetchall()
        if len(identity) != 1 or not isinstance(identity[0][1], bytes):
            raise ControlError(E.STORE_CORRUPT)
        counters = {row[0] for row in db.execute("SELECT name FROM counters").fetchall()}
        if (
            counters != set(_COUNTERS)
            or db.execute("SELECT count(*) FROM queue").fetchone()[0] != 1
        ):
            raise ControlError(E.STORE_CORRUPT)
        return identity[0][0], identity[0][1]

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        db: sqlite3.Connection | None = None
        try:
            db = _connect(self.path, self._timeout, self._connect_hook)
            # A deferred read transaction keeps one consistent SHARED snapshot until commit
            # (rollback journal mode), so snapshot and watermark cannot drift apart.
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            if write and self._fault is not None:
                self._fault("before_commit")
            db.commit()
            if write and self._fault is not None:
                self._fault("after_commit")
        except ControlError:
            if db is not None and db.in_transaction:
                db.rollback()
            raise
        except (sqlite3.Error, OSError) as exc:
            if db is not None and db.in_transaction:
                with suppress(sqlite3.Error):
                    db.rollback()
            raise _storage_error(exc) from None
        except BaseException:
            if db is not None and db.in_transaction:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    def _now(self) -> str:
        return _ts(self._clock())

    # ------------------------------------------------------------------ primitives

    @staticmethod
    def _counter(db: sqlite3.Connection, name: str) -> int:
        db.execute("UPDATE counters SET value = value + 1 WHERE name = ?", (name,))
        value: int = db.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()[0]
        return value

    @staticmethod
    def _epoch(db: sqlite3.Connection) -> tuple[str, int]:
        epoch, floor = db.execute("SELECT epoch, epoch_floor FROM installation").fetchone()
        return str(epoch), int(floor)

    def _emit(
        self,
        db: sqlite3.Connection,
        *,
        type_: Literal["state_changed", "queue_changed"],
        resource_kind: str,
        resource_id: str,
        revision: int,
        status: str | None,
        reason: ReasonCode | None,
        attempt_id: str | None,
    ) -> int:
        seq = self._counter(db, "outbox_seq")
        epoch, _ = self._epoch(db)
        db.execute(
            "INSERT INTO outbox VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                seq,
                epoch,
                type_,
                resource_kind,
                resource_id,
                revision,
                status,
                reason.value if reason else None,
                attempt_id,
                self._now(),
            ),
        )
        return seq

    def _row(self, db: sqlite3.Connection, operation_id: str) -> tuple[str, S, int, str | None]:
        row = db.execute(
            "SELECT kind, status, revision, recovery_required FROM operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise ControlError(E.RESOURCE_NOT_FOUND)
        try:
            return row[0], S(row[1]), int(row[2]), row[3]
        except ValueError:
            raise ControlError(E.STORE_CORRUPT) from None

    def _record(
        self,
        db: sqlite3.Connection,
        operation_id: str,
        status: S,
        reason: ReasonCode,
        *,
        recovery_required: ReasonCode | None = None,
        attempt_id: str | None = None,
    ) -> int:
        """Append one immutable revision and its state_changed event, same transaction."""
        _, _, revision, _ = self._row(db, operation_id)
        revision += 1
        db.execute(
            "UPDATE operations SET status = ?, revision = ?, reason_code = ?, "
            "recovery_required = ?, updated_at = ? WHERE id = ?",
            (
                status.value,
                revision,
                reason.value,
                recovery_required.value if recovery_required else None,
                self._now(),
                operation_id,
            ),
        )
        self._journal(db, operation_id, revision, status, reason, recovery_required, attempt_id)
        return revision

    def _journal(
        self,
        db: sqlite3.Connection,
        operation_id: str,
        revision: int,
        status: S,
        reason: ReasonCode,
        recovery_required: ReasonCode | None,
        attempt_id: str | None,
    ) -> None:
        kind = db.execute("SELECT kind FROM operations WHERE id = ?", (operation_id,)).fetchone()[0]
        self._counter(db, "operations_revision")
        seq = self._emit(
            db,
            type_="state_changed",
            resource_kind=kind,
            resource_id=operation_id,
            revision=revision,
            status=status.value,
            reason=reason,
            attempt_id=attempt_id,
        )
        db.execute(
            "INSERT INTO operation_history VALUES (?,?,?,?,?,?,?)",
            (
                operation_id,
                revision,
                status.value,
                reason.value,
                recovery_required.value if recovery_required else None,
                seq,
                self._now(),
            ),
        )

    def _queue_changed(self, db: sqlite3.Connection, reason: ReasonCode) -> None:
        db.execute("UPDATE queue SET revision = revision + 1")
        revision, dispatch = db.execute("SELECT revision, dispatch FROM queue").fetchone()
        self._emit(
            db,
            type_="queue_changed",
            resource_kind="queue",
            resource_id=QUEUE_RESOURCE_ID,
            revision=revision,
            status=dispatch,
            reason=reason,
            attempt_id=None,
        )

    def _enqueue(self, db: sqlite3.Connection, operation_id: str) -> None:
        db.execute(
            "INSERT INTO queue_items VALUES (?,?,?,0,0,'')",
            (_new_id("qi_"), operation_id, self._counter(db, "enqueue_order")),
        )

    def _release(self, db: sqlite3.Connection, operation_id: str, attempt_state: str) -> str | None:
        """Drop lease and queue item; returns the attempt ID that held the lease."""
        lease = db.execute(
            "SELECT attempt_id FROM leases WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        db.execute("DELETE FROM leases WHERE operation_id = ?", (operation_id,))
        db.execute("DELETE FROM queue_items WHERE operation_id = ?", (operation_id,))
        if lease is None:
            return None
        db.execute(
            "UPDATE attempts SET state = ?, ended_at = ? WHERE id = ?",
            (attempt_state, self._now(), lease[0]),
        )
        return str(lease[0])

    @staticmethod
    def _blockers(raw: str) -> tuple[ReasonCode, ...]:
        try:
            return tuple(ReasonCode(code) for code in raw.split(",") if code)
        except ValueError:
            raise ControlError(E.STORE_CORRUPT) from None

    def _view(self, db: sqlite3.Connection, operation_id: str) -> OperationView:
        row = db.execute(
            "SELECT id, kind, status, revision, reason_code, recovery_required, created_at, "
            "updated_at FROM operations WHERE id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise ControlError(E.RESOURCE_NOT_FOUND)
        item = db.execute(
            "SELECT held, blockers FROM queue_items WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        attempt = db.execute(
            "SELECT id, number, owner_id, state, launch, started_at, ended_at FROM attempts "
            "WHERE operation_id = ? ORDER BY number DESC LIMIT 1",
            (operation_id,),
        ).fetchone()
        blockers = self._blockers(item[1]) if item else ()
        try:
            return OperationView.model_validate(
                {
                    "schema_version": 1,
                    "id": row[0],
                    "kind": row[1],
                    "status": row[2],
                    "revision": row[3],
                    "held": bool(item[0]) if item else False,
                    "blocked": bool(blockers),
                    "blockers": blockers,
                    "recovery_required": row[5],
                    "reason_code": row[4],
                    "attempt": None
                    if attempt is None
                    else AttemptView.model_validate(
                        {
                            "attempt_id": attempt[0],
                            "number": attempt[1],
                            "owner_id": attempt[2],
                            "state": attempt[3],
                            "launch": attempt[4],
                            "started_at": attempt[5],
                            "ended_at": attempt[6],
                        }
                    ),
                    "created_at": row[6],
                    "updated_at": row[7],
                }
            )
        except ValidationError:
            raise ControlError(E.STORE_CORRUPT) from None

    def _queue_view(self, db: sqlite3.Connection) -> QueueView:
        revision, dispatch = db.execute("SELECT revision, dispatch FROM queue").fetchone()
        rows = db.execute(
            "SELECT q.id, q.operation_id, o.kind, q.active, q.held, q.blockers "
            "FROM queue_items q JOIN operations o ON o.id = q.operation_id "
            "ORDER BY q.active DESC, q.ord"
        ).fetchall()
        items, active, position = [], None, 0
        for item_id, operation_id, kind, is_active, held, blockers in rows:
            if is_active:
                active = item_id
            else:
                position += 1
            items.append(
                {
                    "id": item_id,
                    "resource_id": operation_id,
                    "resource_kind": kind,
                    "position": None if is_active else position,
                    "held": bool(held),
                    "blockers": self._blockers(blockers),
                }
            )
        try:
            return QueueView.model_validate(
                {
                    "schema_version": 1,
                    "revision": revision,
                    "dispatch": dispatch,
                    "active_item_id": active,
                    "items": tuple(QueueItemView.model_validate(item) for item in items),
                }
            )
        except ValidationError:
            raise ControlError(E.STORE_CORRUPT) from None

    # ------------------------------------------------------------------ idempotency

    def _digest(self, body: Any) -> str:
        try:
            text = canonical(body)
        except (TypeError, ValueError):
            raise ControlError(E.REQUEST_INVALID) from None
        if len(text.encode()) > MAX_REQUEST_BYTES:
            raise ControlError(E.PAYLOAD_TOO_LARGE)
        return hmac.new(self._secret, text.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _validate_scope(scope: IdempotencyScope) -> None:
        if not isinstance(scope.key, str) or not KEY_PATTERN.fullmatch(scope.key):
            raise ControlError(E.IDEMPOTENCY_KEY_INVALID)
        if not isinstance(scope.endpoint, str) or not ENDPOINT_PATTERN.fullmatch(scope.endpoint):
            raise ControlError(E.ENDPOINT_INVALID)

    def _replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> Accepted | None:
        row = db.execute(
            "SELECT digest, status, headers, body FROM replays WHERE installation = ? "
            "AND method = 'POST' AND endpoint = ? AND key = ?",
            (self._installation, scope.endpoint, scope.key),
        ).fetchone()
        if row is None:
            self._scope_free(db, "replays", scope)  # one scope, one response kind
            return None
        if not hmac.compare_digest(row[0], digest):
            raise ControlError(E.IDEMPOTENCY_CONFLICT)
        try:
            headers = json.loads(row[2])
            if not isinstance(headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
            ):
                raise ValueError
            operation = OperationView.model_validate_json(row[3])
        except (ValueError, ValidationError):
            raise ControlError(E.STORE_CORRUPT) from None
        return Accepted(row[1], MappingProxyType(headers), operation, True)

    def _remember(
        self,
        db: sqlite3.Connection,
        scope: IdempotencyScope,
        digest: str,
        status: int,
        headers: dict[str, str],
        operation: OperationView,
    ) -> Accepted:
        db.execute(
            "INSERT INTO replays VALUES (?,'POST',?,?,?,?,?,?,?,?)",
            (
                self._installation,
                scope.endpoint,
                scope.key,
                digest,
                status,
                canonical(headers),
                canonical(operation.to_wire()),
                operation.id,
                self._now(),
            ),
        )
        return Accepted(status, MappingProxyType(headers), operation, False)

    # ------------------------------------------------------------------ public API

    def submit(self, endpoint: str, key: str, body: Mapping[str, Any]) -> Accepted:
        """Accept one operation (201). Same key+body replays; different body is 409."""
        scope = IdempotencyScope(endpoint, key)
        self._validate_scope(scope)
        digest = self._digest(body)
        with self._transaction() as db:
            # Replay lookup precedes DTO, semantic and CAS checks.
            replay = self._replay(db, scope, digest)
            if replay is not None:
                return replay
            try:
                request = SubmitOperation.model_validate(body)
            except ValidationError:
                raise ControlError(E.REQUEST_INVALID) from None
            _check_private(request.private_payload)
            operation_id = _new_id(ID_PREFIX[request.kind])
            now = self._now()
            db.execute(
                "INSERT INTO operations VALUES (?,?,?,1,?,NULL,?,?)",
                (operation_id, request.kind, S.QUEUED.value, R.SUBMITTED.value, now, now),
            )
            db.execute(
                "INSERT INTO operation_private VALUES (?,?)",
                (operation_id, canonical(request.private_payload)),
            )
            self._journal(db, operation_id, 1, S.QUEUED, R.SUBMITTED, None, None)
            if request.kind in transitions.QUEUED_KINDS:
                self._enqueue(db, operation_id)
                self._queue_changed(db, R.SUBMITTED)
            view = self._view(db, operation_id)
            headers = {"ETag": f'"{view.revision}"', "Location": f"{endpoint}/{operation_id}"}
            return self._remember(db, scope, digest, 201, headers, view)

    def transition(
        self,
        operation_id: str,
        request: Mapping[str, Any],
        *,
        idempotency: IdempotencyScope | None = None,
        fencing_token: int | None = None,
    ) -> Accepted:
        """Apply one table-checked transition with CAS. Never enters ``running``."""
        digest = None
        if idempotency is not None:
            self._validate_scope(idempotency)
            digest = self._digest(
                {"operation_id": operation_id, "request": dict(request), "fence": fencing_token}
            )
        with self._transaction() as db:
            if idempotency is not None and digest is not None:
                replay = self._replay(db, idempotency, digest)
                if replay is not None:
                    return replay
            try:
                parsed = TransitionRequest.model_validate(request)
            except ValidationError:
                raise ControlError(E.REQUEST_INVALID) from None
            if parsed.reason_code in RESERVED_REASONS:
                raise ControlError(E.REQUEST_INVALID)
            kind, current, revision, _ = self._row(db, operation_id)
            if revision != parsed.expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            target = parsed.to_status
            if target is S.RUNNING:
                raise ControlError(E.INVALID_TRANSITION)  # only begin_attempt may dispatch
            transitions.check(kind, current, target)
            attempt_id = self._apply(db, operation_id, kind, current, target, fencing_token)
            self._record(db, operation_id, target, parsed.reason_code, attempt_id=attempt_id)
            if self._queue_touched(kind, current, target):
                self._queue_changed(db, parsed.reason_code)
            view = self._view(db, operation_id)
            headers = {"ETag": f'"{view.revision}"'}
            if idempotency is not None and digest is not None:
                return self._remember(db, idempotency, digest, 200, headers, view)
            return Accepted(200, MappingProxyType(headers), view, False)

    @staticmethod
    def _queue_touched(kind: str, current: S, target: S) -> bool:
        if kind not in transitions.QUEUED_KINDS:
            return False
        return not (current in transitions.ACTIVE and target is S.CANCELLING)

    def _apply(
        self,
        db: sqlite3.Connection,
        operation_id: str,
        kind: str,
        current: S,
        target: S,
        fencing_token: int | None,
    ) -> str | None:
        """Side effects on queue item / lease for one allowed transition."""
        if current in transitions.ACTIVE:
            lease = db.execute(
                "SELECT attempt_id, fencing_token FROM leases WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if lease is None:
                raise ControlError(E.STORE_CORRUPT)
            if target is S.CANCELLING:
                return str(lease[0])  # cancel request: user side, slot stays occupied
            if fencing_token is None or fencing_token != lease[1]:
                raise ControlError(E.FENCING_TOKEN_STALE)
            state = "interrupted" if target is S.INTERRUPTED else "released"
            return self._release(db, operation_id, state)
        item = db.execute(
            "SELECT held, blockers FROM queue_items WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        if target in transitions.TERMINAL:
            self._release(db, operation_id, "released")
        elif target is S.HELD:
            if current is S.BLOCKED and not (item and item[0]):
                raise ControlError(E.INVALID_TRANSITION)
            db.execute(
                "UPDATE queue_items SET held = 1, blockers = '' WHERE operation_id = ?",
                (operation_id,),
            )
        elif target is S.BLOCKED:
            db.execute(
                "UPDATE queue_items SET blockers = ? WHERE operation_id = ?",
                (R.PRESTART_REVALIDATION_FAILED.value, operation_id),
            )
        elif target is S.QUEUED:
            if item is None:
                self._enqueue(db, operation_id)  # explicit resume: back to the tail
            elif current is S.BLOCKED and item[0]:
                raise ControlError(E.INVALID_TRANSITION)  # releasing a block is not a release
            else:
                db.execute(
                    "UPDATE queue_items SET held = 0, blockers = '' WHERE operation_id = ?",
                    (operation_id,),
                )
        return None

    def begin_attempt(
        self,
        operation_id: str,
        expected_revision: int,
        owner: OwnerIdentity,
        *,
        lease_seconds: int = 60,
    ) -> LeaseGrant:
        """Commit dispatch intent, lease, fencing token and attempt journal atomically.

        This only *records* intent. Contract for B7/B8: commit this before starting any
        child process, then report evidence with ``record_launch``. B6 starts nothing.
        """
        if not 1 <= lease_seconds <= 3600:
            raise ControlError(E.REQUEST_INVALID)
        with self._transaction() as db:
            kind, current, revision, _ = self._row(db, operation_id)
            if revision != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            transitions.check(kind, current, S.RUNNING)
            if kind in transitions.QUEUED_KINDS:
                if db.execute("SELECT dispatch FROM queue").fetchone()[0] != "running":
                    raise ControlError(E.QUEUE_PAUSED)
                item = db.execute(
                    "SELECT held, blockers FROM queue_items WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if item is None or item[0] or item[1]:
                    raise ControlError(E.NOT_DISPATCHABLE)
                self._dispatch_guard(db, operation_id)
            slot = transitions.SLOT[kind]
            if db.execute("SELECT 1 FROM leases WHERE slot = ?", (slot,)).fetchone():
                raise ControlError(E.SLOT_OCCUPIED)
            number = db.execute(
                "SELECT coalesce(max(number), 0) + 1 FROM attempts WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()[0]
            attempt_id, fence = _new_id("att_"), self._counter(db, "fencing")
            now = self._clock()
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,'active','intended',?,NULL)",
                (attempt_id, operation_id, number, fence, owner.owner_id, _ts(now)),
            )
            db.execute(
                "INSERT INTO attempt_private VALUES (?,?,?,?,NULL,NULL)",
                (attempt_id, owner.nonce, owner.process.pid, owner.process.start_identity),
            )
            try:
                db.execute(
                    "INSERT INTO leases VALUES (?,?,?,?,?,?)",
                    (
                        slot,
                        operation_id,
                        attempt_id,
                        fence,
                        _ts(now + timedelta(seconds=lease_seconds)),
                        _ts(now),
                    ),
                )
                if kind in transitions.QUEUED_KINDS:
                    db.execute(
                        "UPDATE queue_items SET active = 1, ord = NULL WHERE operation_id = ?",
                        (operation_id,),
                    )
            except sqlite3.IntegrityError:
                raise ControlError(E.SLOT_OCCUPIED) from None
            self._record(db, operation_id, S.RUNNING, R.DISPATCH_STARTED, attempt_id=attempt_id)
            if kind in transitions.QUEUED_KINDS:
                self._queue_changed(db, R.DISPATCH_STARTED)
            return LeaseGrant(
                operation=self._view(db, operation_id), attempt_id=attempt_id, fencing_token=fence
            )

    def _dispatch_guard(self, db: sqlite3.Connection, operation_id: str) -> None:
        """Extra dispatch precondition checked inside the begin_attempt transaction."""

    def _fenced(self, db: sqlite3.Connection, operation_id: str, fencing_token: int) -> str:
        self._row(db, operation_id)
        lease = db.execute(
            "SELECT attempt_id, fencing_token FROM leases WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if lease is None or lease[1] != fencing_token:
            raise ControlError(E.FENCING_TOKEN_STALE)
        return str(lease[0])

    def record_launch(
        self,
        operation_id: str,
        fencing_token: int,
        *,
        outcome: Literal["confirmed", "failed"],
        child: ProcessIdentity | None = None,
    ) -> OperationView:
        """Record post-spawn evidence. ``confirmed`` requires the child identity."""
        if outcome not in ("confirmed", "failed") or (outcome == "confirmed") != (
            child is not None
        ):
            raise ControlError(E.REQUEST_INVALID)
        with self._transaction() as db:
            attempt_id = self._fenced(db, operation_id, fencing_token)
            launch = db.execute(
                "SELECT launch FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if launch[0] != "intended":
                raise ControlError(E.INVALID_TRANSITION)
            db.execute("UPDATE attempts SET launch = ? WHERE id = ?", (outcome, attempt_id))
            if child is not None:
                db.execute(
                    "UPDATE attempt_private SET child_pid = ?, child_start = ? "
                    "WHERE attempt_id = ?",
                    (child.pid, child.start_identity, attempt_id),
                )
            _, status, _, flagged = self._row(db, operation_id)
            reason = R.LAUNCH_CONFIRMED if outcome == "confirmed" else R.LAUNCH_FAILED
            self._record(
                db,
                operation_id,
                status,
                reason,
                recovery_required=R(flagged) if flagged else None,
                attempt_id=attempt_id,
            )
            return self._view(db, operation_id)

    def renew_lease(
        self, operation_id: str, fencing_token: int, *, lease_seconds: int = 60
    ) -> None:
        """Extend a lease. Expiry is advisory: it never triggers dispatch or interruption."""
        if not 1 <= lease_seconds <= 3600:
            raise ControlError(E.REQUEST_INVALID)
        with self._transaction() as db:
            self._fenced(db, operation_id, fencing_token)
            now = self._clock()
            db.execute(
                "UPDATE leases SET expires_at = ?, last_seen_at = ? WHERE operation_id = ?",
                (_ts(now + timedelta(seconds=lease_seconds)), _ts(now), operation_id),
            )

    def set_hold(
        self,
        operation_id: str,
        expected_revision: int,
        *,
        held: bool,
        idempotency: IdempotencyScope | None = None,
    ) -> Accepted:
        """Record user hold intent on a waiting item (B7 wraps this as hold/release).

        ``held`` is user intent and ``blocked`` is an unmet environment requirement; they
        are separate flags, so releasing a hold never clears a block. For jobs the status
        mirrors the flags (queued/held/blocked); setup operations keep ``queued``.
        """
        if type(held) is not bool:
            raise ControlError(E.REQUEST_INVALID)
        digest = None
        if idempotency is not None:
            self._validate_scope(idempotency)
            digest = self._digest(
                {"operation_id": operation_id, "expected_revision": expected_revision, "held": held}
            )
        with self._transaction() as db:
            if idempotency is not None and digest is not None:
                replay = self._replay(db, idempotency, digest)
                if replay is not None:
                    return replay
            kind, current, revision, _ = self._row(db, operation_id)
            if revision != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            item = db.execute(
                "SELECT held, blockers FROM queue_items WHERE operation_id = ? AND active = 0",
                (operation_id,),
            ).fetchone()
            if item is None or current not in transitions.WAITING or bool(item[0]) == held:
                raise ControlError(E.INVALID_TRANSITION)
            db.execute(
                "UPDATE queue_items SET held = ? WHERE operation_id = ?", (int(held), operation_id)
            )
            target = current
            if kind == "job" and not item[1]:
                target = S.HELD if held else S.QUEUED
            reason = R.USER_HOLD if held else R.USER_RELEASE
            self._record(db, operation_id, target, reason)
            self._queue_changed(db, reason)
            view = self._view(db, operation_id)
            headers = {"ETag": f'"{view.revision}"'}
            if idempotency is not None and digest is not None:
                return self._remember(db, idempotency, digest, 200, headers, view)
            return Accepted(200, MappingProxyType(headers), view, False)

    def _scope_free(self, db: sqlite3.Connection, own: str, scope: IdempotencyScope) -> None:
        """Refuse a key already used for a different response kind in this scope."""
        for table in self.REPLAY_TABLES:
            if table != own and self._scope_used(db, table, scope):
                raise ControlError(E.IDEMPOTENCY_CONFLICT)

    def _scope_used(self, db: sqlite3.Connection, table: str, scope: IdempotencyScope) -> bool:
        return (
            db.execute(
                f"SELECT 1 FROM {table} WHERE installation = ? AND method = 'POST' "
                "AND endpoint = ? AND key = ?",
                (self._installation, scope.endpoint, scope.key),
            ).fetchone()
            is not None
        )

    def _queue_replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str
    ) -> AcceptedQueue | None:
        row = db.execute(
            "SELECT digest, status, headers, body FROM queue_replays WHERE installation = ? "
            "AND method = 'POST' AND endpoint = ? AND key = ?",
            (self._installation, scope.endpoint, scope.key),
        ).fetchone()
        if row is None:
            self._scope_free(db, "queue_replays", scope)
            return None
        if not hmac.compare_digest(row[0], digest):
            raise ControlError(E.IDEMPOTENCY_CONFLICT)
        try:
            headers = json.loads(row[2])
            if not isinstance(headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
            ):
                raise ValueError
            queue = QueueView.model_validate_json(row[3])
        except (ValueError, ValidationError):
            raise ControlError(E.STORE_CORRUPT) from None
        return AcceptedQueue(row[1], MappingProxyType(headers), queue, True)

    def set_dispatch(
        self,
        expected_revision: int,
        dispatch: DispatchState,
        *,
        idempotency: IdempotencyScope | None = None,
    ) -> AcceptedQueue:
        """Record queue dispatch state. ``paused`` only stops *new* dispatch.

        With ``idempotency`` the first committed response (status, ETag, QueueView) is
        stored in ``queue_replays`` in the same transaction and replayed verbatim, before
        any validation or CAS. Omitting it is allowed for internal B6 callers only; the
        B7+ HTTP boundary must always pass a key. A no-op (already in the requested state
        with a matching revision) adds no revision or event, but its response is recorded.
        """
        digest = None
        if idempotency is not None:
            self._validate_scope(idempotency)
            digest = self._digest({"expected_revision": expected_revision, "dispatch": dispatch})
        with self._transaction() as db:
            if idempotency is not None and digest is not None:
                replay = self._queue_replay(db, idempotency, digest)
                if replay is not None:
                    return replay
            if dispatch not in ("running", "paused") or type(expected_revision) is not int:
                raise ControlError(E.REQUEST_INVALID)
            revision, current = db.execute("SELECT revision, dispatch FROM queue").fetchone()
            if revision != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            if current != dispatch:
                db.execute("UPDATE queue SET dispatch = ?", (dispatch,))
                self._queue_changed(db, R.QUEUE_PAUSED if dispatch == "paused" else R.QUEUE_RESUMED)
            view = self._queue_view(db)
            headers = {"ETag": f'"{view.revision}"'}
            if idempotency is not None and digest is not None:
                db.execute(
                    "INSERT INTO queue_replays VALUES (?,'POST',?,?,?,?,?,?,?,?)",
                    (
                        self._installation,
                        idempotency.endpoint,
                        idempotency.key,
                        digest,
                        200,
                        canonical(headers),
                        canonical(view.to_wire()),
                        view.revision,
                        self._now(),
                    ),
                )
            return AcceptedQueue(200, MappingProxyType(headers), view, False)

    # ------------------------------------------------------------------ reads

    def get(self, operation_id: str) -> OperationView:
        with self._transaction(write=False) as db:
            return self._view(db, operation_id)

    def get_private(self, operation_id: str) -> PrivateOperation:
        """Private read. Callers must not log, publish or copy this into events."""
        with self._transaction(write=False) as db:
            kind, _, _, _ = self._row(db, operation_id)
            row = db.execute(
                "SELECT body FROM operation_private WHERE operation_id = ?", (operation_id,)
            ).fetchone()
            if row is None:
                raise ControlError(E.STORE_CORRUPT)
            try:
                payload = json.loads(row[0])
                return PrivateOperation.model_validate(
                    {
                        "schema_version": 1,
                        "id": operation_id,
                        "kind": kind,
                        "private_payload": payload,
                    }
                )
            except (ValueError, ValidationError):
                raise ControlError(E.STORE_CORRUPT) from None

    def queue(self) -> QueueView:
        with self._transaction(write=False) as db:
            return self._queue_view(db)

    def _parse_cursor(self, db: sqlite3.Connection, cursor: str | None) -> tuple[str, int, int]:
        epoch, floor = self._epoch(db)
        high: int = db.execute("SELECT value FROM counters WHERE name = 'outbox_seq'").fetchone()[0]
        if cursor is None:
            return epoch, floor, high
        match = CURSOR_RE.fullmatch(cursor) if isinstance(cursor, str) else None
        if match is None:
            raise ControlError(E.CURSOR_INVALID)
        seq = int(match.group(2))
        if not hmac.compare_digest(match.group(1), epoch) or seq < floor:
            raise ControlError(E.EVENT_CURSOR_EXPIRED)
        if seq > high:
            raise ControlError(E.CURSOR_INVALID)
        return epoch, seq, high

    def read_events(
        self, after: str | None = None, *, resource_id: str | None = None, limit: int = 100
    ) -> EventPage:
        """Read committed events after ``after``. Read-only; filters skip gaps safely."""
        if type(limit) is not int or not 1 <= limit <= MAX_EVENT_PAGE:
            raise ControlError(E.REQUEST_INVALID)
        if resource_id is not None and (
            not isinstance(resource_id, str) or not 1 <= len(resource_id) <= 200
        ):
            raise ControlError(E.REQUEST_INVALID)
        with self._transaction(write=False) as db:
            epoch, start, high = self._parse_cursor(db, after)
            query = (
                "SELECT seq, epoch, type, resource_kind, resource_id, resource_revision, "
                "status, reason_code, attempt_id, recorded_at FROM outbox WHERE seq > ? "
            )
            params: tuple[Any, ...] = (start,)
            if resource_id is not None:
                query += "AND resource_id = ? "
                params += (resource_id,)
            rows = db.execute(query + "ORDER BY seq LIMIT ?", (*params, limit + 1)).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            # Without more rows the scan covered everything up to the high watermark.
            last = rows[-1][0] if has_more else high
            try:
                events = tuple(self._event(row) for row in rows)
                return EventPage(
                    schema_version=1,
                    events=events,
                    next_cursor=f"{epoch}:{last}",
                    has_more=has_more,
                )
            except ValidationError:
                raise ControlError(E.STORE_CORRUPT) from None

    @staticmethod
    def _event(row: tuple[Any, ...]) -> EventEnvelope:
        seq, epoch, type_, kind, resource_id, revision, status, reason, attempt, at = row
        return EventEnvelope.model_validate(
            {
                "schema_version": 1,
                "id": f"{epoch}:{seq}",
                "resource_id": resource_id,
                "resource_kind": kind,
                "resource_revision": revision,
                "timestamp": at,
                "type": type_,
                "payload": EventPayload.model_validate(
                    {"status": status, "reason_code": reason, "attempt_id": attempt}
                ),
            }
        )

    def snapshot(self) -> ControlSnapshot:
        """Operations, queue and outbox watermark from one read transaction."""
        with self._transaction(write=False) as db:
            epoch, _, high = self._parse_cursor(db, None)
            ids = [row[0] for row in db.execute("SELECT id FROM operations ORDER BY rowid")]
            revision = db.execute(
                "SELECT value FROM counters WHERE name = 'operations_revision'"
            ).fetchone()[0]
            try:
                return ControlSnapshot(
                    schema_version=1,
                    event_cursor=f"{epoch}:{high}",
                    operations_revision=max(revision, 1),
                    operations=tuple(self._view(db, operation_id) for operation_id in ids),
                    queue=self._queue_view(db),
                )
            except ValidationError:
                raise ControlError(E.STORE_CORRUPT) from None

    # ------------------------------------------------------------------ recovery

    def _active_leases(self, db: sqlite3.Connection) -> list[_ActiveLease]:
        rows = db.execute(
            "SELECT l.operation_id, l.attempt_id, l.fencing_token, a.launch, p.owner_nonce, "
            "p.owner_pid, p.owner_start, p.child_pid, p.child_start FROM leases l "
            "JOIN attempts a ON a.id = l.attempt_id "
            "JOIN attempt_private p ON p.attempt_id = l.attempt_id ORDER BY l.fencing_token"
        ).fetchall()
        try:
            return [
                _ActiveLease(
                    operation_id=row[0],
                    attempt_id=row[1],
                    fencing_token=row[2],
                    launch=row[3],
                    owner_nonce=row[4],
                    owner=ProcessIdentity(pid=row[5], start_identity=row[6]),
                    child=None
                    if row[7] is None
                    else ProcessIdentity(pid=row[7], start_identity=row[8]),
                )
                for row in rows
            ]
        except ValidationError:
            raise ControlError(E.STORE_CORRUPT) from None

    def recover(
        self,
        verifier: ProcessVerifier | None = None,
        *,
        current_owner: OwnerIdentity | None = None,
    ) -> RecoveryReport:
        """Explicit startup recovery. Never dispatches, kills, downloads or succeeds work.

        Pauses dispatch, holds waiting items in order, and interrupts active records only
        when the owner *and* any recorded child are verified gone. Anything unverifiable
        is flagged ``recovery_required`` and keeps its slot so no second worker starts.
        Calling it again with the same observations changes nothing.
        """
        checker = verifier or UnknownVerifier()
        with self._transaction(write=False) as db:
            leases = self._active_leases(db)
        decisions = {}
        for lease in leases:  # observe outside the write lock; re-validated below
            if current_owner is not None and hmac.compare_digest(
                lease.owner_nonce, current_owner.nonce
            ):
                owner = Liveness.ALIVE
            else:
                owner = safe_check(checker, lease.owner)
            child = safe_check(checker, lease.child) if lease.child is not None else None
            decisions[(lease.operation_id, lease.fencing_token)] = decide(
                owner, lease.launch, child
            )
        held: list[str] = []
        interrupted: list[str] = []
        required: list[str] = []
        alive: list[str] = []
        with self._transaction() as db:
            queue_changed = False
            flagged_now = False
            if db.execute("SELECT dispatch FROM queue").fetchone()[0] != "paused":
                db.execute("UPDATE queue SET dispatch = 'paused'")
                queue_changed = True
            waiting = db.execute(
                "SELECT q.operation_id, o.kind, o.status FROM queue_items q "
                "JOIN operations o ON o.id = q.operation_id "
                "WHERE q.active = 0 AND q.held = 0 ORDER BY q.ord"
            ).fetchall()
            for operation_id, kind, status in waiting:
                db.execute(
                    "UPDATE queue_items SET held = 1 WHERE operation_id = ?", (operation_id,)
                )
                target = S.HELD if kind == "job" and status == S.QUEUED.value else S(status)
                self._record(db, operation_id, target, R.RECOVERY_HELD)
                held.append(operation_id)
                queue_changed = True
            current = {lease.operation_id: lease for lease in self._active_leases(db)}
            active_rows = db.execute(
                "SELECT id, kind, status, recovery_required FROM operations "
                "WHERE status IN ('running','cancelling') ORDER BY rowid"
            ).fetchall()
            for operation_id, kind, status, flagged in active_rows:
                active = current.get(operation_id)
                decision = decisions.get((operation_id, active.fencing_token)) if active else None
                if active is not None and decision is None:
                    continue  # lease changed after observation; leave it to a later call
                if decision is not None and decision.outcome is Outcome.OWNER_ALIVE:
                    alive.append(operation_id)
                    continue
                if decision is not None and decision.outcome is Outcome.INTERRUPTED:
                    attempt_id = self._release(db, operation_id, "interrupted")
                    self._record(
                        db, operation_id, S.INTERRUPTED, R.OWNER_LOST, attempt_id=attempt_id
                    )
                    interrupted.append(operation_id)
                    queue_changed = queue_changed or kind in transitions.QUEUED_KINDS
                    continue
                reason = (
                    decision.reason
                    if decision is not None and decision.reason is not None
                    else R.RECOVERY_OWNER_UNVERIFIED
                )
                required.append(operation_id)
                if flagged != reason.value:
                    flagged_now = True
                    self._record(
                        db,
                        operation_id,
                        S(status),
                        reason,
                        recovery_required=reason,
                        attempt_id=active.attempt_id if active else None,
                    )
            if queue_changed:
                self._queue_changed(db, R.RECOVERY_PAUSED)
            epoch, _ = self._epoch(db)
            high = db.execute("SELECT value FROM counters WHERE name = 'outbox_seq'").fetchone()[0]
            changed = queue_changed or flagged_now
            return RecoveryReport(
                schema_version=1,
                queue_paused=True,
                held=tuple(held),
                interrupted=tuple(interrupted),
                recovery_required=tuple(required),
                owner_alive=tuple(alive),
                changed=changed,
                event_cursor=f"{epoch}:{high}",
            )

    # ------------------------------------------------------------------ maintenance

    def backup(self, destination: Path) -> None:
        """Consistent online copy via the SQLite backup API into a new 0600 file."""
        _copy_database(
            self.path, destination, self._timeout, self._connect_hook, verify=self._verify
        )

    @classmethod
    def backup_before_open(
        cls, path: Path, destination: Path, *, timeout: float = DEFAULT_TIMEOUT
    ) -> int:
        """Copy an existing store file *before* any class opens (and migrates) it.

        Opening a store with a newer class migrates it in place (one way), so a backup
        taken after construction already has the new layout and cannot restore the
        older code path. This copies the file at its current ``user_version`` and
        returns that version; the copy is integrity-checked but not upgraded.
        """
        _validate_location(path)
        copied: list[int] = []

        def verify(check: sqlite3.Connection) -> None:
            if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ControlError(E.STORE_CORRUPT)
            copied.append(int(check.execute("PRAGMA user_version").fetchone()[0]))

        _copy_database(path, destination, timeout, None, verify=verify)
        return copied[0]

    def rotate_epoch(self) -> str:
        """Start a new cursor epoch (required after restoring a backup); old cursors expire."""
        with self._transaction() as db:
            high = db.execute("SELECT value FROM counters WHERE name = 'outbox_seq'").fetchone()[0]
            epoch = secrets.token_hex(16)
            db.execute("UPDATE installation SET epoch = ?, epoch_floor = ?", (epoch, high))
            return f"{epoch}:{high}"
