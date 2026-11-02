"""B8 setup persistence on top of the B7 job store (storage layout v4).

``SetupControlStore`` adds immutable plan bodies, one-use approvals, the operation <-> plan
link, public step progress and a private step journal. Every mutation commits resource
state, queue metadata, revision history, outbox events and the idempotent response in one
``BEGIN IMMEDIATE`` (the B6 primitives). Setup operations and B7 jobs share the single
``heavy`` lease. Nothing here observes the machine, starts processes or serves HTTP.

Layout compatibility: B6 ``ControlStore`` (v2) and B7 ``JobControlStore`` (v3) refuse a v4
file with ``store_schema_unsupported``; v1/v2/v3 files migrate to v4 in one transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, ClassVar, Final, Literal

from pydantic import ValidationError

from utteran_contracts.control import OperationStatus, ProcessIdentity, ReasonCode
from utteran_contracts.setup import (
    APPROVAL_TTL_SECONDS,
    PLAN_TTL_SECONDS,
    SETUP_MODELS,
    Approval,
    ApprovalRequest,
    EvidenceKind,
    PlanContent,
    SetupErrorCode,
    SetupOperation,
    SetupOperationList,
    SetupPlan,
    SetupStart,
    StepReason,
    StepStatus,
    WaitingReason,
)
from utteran_control import transitions
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.job_repository import (
    LIST_CAPACITY,
    LIST_MAX_ITEMS,
    LIST_TOTAL_ITEMS,
    LIST_TTL,
    REQUIRED_TABLES_V3,
    JobControlStore,
    _ListSnapshot,
    _ListSnapshots,
)
from utteran_control.store import IdempotencyScope, _new_id, _ts, canonical

S = OperationStatus
R = ReasonCode
E = ErrorCode

SETUP_SCHEMA_VERSION: Final = 4
MAX_SETUP_LIST: Final = 100
_SETUP_CURSOR = re.compile(
    r"s2\.([0-9a-f]{32})\.([1-9][0-9]{0,8})\.([1-9][0-9]{0,2})\.([0-9a-f]{64})"
)
ReplayKind = Literal["plan", "approval", "operation"]

_DDL_V4: Final = (
    # Store-local observation counter: +1 whenever the observed setup inventory changes.
    "CREATE TABLE setup_inventory (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
    "revision INTEGER NOT NULL CHECK (revision >= 1), "
    "fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64), observed_at TEXT NOT NULL)",
    "CREATE TABLE setup_plans (id TEXT PRIMARY KEY, "
    "revision INTEGER NOT NULL CHECK (revision >= 1), state TEXT NOT NULL CHECK "
    "(state IN ('proposed','approved','consumed','discarded')), "
    "body TEXT NOT NULL, plan_hash TEXT NOT NULL CHECK (length(plan_hash) = 64), "
    "inventory_revision INTEGER NOT NULL, created_at TEXT NOT NULL, "
    "expires_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    # The hashed body is immutable; only state/revision/updated_at may change.
    "CREATE TRIGGER setup_plans_body_immutable BEFORE UPDATE OF id, body, plan_hash, "
    "inventory_revision, created_at, expires_at ON setup_plans "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TRIGGER setup_plans_no_delete BEFORE DELETE ON setup_plans "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    "CREATE TABLE setup_approvals (id TEXT PRIMARY KEY, "
    "plan_id TEXT NOT NULL REFERENCES setup_plans(id), plan_revision INTEGER NOT NULL, "
    "plan_hash TEXT NOT NULL, agreements TEXT NOT NULL, expires_at TEXT NOT NULL, "
    "created_at TEXT NOT NULL, consumed_at TEXT, operation_id TEXT REFERENCES operations(id), "
    "CHECK ((consumed_at IS NULL) = (operation_id IS NULL)))",
    "CREATE TRIGGER setup_approvals_no_delete BEFORE DELETE ON setup_approvals "
    "BEGIN SELECT RAISE(ABORT, 'immutable'); END",
    # Operation <-> plan link (immutable). The first operation consumes the approval.
    "CREATE TABLE setup_operations (operation_id TEXT PRIMARY KEY REFERENCES operations(id), "
    "plan_id TEXT NOT NULL REFERENCES setup_plans(id), plan_revision INTEGER NOT NULL, "
    "plan_hash TEXT NOT NULL, approval_id TEXT NOT NULL REFERENCES setup_approvals(id), "
    "retry_of TEXT REFERENCES operations(id), created_at TEXT NOT NULL)",
    "CREATE UNIQUE INDEX setup_operations_one_use ON setup_operations(approval_id) "
    "WHERE retry_of IS NULL",
    # Mutable side state with fixed vocabularies only.
    "CREATE TABLE setup_state (operation_id TEXT PRIMARY KEY REFERENCES operations(id), "
    "error_code TEXT, error_step TEXT, waiting_reason TEXT, last_fingerprint TEXT)",
    # Public step progress (fixed vocabularies, no output text).
    "CREATE TABLE setup_steps (operation_id TEXT NOT NULL REFERENCES operations(id), "
    "step_id TEXT NOT NULL, ord INTEGER NOT NULL, kind TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK (status IN "
    "('pending','running','succeeded','failed','interrupted','skipped')), "
    "reason_code TEXT, evidence_kind TEXT, observed_at TEXT, attempt_id TEXT, "
    "PRIMARY KEY (operation_id, step_id), UNIQUE (operation_id, ord))",
    # Private, append-only step journal: intent before spawn, then launch / exit / result.
    "CREATE TABLE setup_step_journal (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
    "operation_id TEXT NOT NULL REFERENCES operations(id), "
    "attempt_id TEXT NOT NULL REFERENCES attempts(id), step_id TEXT NOT NULL, "
    "event TEXT NOT NULL CHECK (event IN "
    "('intent','launched','launch_failed','exited','result')), "
    "child_pid INTEGER, child_start TEXT, exit_code INTEGER, detail TEXT, "
    "recorded_at TEXT NOT NULL)",
    # Plan / Approval / SetupOperation responses (kind-checked; one scope, one kind).
    "CREATE TABLE setup_replays (installation TEXT NOT NULL, method TEXT NOT NULL, "
    "endpoint TEXT NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL, "
    "status INTEGER NOT NULL, headers TEXT NOT NULL, body TEXT NOT NULL, "
    "kind TEXT NOT NULL CHECK (kind IN ('plan','approval','operation')), "
    "resource_id TEXT NOT NULL, created_at TEXT NOT NULL, "
    "PRIMARY KEY (installation, method, endpoint, key))",
    *(
        f"CREATE TRIGGER {table}_immutable_{verb} BEFORE {verb} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'immutable'); END"
        for table in ("setup_operations", "setup_step_journal", "setup_replays")
        for verb in ("UPDATE", "DELETE")
    ),
)
REQUIRED_TABLES_V4: Final = REQUIRED_TABLES_V3 | {
    "setup_inventory",
    "setup_plans",
    "setup_approvals",
    "setup_operations",
    "setup_state",
    "setup_steps",
    "setup_step_journal",
    "setup_replays",
}
RETRYABLE_SETUP: Final = frozenset({S.FAILED, S.CANCELLED})
RESUMABLE_SETUP: Final = frozenset({S.WAITING_USER, S.INTERRUPTED})
_SATISFIED: Final = frozenset({"succeeded", "skipped"})


@dataclass(frozen=True)
class AcceptedSetup:
    """Accepted setup mutation; replays reproduce status, headers and body exactly."""

    status: int
    headers: MappingProxyType[str, str]
    body: dict[str, Any]
    kind: ReplayKind
    replayed: bool


def plan_hash(content: PlanContent) -> str:
    return hashlib.sha256(canonical(content.to_wire()).encode()).hexdigest()


def required_credentials(model_refs: Collection[str]) -> frozenset[str]:
    gated = any(SETUP_MODELS[ref].gated for ref in model_refs if ref in SETUP_MODELS)
    return frozenset({"hf_token"}) if gated else frozenset()


class SetupControlStore(JobControlStore):
    """B7 store opened at layout v4 (setup plans / approvals / operations)."""

    TARGET_VERSION: ClassVar[int] = SETUP_SCHEMA_VERSION
    REPLAY_TABLES: ClassVar[tuple[str, ...]] = (*JobControlStore.REPLAY_TABLES, "setup_replays")

    def __init__(self, path: Any, **kwargs: Any) -> None:
        self._setup_lists = _ListSnapshots(
            clock=kwargs.get("list_clock"),
            ttl=kwargs.get("list_ttl", LIST_TTL),
            capacity=kwargs.get("list_capacity", LIST_CAPACITY),
            max_items=kwargs.get("list_max_items", LIST_MAX_ITEMS),
            total_items=kwargs.get("list_total_items", LIST_TOTAL_ITEMS),
        )
        super().__init__(path, **kwargs)

    @classmethod
    def _layout(cls, version: int) -> tuple[tuple[str, ...], frozenset[str]]:
        if version == SETUP_SCHEMA_VERSION:
            return _DDL_V4, REQUIRED_TABLES_V4
        return super()._layout(version)

    # ------------------------------------------------------------------ replays

    def setup_digest(self, body: Any) -> str:
        return self._digest(body)

    def _setup_replay(
        self, db: sqlite3.Connection, scope: IdempotencyScope, digest: str, kind: ReplayKind
    ) -> AcceptedSetup | None:
        row = db.execute(
            "SELECT digest, status, headers, body, kind FROM setup_replays WHERE "
            "installation = ? AND method = 'POST' AND endpoint = ? AND key = ?",
            (self._installation, scope.endpoint, scope.key),
        ).fetchone()
        if row is None:
            self._scope_free(db, "setup_replays", scope)
            return None
        if row[4] != kind or not hmac.compare_digest(row[0], digest):
            raise ControlError(E.IDEMPOTENCY_CONFLICT)
        try:
            body = json.loads(row[3])
            models: dict[str, Any] = {
                "plan": SetupPlan,
                "approval": Approval,
                "operation": SetupOperation,
            }
            model = models[kind]
            model.model_validate(body)
            return AcceptedSetup(row[1], MappingProxyType(self._headers(row[2])), body, kind, True)
        except (ValueError, ValidationError):
            raise ControlError(E.STORE_CORRUPT) from None

    def _remember_setup(
        self,
        db: sqlite3.Connection,
        scope: IdempotencyScope,
        digest: str,
        status: int,
        headers: dict[str, str],
        body: dict[str, Any],
        kind: ReplayKind,
        resource_id: str,
    ) -> AcceptedSetup:
        db.execute(
            "INSERT INTO setup_replays VALUES (?,'POST',?,?,?,?,?,?,?,?,?)",
            (
                self._installation,
                scope.endpoint,
                scope.key,
                digest,
                status,
                canonical(headers),
                canonical(body),
                kind,
                resource_id,
                self._now(),
            ),
        )
        return AcceptedSetup(status, MappingProxyType(headers), body, kind, False)

    def lookup_setup(
        self, scope: IdempotencyScope, digest: str, kind: ReplayKind
    ) -> AcceptedSetup | None:
        """Replay lookup only; lets callers answer a replay before observing the machine."""
        self._validate_scope(scope)
        with self._transaction(write=False) as db:
            return self._setup_replay(db, scope, digest, kind)

    # ------------------------------------------------------------------ plans

    def _expired(self, value: str) -> bool:
        return self._now() >= value

    def _plan_row(self, db: sqlite3.Connection, plan_id: str) -> tuple[Any, ...]:
        row = db.execute(
            "SELECT id, revision, state, body, plan_hash, inventory_revision, created_at, "
            "expires_at FROM setup_plans WHERE id = ?",
            (plan_id,),
        ).fetchone()
        if row is None or row[2] == "discarded":
            raise ControlError(E.RESOURCE_NOT_FOUND)
        if hashlib.sha256(row[3].encode()).hexdigest() != row[4]:
            raise ControlError(E.STORE_CORRUPT)  # never trust a rewritten plan body
        return tuple(row)

    @staticmethod
    def _content(body: str) -> PlanContent:
        try:
            return PlanContent.model_validate_json(body)
        except (ValidationError, ValueError):
            raise ControlError(E.STORE_CORRUPT) from None

    def _plan_view(self, db: sqlite3.Connection, plan_id: str) -> SetupPlan:
        row = self._plan_row(db, plan_id)
        content = self._content(row[3])
        state = row[2]
        if state in ("proposed", "approved") and self._expired(row[7]):
            state = "expired"  # computed; a GET never writes
        wire = content.to_wire()
        wire.pop("schema_version")
        try:
            return SetupPlan.model_validate(
                {
                    **wire,
                    "schema_version": 1,
                    "id": row[0],
                    "revision": row[1],
                    "state": state,
                    "inventory_revision": row[5],
                    "plan_hash": row[4],
                    "created_at": row[6],
                    "expires_at": row[7],
                }
            )
        except ValidationError:
            raise ControlError(E.STORE_CORRUPT) from None

    def create_plan(
        self, scope: IdempotencyScope, digest: str, content: PlanContent
    ) -> AcceptedSetup:
        """Record a proposed plan (201). No environment change of any kind happens here."""
        self._validate_scope(scope)
        body = canonical(content.to_wire())
        with self._transaction() as db:
            replay = self._setup_replay(db, scope, digest, "plan")
            if replay is not None:
                return replay
            now = self._clock()
            inventory = db.execute("SELECT revision, fingerprint FROM setup_inventory").fetchone()
            if inventory is None:
                revision = 1
                db.execute(
                    "INSERT INTO setup_inventory VALUES (1,1,?,?)",
                    (content.inventory_fingerprint, _ts(now)),
                )
            elif inventory[1] != content.inventory_fingerprint:
                revision = int(inventory[0]) + 1
                db.execute(
                    "UPDATE setup_inventory SET revision = ?, fingerprint = ?, observed_at = ?",
                    (revision, content.inventory_fingerprint, _ts(now)),
                )
            else:
                revision = int(inventory[0])
            plan_id = _new_id("plan_")
            db.execute(
                "INSERT INTO setup_plans VALUES (?,1,'proposed',?,?,?,?,?,?)",
                (
                    plan_id,
                    body,
                    hashlib.sha256(body.encode()).hexdigest(),
                    revision,
                    _ts(now),
                    _ts(now + timedelta(seconds=PLAN_TTL_SECONDS)),
                    _ts(now),
                ),
            )
            view = self._plan_view(db, plan_id)
            headers = {"ETag": f'"{view.revision}"', "Location": f"/api/v1/setup-plans/{plan_id}"}
            return self._remember_setup(
                db, scope, digest, 201, headers, view.to_wire(), "plan", plan_id
            )

    def get_plan(self, plan_id: str) -> SetupPlan:
        with self._transaction(write=False) as db:
            return self._plan_view(db, plan_id)

    def get_plan_content(self, plan_id: str) -> PlanContent:
        with self._transaction(write=False) as db:
            return self._content(self._plan_row(db, plan_id)[3])

    def discard_plan(self, plan_id: str, expected_revision: int) -> None:
        """Tombstone an unconsumed plan; its approvals become unusable (204)."""
        with self._transaction() as db:
            row = self._plan_row(db, plan_id)
            if row[1] != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            if row[2] == "consumed":
                raise ControlError(E.PLAN_CONSUMED)
            db.execute(
                "UPDATE setup_plans SET state = 'discarded', revision = revision + 1, "
                "updated_at = ? WHERE id = ?",
                (self._now(), plan_id),
            )

    def approve_plan(
        self,
        plan_id: str,
        expected_revision: int,
        scope: IdempotencyScope,
        request: ApprovalRequest,
        *,
        fingerprint: str,
        credentials: Collection[str],
    ) -> AcceptedSetup:
        """Bind a one-use approval to the exact plan hash, revision and inventory."""
        self._validate_scope(scope)
        digest = self._digest(
            {"op": "approve", "plan": plan_id, "expected": expected_revision, **request.to_wire()}
        )
        with self._transaction() as db:
            replay = self._setup_replay(db, scope, digest, "approval")
            if replay is not None:
                return replay
            row = self._plan_row(db, plan_id)
            if row[1] != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            if self._expired(row[7]):
                raise ControlError(E.PLAN_EXPIRED)
            if row[2] == "approved":
                raise ControlError(E.PLAN_ALREADY_APPROVED)
            if row[2] == "consumed":
                raise ControlError(E.PLAN_CONSUMED)
            if not hmac.compare_digest(row[4], request.plan_hash):
                raise ControlError(E.PLAN_HASH_MISMATCH)
            content = self._content(row[3])
            if not hmac.compare_digest(content.inventory_fingerprint, fingerprint):
                raise ControlError(E.INVENTORY_STALE)
            accepted = set(request.accepted_agreement_ids)
            required = set(content.required_agreements)
            if accepted - required or len(accepted) != len(request.accepted_agreement_ids):
                raise ControlError(E.AGREEMENT_UNKNOWN)
            if required - accepted:
                raise ControlError(E.AGREEMENT_REQUIRED)  # never assumed on the user's behalf
            if required_credentials(content.model_refs) - set(credentials):
                raise ControlError(E.CREDENTIAL_REQUIRED)
            now = self._clock()
            plan_expiry = datetime.fromisoformat(row[7].replace("Z", "+00:00"))
            expires = min(now + timedelta(seconds=APPROVAL_TTL_SECONDS), plan_expiry)
            revision = int(row[1]) + 1
            db.execute(
                "UPDATE setup_plans SET state = 'approved', revision = ?, updated_at = ? "
                "WHERE id = ?",
                (revision, _ts(now), plan_id),
            )
            approval_id = _new_id("apr_")
            agreements = tuple(sorted(accepted))
            db.execute(
                "INSERT INTO setup_approvals VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                (
                    approval_id,
                    plan_id,
                    revision,
                    row[4],
                    canonical(list(agreements)),
                    _ts(expires),
                    _ts(now),
                ),
            )
            approval = Approval(
                schema_version=1,
                id=approval_id,
                plan_id=plan_id,
                plan_revision=revision,
                plan_hash=row[4],
                accepted_agreement_ids=agreements,
                expires_at=expires,
            )
            headers = {"ETag": f'"{revision}"', "Location": f"/api/v1/setup-plans/{plan_id}"}
            return self._remember_setup(
                db, scope, digest, 201, headers, approval.to_wire(), "approval", approval_id
            )

    # ------------------------------------------------------------------ operations

    def _insert_setup(
        self,
        db: sqlite3.Connection,
        content: PlanContent,
        *,
        plan_id: str,
        plan_revision: int,
        hash_: str,
        approval_id: str,
        retry_of: str | None,
    ) -> str:
        operation_id = _new_id("setup_")
        now = self._now()
        db.execute(
            "INSERT INTO operations VALUES (?,'setup_operation',?,1,?,NULL,?,?)",
            (operation_id, S.QUEUED.value, R.SUBMITTED.value, now, now),
        )
        db.execute(
            "INSERT INTO operation_private VALUES (?,?)",
            (operation_id, canonical({"b8_plan_hash": hash_})),
        )
        db.execute(
            "INSERT INTO setup_operations VALUES (?,?,?,?,?,?,?)",
            (operation_id, plan_id, plan_revision, hash_, approval_id, retry_of, now),
        )
        db.execute("INSERT INTO setup_state VALUES (?,NULL,NULL,NULL,NULL)", (operation_id,))
        db.executemany(
            "INSERT INTO setup_steps VALUES (?,?,?,?,'pending',NULL,NULL,NULL,NULL)",
            [(operation_id, step.id, index, step.kind) for index, step in enumerate(content.steps)],
        )
        self._journal(db, operation_id, 1, S.QUEUED, R.SUBMITTED, None, None)
        self._enqueue(db, operation_id)
        return operation_id

    def _link(self, db: sqlite3.Connection, operation_id: str) -> tuple[Any, ...]:
        row = db.execute(
            "SELECT plan_id, plan_revision, plan_hash, approval_id, retry_of "
            "FROM setup_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise ControlError(E.RESOURCE_NOT_FOUND)  # not a B8 setup operation
        return tuple(row)

    def _setup_view(self, db: sqlite3.Connection, operation_id: str) -> SetupOperation:
        operation = self._view(db, operation_id)
        if operation.kind != "setup_operation":
            raise ControlError(E.RESOURCE_NOT_FOUND)
        plan_id, plan_revision, _, _, retry_of = self._link(db, operation_id)
        content = self._content(
            db.execute("SELECT body FROM setup_plans WHERE id = ?", (plan_id,)).fetchone()[0]
        )
        state = db.execute(
            "SELECT error_code, error_step, waiting_reason FROM setup_state WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        steps = db.execute(
            "SELECT step_id, kind, status, reason_code, evidence_kind, observed_at "
            "FROM setup_steps WHERE operation_id = ? ORDER BY ord",
            (operation_id,),
        ).fetchall()
        attempts = db.execute(
            "SELECT id, number, state, launch, started_at, ended_at FROM attempts "
            "WHERE operation_id = ? ORDER BY number",
            (operation_id,),
        ).fetchall()
        interrupted = operation.status is S.INTERRUPTED
        try:
            return SetupOperation.model_validate(
                {
                    "schema_version": 1,
                    "id": operation_id,
                    "revision": operation.revision,
                    "plan_ref": {"id": plan_id, "revision": plan_revision},
                    "profile_kind": content.profile_kind,
                    "status": operation.status,
                    "held": operation.held,
                    "recovery_required": operation.recovery_required,
                    "reason_code": operation.reason_code,
                    "waiting_reason": state[2] if state else None,
                    "attempt_number": attempts[-1][1] if attempts else None,
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
                    "steps": [
                        {
                            "step_id": s[0],
                            "kind": s[1],
                            "status": "interrupted" if interrupted and s[2] == "running" else s[2],
                            "reason_code": s[3],
                            "evidence": None
                            if s[4] is None
                            else {"kind": s[4], "observed_at": s[5]},
                        }
                        for s in steps
                    ],
                    "retry_of": retry_of,
                    "error": None
                    if not state or state[0] is None
                    else {"code": state[0], "step_id": state[1]},
                    "created_at": operation.created_at,
                    "updated_at": operation.updated_at,
                }
            )
        except ValidationError:
            raise ControlError(E.STORE_CORRUPT) from None

    def _remember_operation(
        self,
        db: sqlite3.Connection,
        scope: IdempotencyScope,
        digest: str,
        status: int,
        operation_id: str,
        *,
        location: bool,
    ) -> AcceptedSetup:
        view = self._setup_view(db, operation_id)
        headers = {"ETag": f'"{view.revision}"'}
        if location:
            headers["Location"] = f"/api/v1/setup-operations/{view.id}"
        return self._remember_setup(
            db, scope, digest, status, headers, view.to_wire(), "operation", view.id
        )

    def start_operation(
        self, scope: IdempotencyScope, digest: str, start: SetupStart, *, fingerprint: str
    ) -> AcceptedSetup:
        """Consume plan + approval and enqueue one SetupOperation, all in one transaction.

        Replay precedes every expiry, CAS and input check, so a lost response is answered
        with the original status / body / ETag / Location and never creates a second
        operation. Any failure before commit leaves every table unchanged.
        """
        self._validate_scope(scope)
        with self._transaction() as db:
            replay = self._setup_replay(db, scope, digest, "operation")
            if replay is not None:
                return replay
            row = self._plan_row(db, start.plan_ref.id)
            if self._expired(row[7]):
                raise ControlError(E.PLAN_EXPIRED)
            if row[2] == "consumed":
                raise ControlError(E.PLAN_CONSUMED)
            if row[2] != "approved":
                raise ControlError(E.PLAN_NOT_APPROVED)
            if row[1] != start.plan_ref.revision:
                raise ControlError(E.PLAN_REVISION_MISMATCH)
            approval = db.execute(
                "SELECT plan_id, plan_revision, plan_hash, expires_at, consumed_at "
                "FROM setup_approvals WHERE id = ?",
                (start.approval_id,),
            ).fetchone()
            if approval is None or approval[0] != row[0] or approval[1] != row[1]:
                raise ControlError(E.APPROVAL_INVALID)
            if approval[4] is not None:
                raise ControlError(E.APPROVAL_CONSUMED)
            if self._expired(approval[3]):
                raise ControlError(E.APPROVAL_EXPIRED)
            if not hmac.compare_digest(approval[2], row[4]):
                raise ControlError(E.PLAN_HASH_MISMATCH)
            content = self._content(row[3])
            if not hmac.compare_digest(content.inventory_fingerprint, fingerprint):
                raise ControlError(E.INVENTORY_STALE)
            operation_id = self._insert_setup(
                db,
                content,
                plan_id=row[0],
                plan_revision=row[1],
                hash_=row[4],
                approval_id=start.approval_id,
                retry_of=None,
            )
            now = self._now()
            db.execute(
                "UPDATE setup_approvals SET consumed_at = ?, operation_id = ? WHERE id = ?",
                (now, operation_id, start.approval_id),
            )
            db.execute(
                "UPDATE setup_plans SET state = 'consumed', revision = revision + 1, "
                "updated_at = ? WHERE id = ?",
                (now, row[0]),
            )
            self._queue_changed(db, R.SUBMITTED)
            if self._fault is not None:
                self._fault("setup_before_replay")
            return self._remember_operation(db, scope, digest, 202, operation_id, location=True)

    def _cas_setup(self, db: sqlite3.Connection, operation_id: str, expected: int) -> S:
        kind, status, revision, _ = self._row(db, operation_id)
        if kind != "setup_operation":
            raise ControlError(E.RESOURCE_NOT_FOUND)
        self._link(db, operation_id)
        if revision != expected:
            raise ControlError(E.REVISION_CONFLICT)
        return status

    def get_setup(self, operation_id: str) -> SetupOperation:
        with self._transaction(write=False) as db:
            return self._setup_view(db, operation_id)

    def setup_plan_for(self, operation_id: str) -> tuple[PlanContent, str | None]:
        """Private read for the executor: the hashed plan and the last observed fingerprint."""
        with self._transaction(write=False) as db:
            plan_id = self._link(db, operation_id)[0]
            content = self._content(self._plan_row(db, plan_id)[3])
            last = db.execute(
                "SELECT last_fingerprint FROM setup_state WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return content, (last[0] if last else None)

    def cancel_setup(
        self, operation_id: str, expected_revision: int, scope: IdempotencyScope
    ) -> AcceptedSetup:
        """Waiting setups end atomically; running ones become ``cancelling`` (lease kept)."""
        self._validate_scope(scope)
        digest = self._digest(
            {"op": "cancel", "setup": operation_id, "expected_revision": expected_revision}
        )
        with self._transaction() as db:
            replay = self._setup_replay(db, scope, digest, "operation")
            if replay is not None:
                return replay
            status = self._cas_setup(db, operation_id, expected_revision)
            if status in transitions.TERMINAL:
                return self._remember_operation(
                    db, scope, digest, 200, operation_id, location=False
                )
            if status is S.CANCELLING:
                return self._remember_operation(
                    db, scope, digest, 202, operation_id, location=False
                )
            if status in (S.QUEUED, S.WAITING_USER, S.INTERRUPTED):
                queued = (
                    db.execute(
                        "SELECT 1 FROM queue_items WHERE operation_id = ?", (operation_id,)
                    ).fetchone()
                    is not None
                )
                self._release(db, operation_id, "released")
                self._record(db, operation_id, S.CANCELLED, R.USER_CANCEL)
                if queued:
                    self._queue_changed(db, R.USER_CANCEL)
            elif status is S.RUNNING:
                lease = db.execute(
                    "SELECT attempt_id FROM leases WHERE operation_id = ?", (operation_id,)
                ).fetchone()
                if lease is None:
                    raise ControlError(E.STORE_CORRUPT)
                self._record(db, operation_id, S.CANCELLING, R.CANCEL_ACCEPTED, attempt_id=lease[0])
            else:
                raise ControlError(E.INVALID_TRANSITION)
            return self._remember_operation(db, scope, digest, 202, operation_id, location=False)

    def _revalidate(
        self,
        db: sqlite3.Connection,
        operation_id: str,
        fingerprint: str,
        credentials: Collection[str],
    ) -> tuple[Any, ...]:
        """Approval still valid and inventory unchanged (plan or last attempt), else 409."""
        link = self._link(db, operation_id)
        plan = self._plan_row(db, link[0])
        content = self._content(plan[3])
        approval = db.execute(
            "SELECT expires_at FROM setup_approvals WHERE id = ?", (link[3],)
        ).fetchone()
        if approval is None or self._expired(approval[0]):
            raise ControlError(E.REAPPROVAL_REQUIRED)
        last = db.execute(
            "SELECT last_fingerprint FROM setup_state WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        known = {content.inventory_fingerprint, *(v for v in (last[0] if last else None,) if v)}
        if fingerprint not in known:
            raise ControlError(E.REAPPROVAL_REQUIRED)
        if required_credentials(content.model_refs) - set(credentials):
            raise ControlError(E.CREDENTIAL_REQUIRED)
        return (*link, content)

    def retry_setup(
        self,
        operation_id: str,
        expected_revision: int,
        scope: IdempotencyScope,
        *,
        fingerprint: str,
        credentials: Collection[str],
    ) -> AcceptedSetup:
        """New SetupOperation (``retry_of``) for the same approved plan; source untouched."""
        self._validate_scope(scope)
        digest = self._digest(
            {"op": "retry", "setup": operation_id, "expected_revision": expected_revision}
        )
        with self._transaction() as db:
            replay = self._setup_replay(db, scope, digest, "operation")
            if replay is not None:
                return replay
            status = self._cas_setup(db, operation_id, expected_revision)
            if status not in RETRYABLE_SETUP:
                raise ControlError(E.INVALID_TRANSITION)
            plan_id, plan_revision, hash_, approval_id, _, content = self._revalidate(
                db, operation_id, fingerprint, credentials
            )
            new_id = self._insert_setup(
                db,
                content,
                plan_id=plan_id,
                plan_revision=plan_revision,
                hash_=hash_,
                approval_id=approval_id,
                retry_of=operation_id,
            )
            last = db.execute(
                "SELECT last_fingerprint FROM setup_state WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            db.execute(
                "UPDATE setup_state SET last_fingerprint = ? WHERE operation_id = ?",
                (last[0] if last else None, new_id),
            )
            self._queue_changed(db, R.SUBMITTED)
            return self._remember_operation(db, scope, digest, 202, new_id, location=True)

    def resume_setup(
        self,
        operation_id: str,
        expected_revision: int,
        scope: IdempotencyScope,
        *,
        fingerprint: str,
        credentials: Collection[str],
    ) -> AcceptedSetup:
        """waiting_user / interrupted -> queued (tail); the next dispatch is a new attempt."""
        self._validate_scope(scope)
        digest = self._digest(
            {"op": "resume", "setup": operation_id, "expected_revision": expected_revision}
        )
        with self._transaction() as db:
            replay = self._setup_replay(db, scope, digest, "operation")
            if replay is not None:
                return replay
            status = self._cas_setup(db, operation_id, expected_revision)
            if status not in RESUMABLE_SETUP:
                raise ControlError(E.INVALID_TRANSITION)
            self._revalidate(db, operation_id, fingerprint, credentials)
            transitions.check("setup_operation", status, S.QUEUED)
            self._enqueue(db, operation_id)
            db.execute(
                "UPDATE setup_steps SET status = 'pending', reason_code = NULL, "
                "evidence_kind = NULL, observed_at = NULL WHERE operation_id = ? "
                "AND status IN ('running','failed','interrupted')",
                (operation_id,),
            )
            db.execute(
                "UPDATE setup_state SET waiting_reason = NULL, error_code = NULL, "
                "error_step = NULL WHERE operation_id = ?",
                (operation_id,),
            )
            self._record(db, operation_id, S.QUEUED, R.EXPLICIT_RESUME)
            self._queue_changed(db, R.EXPLICIT_RESUME)
            return self._remember_operation(db, scope, digest, 202, operation_id, location=True)

    # ------------------------------------------------------------------ listing

    def _setup_mac(self, snapshot_id: str, offset: int, limit: int, scope: str) -> str:
        message = f"setups:v1:{snapshot_id}:{offset}:{limit}:filter=all:{scope}".encode()
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    def _setup_page(
        self, snapshot: _ListSnapshot, offset: int, limit: int, scope: str
    ) -> SetupOperationList:
        end = offset + limit
        next_cursor = None
        if end < len(snapshot.items):
            mac = self._setup_mac(snapshot.id, end, limit, scope)
            next_cursor = f"s2.{snapshot.id}.{end}.{limit}.{mac}"
        return SetupOperationList(
            schema_version=1,
            revision=snapshot.revision,
            items=snapshot.items[offset:end],
            next_cursor=next_cursor,
        )

    def list_setups(
        self, cursor: str | None = None, *, limit: int = MAX_SETUP_LIST, scope: str = ""
    ) -> SetupOperationList:
        """Point-in-time paging (same semantics as B7 ``GET /jobs``; ``s2`` cursors only)."""
        if type(limit) is not int or not 1 <= limit <= MAX_SETUP_LIST or not isinstance(scope, str):
            raise ControlError(E.REQUEST_INVALID)
        if cursor is not None:
            match = _SETUP_CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
            if match is None:
                raise ControlError(E.CURSOR_INVALID)
            snapshot_id, offset, page = match.group(1), int(match.group(2)), int(match.group(3))
            expected = self._setup_mac(snapshot_id, offset, page, scope)
            if page != limit or not hmac.compare_digest(match.group(4), expected):
                raise ControlError(E.CURSOR_INVALID)
            return self._setup_page(self._setup_lists.get(snapshot_id, scope), offset, limit, scope)
        with self._transaction(write=False) as db:
            total = db.execute("SELECT count(*) FROM setup_operations").fetchone()[0]
            if total > self._setup_lists.max_items:
                raise ControlError(E.LIST_SNAPSHOT_TOO_LARGE)
            rows = db.execute("SELECT operation_id FROM setup_operations ORDER BY rowid").fetchall()
            revision = db.execute(
                "SELECT value FROM counters WHERE name = 'operations_revision'"
            ).fetchone()[0]
            snapshot = _ListSnapshot(
                id=secrets.token_hex(16),
                scope=scope,
                revision=max(revision, 1),
                items=tuple(self._setup_view(db, row[0]) for row in rows),
            )
        if len(snapshot.items) > limit:
            self._setup_lists.put(snapshot)
        return self._setup_page(snapshot, 0, limit, scope)

    # ------------------------------------------------------------------ dispatch

    def _dispatch_guard(self, db: sqlite3.Connection, operation_id: str) -> None:
        """A B7 job or a B8 setup that is the *first* eligible item may take the slot."""
        known = db.execute(
            "SELECT 1 FROM job_snapshots WHERE operation_id = ? UNION ALL "
            "SELECT 1 FROM setup_operations WHERE operation_id = ?",
            (operation_id, operation_id),
        ).fetchone()
        if known is None:
            raise ControlError(E.NOT_DISPATCHABLE)
        first = db.execute(
            "SELECT operation_id FROM queue_items WHERE active = 0 AND held = 0 "
            "AND blockers = '' ORDER BY ord LIMIT 1"
        ).fetchone()
        if first is None or first[0] != operation_id:
            raise ControlError(E.NOT_DISPATCHABLE)

    def next_dispatchable(self) -> tuple[str, int] | None:
        """First eligible waiting B7 job or B8 setup (id, revision). Never skips past others."""
        with self._transaction(write=False) as db:
            if db.execute("SELECT dispatch FROM queue").fetchone()[0] != "running":
                return None
            if db.execute("SELECT 1 FROM leases WHERE slot = 'heavy'").fetchone():
                return None
            row = db.execute(
                "SELECT q.operation_id, o.revision, s.operation_id, u.operation_id "
                "FROM queue_items q JOIN operations o ON o.id = q.operation_id "
                "LEFT JOIN job_snapshots s ON s.operation_id = q.operation_id "
                "LEFT JOIN setup_operations u ON u.operation_id = q.operation_id "
                "WHERE q.active = 0 AND q.held = 0 AND q.blockers = '' ORDER BY q.ord LIMIT 1"
            ).fetchone()
            if row is None or (row[2] is None and row[3] is None):
                return None
            return str(row[0]), int(row[1])

    def _step_journal(
        self,
        db: sqlite3.Connection,
        operation_id: str,
        attempt_id: str,
        step_id: str,
        event: str,
        *,
        child: ProcessIdentity | None = None,
        exit_code: int | None = None,
        detail: str | None = None,
    ) -> None:
        db.execute(
            "INSERT INTO setup_step_journal (operation_id, attempt_id, step_id, event, "
            "child_pid, child_start, exit_code, detail, recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                operation_id,
                attempt_id,
                step_id,
                event,
                child.pid if child else None,
                child.start_identity if child else None,
                exit_code,
                detail,
                self._now(),
            ),
        )

    def _step_status(self, db: sqlite3.Connection, operation_id: str, step_id: str) -> str:
        row = db.execute(
            "SELECT status FROM setup_steps WHERE operation_id = ? AND step_id = ?",
            (operation_id, step_id),
        ).fetchone()
        if row is None:
            raise ControlError(E.REQUEST_INVALID)
        return str(row[0])

    def step_started(self, operation_id: str, fencing_token: int, step_id: str) -> None:
        """Persist the step intent *before* any child is spawned (launch = intended)."""
        with self._transaction() as db:
            attempt_id = self._fenced(db, operation_id, fencing_token)
            _, status, _, flagged = self._row(db, operation_id)
            if status is not S.RUNNING or self._step_status(db, operation_id, step_id) != "pending":
                raise ControlError(E.INVALID_TRANSITION)
            db.execute(
                "UPDATE setup_steps SET status = 'running', reason_code = NULL, "
                "evidence_kind = NULL, observed_at = NULL, attempt_id = ? "
                "WHERE operation_id = ? AND step_id = ?",
                (attempt_id, operation_id, step_id),
            )
            self._record(
                db,
                operation_id,
                status,
                R.STEP_PROGRESS,
                recovery_required=R(flagged) if flagged else None,
                attempt_id=attempt_id,
            )

    def child_intent(self, operation_id: str, fencing_token: int, step_id: str) -> None:
        """Persist the intent to spawn one child of a running step, *before* spawning it.

        From this commit until ``step_launched`` records the child identity, recovery sees
        ``launch = intended`` and keeps the slot (``recovery_required``), never interrupts.
        """
        with self._transaction() as db:
            attempt_id = self._fenced(db, operation_id, fencing_token)
            if self._step_status(db, operation_id, step_id) != "running":
                raise ControlError(E.INVALID_TRANSITION)
            db.execute("UPDATE attempts SET launch = 'intended' WHERE id = ?", (attempt_id,))
            db.execute(
                "UPDATE attempt_private SET child_pid = NULL, child_start = NULL "
                "WHERE attempt_id = ?",
                (attempt_id,),
            )
            self._step_journal(db, operation_id, attempt_id, step_id, "intent")

    def reset_steps(self, operation_id: str, fencing_token: int) -> None:
        """New attempt: every step is re-checked (verified reuse or rerun), none trusted."""
        with self._transaction() as db:
            self._fenced(db, operation_id, fencing_token)
            db.execute(
                "UPDATE setup_steps SET status = 'pending', reason_code = NULL, "
                "evidence_kind = NULL, observed_at = NULL WHERE operation_id = ?",
                (operation_id,),
            )

    def step_launched(
        self,
        operation_id: str,
        fencing_token: int,
        step_id: str,
        child: ProcessIdentity | None,
    ) -> None:
        """Post-spawn evidence (PID + OS start identity), or ``None`` for a failed spawn."""
        with self._transaction() as db:
            attempt_id = self._fenced(db, operation_id, fencing_token)
            if self._step_status(db, operation_id, step_id) != "running":
                raise ControlError(E.INVALID_TRANSITION)
            if child is None:
                db.execute("UPDATE attempts SET launch = 'failed' WHERE id = ?", (attempt_id,))
                self._step_journal(db, operation_id, attempt_id, step_id, "launch_failed")
                return
            db.execute("UPDATE attempts SET launch = 'confirmed' WHERE id = ?", (attempt_id,))
            db.execute(
                "UPDATE attempt_private SET child_pid = ?, child_start = ? WHERE attempt_id = ?",
                (child.pid, child.start_identity, attempt_id),
            )
            self._step_journal(db, operation_id, attempt_id, step_id, "launched", child=child)

    def step_exited(
        self, operation_id: str, fencing_token: int, step_id: str, exit_code: int
    ) -> None:
        with self._transaction() as db:
            attempt_id = self._fenced(db, operation_id, fencing_token)
            self._step_journal(db, operation_id, attempt_id, step_id, "exited", exit_code=exit_code)

    def step_finished(
        self,
        operation_id: str,
        fencing_token: int,
        step_id: str,
        status: StepStatus,
        reason: StepReason,
        evidence: EvidenceKind | None = None,
    ) -> None:
        """Record a step result. ``succeeded``/``skipped`` require verification evidence."""
        if status not in ("succeeded", "failed", "skipped", "pending"):
            raise ControlError(E.REQUEST_INVALID)
        if (status in _SATISFIED) != (evidence is not None):
            raise ControlError(E.REQUEST_INVALID)  # no evidence, no success
        with self._transaction() as db:
            attempt_id = self._fenced(db, operation_id, fencing_token)
            _, current, _, flagged = self._row(db, operation_id)
            if self._step_status(db, operation_id, step_id) != "running":
                raise ControlError(E.INVALID_TRANSITION)
            db.execute(
                "UPDATE setup_steps SET status = ?, reason_code = ?, evidence_kind = ?, "
                "observed_at = ? WHERE operation_id = ? AND step_id = ?",
                (
                    status,
                    reason,
                    evidence,
                    self._now() if evidence else None,
                    operation_id,
                    step_id,
                ),
            )
            self._step_journal(db, operation_id, attempt_id, step_id, "result", detail=reason)
            self._record(
                db,
                operation_id,
                current,
                R.STEP_PROGRESS,
                recovery_required=R(flagged) if flagged else None,
                attempt_id=attempt_id,
            )

    def setup_steps_state(self, operation_id: str) -> dict[str, str]:
        with self._transaction(write=False) as db:
            return {
                row[0]: row[1]
                for row in db.execute(
                    "SELECT step_id, status FROM setup_steps WHERE operation_id = ?",
                    (operation_id,),
                )
            }

    def _finish_common(
        self,
        db: sqlite3.Connection,
        operation_id: str,
        target: S,
        reason: ReasonCode,
        last_fingerprint: str | None,
    ) -> None:
        attempt_id = self._release(db, operation_id, "released")
        if last_fingerprint is not None:
            db.execute(
                "UPDATE setup_state SET last_fingerprint = ? WHERE operation_id = ?",
                (last_fingerprint, operation_id),
            )
        self._record(db, operation_id, target, reason, attempt_id=attempt_id)
        self._queue_changed(db, reason)

    def setup_wait(
        self,
        operation_id: str,
        fencing_token: int,
        reason: WaitingReason,
        *,
        last_fingerprint: str | None = None,
    ) -> SetupOperation:
        """running -> waiting_user (slot released). A recorded cancel wins -> cancelled."""
        with self._transaction() as db:
            self._fenced(db, operation_id, fencing_token)
            _, status, _, _ = self._row(db, operation_id)
            db.execute(
                "UPDATE setup_steps SET status = 'pending' WHERE operation_id = ? "
                "AND status = 'running'",
                (operation_id,),
            )
            if status is S.CANCELLING:
                self._finish_common(
                    db, operation_id, S.CANCELLED, R.EXIT_RECONCILED, last_fingerprint
                )
                return self._setup_view(db, operation_id)
            if status is not S.RUNNING:
                raise ControlError(E.STORE_CORRUPT)
            db.execute(
                "UPDATE setup_state SET waiting_reason = ? WHERE operation_id = ?",
                (reason, operation_id),
            )
            code = {
                "credential_required": R.CREDENTIAL_REQUIRED,
                "agreement_required": R.AGREEMENT_REQUIRED,
            }.get(reason, R.REAPPROVAL_REQUIRED)
            self._finish_common(db, operation_id, S.WAITING_USER, code, last_fingerprint)
            return self._setup_view(db, operation_id)

    def finish_setup(
        self,
        operation_id: str,
        fencing_token: int,
        outcome: Literal["succeeded", "failed", "cancelled"],
        *,
        error: SetupErrorCode | None = None,
        error_step: str | None = None,
        last_fingerprint: str | None = None,
    ) -> SetupOperation:
        """Fenced terminal reconciliation once no child of this attempt can be running.

        ``succeeded`` is accepted only when every step is ``succeeded`` or ``skipped``
        with recorded evidence. A cancel committed earlier wins (``cancelled``).
        """
        with self._transaction() as db:
            self._fenced(db, operation_id, fencing_token)
            _, status, _, _ = self._row(db, operation_id)
            if status not in transitions.ACTIVE:
                raise ControlError(E.STORE_CORRUPT)
            steps = db.execute(
                "SELECT status, evidence_kind FROM setup_steps WHERE operation_id = ?",
                (operation_id,),
            ).fetchall()
            if outcome == "succeeded" and not all(
                row[0] in _SATISFIED and row[1] is not None for row in steps
            ):
                raise ControlError(E.REQUEST_INVALID)
            db.execute(
                "UPDATE setup_steps SET status = 'pending' WHERE operation_id = ? "
                "AND status = 'running'",
                (operation_id,),
            )
            if status is S.CANCELLING:
                target, reason = S.CANCELLED, R.EXIT_RECONCILED
            elif outcome == "succeeded":
                target, reason = S.SUCCEEDED, R.COMPLETION_VERIFIED
            else:
                target, reason = S.FAILED, R.CLASSIFIED_FAILURE
                error = error or "executor_failed"
            transitions.check("setup_operation", status, target)
            if target is S.FAILED:
                db.execute(
                    "UPDATE setup_state SET error_code = ?, error_step = ? WHERE operation_id = ?",
                    (error, error_step, operation_id),
                )
            self._finish_common(db, operation_id, target, reason, last_fingerprint)
            return self._setup_view(db, operation_id)

    def flag_setup_unconfirmed(self, operation_id: str, fencing_token: int) -> None:
        """Child exit could not be confirmed: keep the slot, mark recovery_required."""
        with self._transaction() as db:
            attempt_id = self._fenced(db, operation_id, fencing_token)
            _, status, _, flagged = self._row(db, operation_id)
            if flagged != R.TERMINATION_FAILED.value:
                self._record(
                    db,
                    operation_id,
                    status,
                    R.TERMINATION_FAILED,
                    recovery_required=R.TERMINATION_FAILED,
                    attempt_id=attempt_id,
                )

    def is_setup(self, operation_id: str) -> bool:
        with self._transaction(write=False) as db:
            return (
                db.execute(
                    "SELECT 1 FROM setup_operations WHERE operation_id = ?", (operation_id,)
                ).fetchone()
                is not None
            )

    def step_journal(self, operation_id: str) -> list[tuple[Any, ...]]:
        """Private journal read (tests / recovery inspection). Never publish."""
        with self._transaction(write=False) as db:
            return [
                tuple(row)
                for row in db.execute(
                    "SELECT step_id, event, child_pid, exit_code, attempt_id "
                    "FROM setup_step_journal WHERE operation_id = ? ORDER BY seq",
                    (operation_id,),
                )
            ]
