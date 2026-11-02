"""SQLite resource history and atomic replay records, separate from B6 operations."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from utteran_contracts.settings import Policy, PolicyWrite, Preset, PresetWrite
from utteran_settings.errors import SettingsError

Kind = Literal["presets", "execution-policies"]
Resource = Preset | Policy
Write = PresetWrite | PolicyWrite


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _storage_error(exc: sqlite3.Error | OSError) -> SettingsError:
    number = getattr(exc, "sqlite_errorcode", 0) & 255
    code = {
        sqlite3.SQLITE_BUSY: "store_busy",
        sqlite3.SQLITE_LOCKED: "store_busy",
        sqlite3.SQLITE_FULL: "store_full",
        sqlite3.SQLITE_READONLY: "store_readonly",
        sqlite3.SQLITE_CORRUPT: "store_corrupt",
        sqlite3.SQLITE_NOTADB: "store_corrupt",
    }.get(number, "store_unavailable")
    return SettingsError(code)


class Repository:
    def __init__(self, path: Path, *, timeout: float = 2.0) -> None:
        self.path = path
        self.timeout = timeout
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.parent.is_symlink() or path.is_symlink():
                raise SettingsError("store_unsafe_path")
            if os.name != "nt":
                path.parent.chmod(0o700)
            created = False
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                created = True
            except FileExistsError:
                pass
            if os.name != "nt":
                path.chmod(0o600)
            with self._transaction() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    if not created:
                        raise SettingsError("store_schema_unsupported")
                    # DDL and metadata are transactional; no executescript implicit commit.
                    for statement in (
                        "CREATE TABLE installation (id TEXT NOT NULL, secret BLOB NOT NULL)",
                        "CREATE TABLE collections (kind TEXT PRIMARY KEY, "
                        "revision INTEGER NOT NULL)",
                        "CREATE TABLE records (kind TEXT, id TEXT, revision INTEGER, "
                        "generation INTEGER, "
                        "deleted INTEGER NOT NULL, body TEXT, PRIMARY KEY(kind,id,revision))",
                        "CREATE INDEX records_snapshot ON records(kind,generation,id)",
                        "CREATE TABLE replays (installation TEXT, method TEXT, "
                        "endpoint TEXT, key TEXT, "
                        "digest TEXT NOT NULL, status INTEGER NOT NULL, headers TEXT NOT NULL, "
                        "body TEXT NOT NULL, PRIMARY KEY(installation,method,endpoint,key))",
                    ):
                        db.execute(statement)
                    db.execute(
                        "INSERT INTO installation VALUES (?,?)",
                        (uuid4().hex, secrets.token_bytes(32)),
                    )
                    db.executemany(
                        "INSERT INTO collections VALUES (?,1)",
                        [("presets",), ("execution-policies",)],
                    )
                    db.execute("PRAGMA user_version=1")
                elif version != 1:
                    raise SettingsError("store_schema_unsupported")
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise SettingsError("store_corrupt")
                identity = db.execute("SELECT id,secret FROM installation").fetchall()
                if len(identity) != 1 or len(identity[0][1]) != 32:
                    raise SettingsError("store_corrupt")
                self._installation, self._secret = identity[0]
        except (sqlite3.Error, OSError) as exc:
            raise _storage_error(exc) from None

    @contextmanager
    def _transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
            db.execute("PRAGMA synchronous=FULL")
            # DELETE journal avoids persistently exposed WAL sidecars. SQLite creates
            # journal files with database permissions and recovers hot journals.
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

    def _decode(self, kind: Kind, body: str) -> Resource:
        try:
            return (Preset if kind == "presets" else Policy).model_validate_json(body)
        except ValidationError:
            raise SettingsError("store_corrupt") from None

    def _get(
        self, db: sqlite3.Connection, kind: Kind, resource_id: str, revision: int | None = None
    ) -> Resource:
        row = db.execute(
            "SELECT body,deleted FROM records WHERE kind=? AND id=? "
            + ("AND revision=? " if revision is not None else "")
            + "ORDER BY revision DESC LIMIT 1",
            (kind, resource_id, revision) if revision is not None else (kind, resource_id),
        ).fetchone()
        if row is None or row[1]:
            raise SettingsError("resource_not_found", 404)
        return self._decode(kind, row[0])

    def get(self, kind: Kind, resource_id: str, revision: int | None = None) -> Resource:
        with self._transaction(write=False) as db:
            return self._get(db, kind, resource_id, revision)

    def _append(
        self,
        db: sqlite3.Connection,
        kind: Kind,
        resource_id: str,
        revision: int,
        payload: Write | None,
    ) -> Resource | None:
        db.execute("UPDATE collections SET revision=revision+1 WHERE kind=?", (kind,))
        generation = db.execute(
            "SELECT revision FROM collections WHERE kind=?", (kind,)
        ).fetchone()[0]
        resource = None
        if payload is not None:
            resource = (Preset if kind == "presets" else Policy).model_validate(
                {**payload.to_wire(), "id": resource_id, "revision": revision}
            )
        db.execute(
            "INSERT INTO records VALUES (?,?,?,?,?,?)",
            (
                kind,
                resource_id,
                revision,
                generation,
                payload is None,
                canonical(resource.to_wire()) if resource else None,
            ),
        )
        return resource

    def mutate(
        self, kind: Kind, resource_id: str, expected: int, payload: Write | None
    ) -> Resource | None:
        with self._transaction() as db:
            current = self._get(db, kind, resource_id)
            if current.revision != expected:
                raise SettingsError("revision_conflict", 412)
            return self._append(db, kind, resource_id, current.revision + 1, payload)

    def post(
        self,
        endpoint: str,
        key: str,
        body: Any,
        *,
        payload: Write | Callable[[], Write] | None = None,
        evaluate: Callable[[sqlite3.Connection], dict[str, Any]] | None = None,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        digest = hmac.new(self._secret, canonical(body).encode(), hashlib.sha256).hexdigest()
        scope = (self._installation, "POST", endpoint, key)
        with self._transaction() as db:
            row = db.execute(
                "SELECT digest,status,headers,body FROM replays WHERE "
                "installation=? AND method=? AND endpoint=? AND key=?",
                scope,
            ).fetchone()
            if row is not None:
                if not hmac.compare_digest(row[0], digest):
                    raise SettingsError("idempotency_conflict", 409)
                try:
                    headers, result = json.loads(row[2]), json.loads(row[3])
                    if not isinstance(headers, dict) or not isinstance(result, dict):
                        raise ValueError
                except (ValueError, TypeError):
                    raise SettingsError("store_corrupt") from None
                return row[1], headers, result
            if payload is not None:
                if callable(payload):
                    payload = payload()
                kind: Kind = "presets" if isinstance(payload, PresetWrite) else "execution-policies"
                if endpoint != f"/api/v1/{kind}":
                    raise SettingsError("invalid_resource_kind", 422)
                prefix = "pre_" if kind == "presets" else "pol_"
                resource = self._append(db, kind, prefix + uuid4().hex, 1, payload)
                assert resource is not None
                status, headers, result = (
                    201,
                    {"ETag": '"1"', "Location": endpoint + "/" + resource.id},
                    resource.to_wire(),
                )
            else:
                assert evaluate is not None
                result = evaluate(db)
                status, headers = 200, {}
            db.execute(
                "INSERT INTO replays VALUES (?,?,?,?,?,?,?,?)",
                (*scope, digest, status, canonical(headers), canonical(result)),
            )
            return status, headers, result

    def list(self, kind: Kind, cursor: str | None = None, *, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise SettingsError("cursor_invalid", 400)
        snapshot, after = None, ""
        if cursor:
            try:
                raw, signature = cursor.split(".")
                expected = hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError
                data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
                if data[0] != kind or type(data[1]) is not int or not isinstance(data[2], str):
                    raise ValueError
                snapshot, after = data[1:]
            except (ValueError, TypeError, IndexError, UnicodeError):
                raise SettingsError("cursor_invalid", 400) from None
        with self._transaction(write=False) as db:
            current = db.execute(
                "SELECT revision FROM collections WHERE kind=?", (kind,)
            ).fetchone()[0]
            if snapshot is None:
                snapshot = current
            if not 1 <= snapshot <= current:
                raise SettingsError("cursor_expired", 409)
            rows = db.execute(
                "SELECT r.id,r.body FROM records r WHERE r.kind=? AND r.id>? AND r.generation<=? "
                "AND r.revision=(SELECT MAX(s.revision) FROM records s WHERE s.kind=r.kind "
                "AND s.id=r.id AND s.generation<=?) AND r.deleted=0 ORDER BY r.id LIMIT ?",
                (kind, after, snapshot, snapshot, limit + 1),
            ).fetchall()
            next_cursor = None
            if len(rows) > limit:
                raw = (
                    base64.urlsafe_b64encode(
                        canonical([kind, snapshot, rows[limit - 1][0]]).encode()
                    )
                    .decode()
                    .rstrip("=")
                )
                next_cursor = (
                    raw + "." + hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()
                )
            items = [self._decode(kind, row[1]).to_wire() for row in rows[:limit]]
            return {
                "schema_version": 1,
                "revision": snapshot,
                "items": items,
                "next_cursor": next_cursor,
            }
