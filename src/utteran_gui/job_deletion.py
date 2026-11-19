"""Bundle C2c: deletion plans for finished B7 jobs ("plan -> explicit approval -> execute").

* ``history`` (③): tombstone the job's private record (``DeletionControlStore``). No file is
  touched; the output directory stays where it is.
* ``history_and_artifacts`` (④): **refused on every platform** (``artifact_deletion_disabled``).

Why ④ is disabled (owner review of PR #58, docs/architecture/program/c2c-deletion-design.md
§5): POSIX has no call that removes *the inode that was verified*. ``unlinkat``,
``renameat`` and ``rmdir`` resolve a name again, so any process of the same user that can
write the output directory can swap that name between the verification and the removal,
and an unverified file or directory is removed in its place. A private quarantine
directory does not change this (the same user can write there too), and neither do a
second ``lstat``, a lock or a random name. Windows has handle-based deletion, but it is not
implemented or accepted (#59). Since a safe deletion cannot be proven, this module contains
no file-removing code at all: nothing here renames, unlinks or removes a directory.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from utteran_contracts.jobs import JobDeletionPlan, JobDeletionPlanRequest, JobDeletionRequest
from utteran_control.deletion_repository import (
    AcceptedDeletion,
    DeletionContext,
    DeletionControlStore,
)
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.store import IdempotencyScope
from utteran_gui.job_admission import ResolutionTokens
from utteran_gui.job_artifacts import owned_directory

PLAN_TTL = timedelta(seconds=300)
_BASE_RETAINED = ("backups", "replay_public_views", "event_skeleton", "core_cache")
_RETAINED_ORDER = (
    "backups",
    "related_jobs",
    "replay_public_views",
    "event_skeleton",
    "core_cache",
    "output_files",
)
E = ErrorCode
Clock = Callable[[], datetime]


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class JobDeletionService:
    """Plans and executes history deletions. One instance per runtime; serialized executes."""

    def __init__(
        self,
        store: DeletionControlStore,
        *,
        clock: Clock | None = None,
        ttl: timedelta = PLAN_TTL,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl
        # Process-local: a restart invalidates every outstanding plan (deletion_plan_stale).
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ checks

    @staticmethod
    def _check(ctx: DeletionContext, mode: str) -> None:
        """Raise ``_Refused`` with a fixed code when this deletion may not be offered."""
        if mode != "history":
            # ④ stays refused whatever the job, the files or the platform (see module doc).
            raise _Refused("artifact_deletion_disabled")
        if not ctx.finished:
            raise _Refused("not_finished")
        if ctx.pending is not None:
            # A journal of an artifact deletion from the pre-review build: files may still
            # exist (possibly moved). Never tombstone over it; nothing completes it here.
            raise _Refused("deletion_in_progress")

    @staticmethod
    def _fingerprint(ctx: DeletionContext, mode: str) -> str:
        message = f"{mode}|{ctx.revision}|{ctx.status}|{ctx.pending is not None}"
        return hashlib.sha256(message.encode()).hexdigest()

    def _mac(self, ctx: DeletionContext, mode: str, expiry: int, binding: str) -> str:
        message = f"dp1|{ctx.job_id}|{mode}|{expiry}|{binding}|{self._fingerprint(ctx, mode)}"
        return hmac.new(self._key, message.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _retained(ctx: DeletionContext, *, files_left: bool) -> list[str]:
        codes = set(_BASE_RETAINED)
        if ctx.related:
            codes.add("related_jobs")
        if files_left:
            codes.add("output_files")
        return [code for code in _RETAINED_ORDER if code in codes]

    @staticmethod
    def _files_may_remain(ctx: DeletionContext) -> bool:
        """Read-only: whether output files of this job may exist (they are never touched)."""
        if ctx.snapshot.output_stamp is None:
            return ctx.status == "succeeded"
        return ctx.status == "succeeded" or owned_directory(ctx.snapshot, ctx.job_id) is not None

    # ------------------------------------------------------------------ plan

    def plan(self, job_id: str, body: Any, *, session: str) -> JobDeletionPlan:
        """Read-only preflight. Allowed plans carry a short-lived token bound to the facts."""
        mode = JobDeletionPlanRequest.model_validate(body).mode
        ctx = self.store.deletion_context(job_id)
        base: dict[str, Any] = {
            "schema_version": 1,
            "job_id": job_id,
            "mode": mode,
            "resume": False,
            "artifacts": [],
            "related_jobs": ctx.related,
            "retained": self._retained(ctx, files_left=self._files_may_remain(ctx)),
        }
        try:
            self._check(ctx, mode)
        except _Refused as refused:
            return JobDeletionPlan.model_validate(
                {
                    **base,
                    "allowed": False,
                    "refusal": refused.code,
                    "plan_token": None,
                    "expires_at": None,
                }
            )
        expires = self._clock() + self._ttl
        expiry = int(expires.timestamp() * 1000)
        mac = self._mac(ctx, mode, expiry, ResolutionTokens.binding(session))
        return JobDeletionPlan.model_validate(
            {
                **base,
                "allowed": True,
                "refusal": None,
                "plan_token": f"dp1.{expiry:013d}.{mac}",
                "expires_at": expires,
            }
        )

    # ------------------------------------------------------------------ execute

    def execute(
        self,
        job_id: str,
        expected_revision: int,
        scope: IdempotencyScope,
        body: Any,
        *,
        session: str,
    ) -> AcceptedDeletion:
        """Re-check, verify the token, then tombstone. Never issues a new plan by itself."""
        digest = self.store.request_digest(
            {"op": "delete", "job": job_id, "expected_revision": expected_revision, "body": body}
        )
        with self._lock:
            replay = self.store.lookup_deletion(scope, digest)
            if replay is not None:
                return replay
            request = JobDeletionRequest.model_validate(body)
            if request.mode != "history":
                raise ControlError(E.ARTIFACT_DELETION_DISABLED)  # before any read or write
            ctx = self.store.deletion_context(job_id)
            if ctx.revision != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            try:
                self._check(ctx, request.mode)
            except _Refused as refused:
                if refused.code == "deletion_in_progress":
                    raise ControlError(E.DELETION_IN_PROGRESS) from None
                raise ControlError(E.DELETION_PLAN_STALE) from None  # facts changed since
            _, expiry_text, mac = request.plan_token.split(".")
            expiry = int(expiry_text)
            expected_mac = self._mac(ctx, request.mode, expiry, ResolutionTokens.binding(session))
            if not hmac.compare_digest(mac, expected_mac):
                raise ControlError(E.DELETION_PLAN_STALE)
            if self._clock() >= datetime.fromtimestamp(expiry / 1000, UTC):
                raise ControlError(E.DELETION_PLAN_EXPIRED)
            return self.store.complete_deletion(
                job_id,
                expected_revision,
                "history",
                scope,
                digest,
                retained=self._retained(ctx, files_left=self._files_may_remain(ctx)),
            )
