"""Bundle C2c: deletion plans and the owned-artifact deletion (④) for finished B7 jobs.

Two independent operations, each "plan -> explicit approval -> execute":

* ``history``: tombstone the job's private record (``DeletionControlStore``). No file is
  touched; the output directory stays where it is.
* ``history_and_artifacts``: first delete the job's own 0.1.54+ output directory, but only
  what can be *proven* to be this job's output, then tombstone in the same way.

What "proven" means (docs/architecture/program/c2c-deletion-design.md §5): exactly one
directory among the id-length candidates carries this job's marker; it holds nothing but
the marker and ``<dir name>.<srt|vtt|json|txt|md>`` files; every file is a regular file with
one link whose size and SHA-256 equal the completion record *of its own format*. Anything
else -- an unexpected file, an edited or swapped artifact, a symlink, a second owner, a
legacy snapshot, evidence without a per-format record, a failed job -- refuses the whole
operation with a fixed code and changes nothing. There is no override.

TOCTOU: the directory is opened once (``O_DIRECTORY|O_NOFOLLOW``) and everything after
that -- reading, hashing, renaming, unlinking -- is relative to that descriptor. A file is
first renamed to a quarantine name, re-opened and re-verified (same inode, size, mtime and
digest) and only then unlinked; a mismatch puts it back under its name (never
overwriting) and stops. ``shutil.rmtree`` is never used. Platforms without ``dir_fd`` /
``O_NOFOLLOW`` support (Windows) are refused (``platform_unverified``): a path-based
deletion cannot be proven safe against reparse points there.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import stat
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from utteran_contracts.jobs import (
    JobDeletion,
    JobDeletionPlan,
    JobDeletionPlanRequest,
    JobDeletionRequest,
)
from utteran_contracts.output_naming import ID_LENGTHS, job_output_name
from utteran_control.deletion_repository import (
    AcceptedDeletion,
    DeletionContext,
    DeletionControlStore,
)
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.store import IdempotencyScope, canonical
from utteran_gui.job_admission import ResolutionTokens
from utteran_gui.job_artifacts import owned_directory
from utteran_gui.job_dispatcher import OUTPUT_MARKER

FORMATS = ("srt", "vtt", "json", "txt", "md")
QUARANTINE_PREFIX = ".utteran-deleting-"
HASH_LIMIT = 64 * 1024 * 1024
MARKER_LIMIT = 256
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
Fault = Callable[[str], None]


def _probe_platform() -> bool:
    if sys.platform == "win32" or os.name != "posix":
        return False
    needed = {os.open, os.stat, os.unlink, os.rename, os.rmdir, os.link}
    return (
        needed <= os.supports_dir_fd
        and os.listdir in os.supports_fd
        and os.stat in os.supports_follow_symlinks
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
    )


# Decided once at import from the interpreter's own capability sets.
_PLATFORM_SUPPORTED = _probe_platform()


def platform_supported() -> bool:
    """True only where every step of §5.2 can run relative to one directory descriptor."""
    return _PLATFORM_SUPPORTED


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _File:
    fmt: str
    name: str
    size: int
    sha256: str
    dev: int
    ino: int
    mtime_ns: int


@dataclass
class _Inspection:
    """Verified state of one deletion. Holds open directory descriptors until closed."""

    mode: str
    resume: bool
    parent_fd: int | None = None
    dir_fd: int | None = None
    dir_name: str | None = None
    id_length: int | None = None
    dir_id: tuple[int, int] | None = None
    marker: tuple[int, int] | None = None
    files: list[_File] = field(default_factory=list)
    expected: tuple[str, ...] = ()

    def fingerprint(self, revision: int) -> str:
        data = {
            "mode": self.mode,
            "revision": revision,
            "resume": self.resume,
            "dir": list(self.dir_id) if self.dir_id else None,
            "length": self.id_length,
            "marker": list(self.marker) if self.marker else None,
            "files": [[f.fmt, f.size, f.sha256, f.dev, f.ino, f.mtime_ns] for f in self.files],
        }
        return hashlib.sha256(canonical(data).encode()).hexdigest()

    def close(self) -> None:
        for fd in (self.dir_fd, self.parent_fd):
            if fd is not None:
                with suppress(OSError):
                    os.close(fd)
        self.dir_fd = self.parent_fd = None


def _open_flags(directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    else:
        flags |= getattr(os, "O_NONBLOCK", 0)  # never block on a FIFO swapped in
    return flags


def _hash_fd(fd: int) -> str:
    digest = hashlib.sha256()
    while block := os.read(fd, 1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _read_file(dir_fd: int, name: str) -> tuple[os.stat_result, str] | None:
    """(fstat, sha256) of a regular one-link file opened without following links."""
    try:
        fd = os.open(name, _open_flags(), dir_fd=dir_fd)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > HASH_LIMIT:
            return None
        return info, _hash_fd(fd)
    finally:
        os.close(fd)


class JobDeletionService:
    """Plans and executes deletions. One instance per runtime; executions are serialized."""

    def __init__(
        self,
        store: DeletionControlStore,
        *,
        clock: Clock | None = None,
        ttl: timedelta = PLAN_TTL,
        _fault: Fault | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ttl = ttl
        # Process-local: a restart invalidates every outstanding plan (deletion_plan_stale).
        self._key = secrets.token_bytes(32)
        self._lock = threading.Lock()
        self._fault = _fault
        self._current: str | None = None  # format being removed (diagnostic hook only)

    def _hook(self, stage: str) -> None:
        if self._fault is not None:
            self._fault(stage)

    # ------------------------------------------------------------------ inspection

    def _names(self, ctx: DeletionContext) -> list[tuple[int, str]]:
        snapshot = ctx.snapshot
        assert snapshot.output_stamp is not None
        names = []
        for length in ID_LENGTHS:
            try:
                names.append(
                    (
                        length,
                        job_output_name(
                            snapshot.input_identity.path,
                            snapshot.output_stamp,
                            ctx.job_id,
                            parent=snapshot.file.output_dir,
                            id_length=length,
                        ),
                    )
                )
            except ValueError:
                break
        return names

    @staticmethod
    def _marker(dir_fd: int, job_id: str) -> tuple[str, tuple[int, int] | None]:
        """('owned' | 'other' | 'absent' | 'mismatch', marker identity)."""
        try:
            info = os.stat(OUTPUT_MARKER, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "absent", None
        if not stat.S_ISREG(info.st_mode):
            return "other", None  # a symlink or anything else is not a marker of ours
        try:
            fd = os.open(OUTPUT_MARKER, _open_flags(), dir_fd=dir_fd)
        except OSError:
            return "other", None
        try:
            opened = os.fstat(fd)
            content = os.read(fd, MARKER_LIMIT + 1)
        finally:
            os.close(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise _Refused("filesystem_error")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return "other", None
        if text.strip() != job_id:
            return "other", None
        if text != job_id + "\n" or opened.st_nlink != 1 or not stat.S_ISREG(opened.st_mode):
            return "mismatch", None
        return "owned", (opened.st_dev, opened.st_ino)

    def _inspect_artifacts(self, ctx: DeletionContext, ins: _Inspection) -> None:
        snapshot = ctx.snapshot
        if ctx.status != "succeeded":
            raise _Refused("not_succeeded")
        if snapshot.output_stamp is None:
            raise _Refused("legacy_output")
        evidence = ctx.evidence or {}
        if not evidence or not set(evidence) <= set(FORMATS):
            raise _Refused("evidence_unverified")
        if not platform_supported():
            raise _Refused("platform_unverified")
        pending = ctx.pending
        journal: dict[str, tuple[str, int]] = {}
        if pending is not None:
            try:
                for item in pending["files"]:
                    journal[str(item["format"])] = (str(item["sha256"]), int(item["size"]))
                journal_length = int(pending["id_length"])
            except (KeyError, TypeError, ValueError):
                raise ControlError(E.STORE_CORRUPT) from None
        expected = journal if pending is not None else dict(evidence)
        ins.expected = tuple(fmt for fmt in FORMATS if fmt in expected)
        parent = snapshot.file.output_dir
        try:
            ins.parent_fd = os.open(parent, _open_flags(directory=True))
        except FileNotFoundError:
            if pending is not None:
                return  # everything already gone; only the tombstone remains
            raise _Refused("directory_missing") from None
        except OSError:
            raise _Refused("unsafe_entry") from None
        owned: list[tuple[int, str, int, tuple[int, int], tuple[int, int] | None]] = []
        unmarked: dict[int, tuple[str, int, tuple[int, int]]] = {}
        for length, name in self._names(ctx):
            try:
                info = os.stat(name, dir_fd=ins.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError:
                raise _Refused("filesystem_error") from None
            if not stat.S_ISDIR(info.st_mode):
                continue  # a symlink or a file with that name is not this job's directory
            try:
                fd = os.open(name, _open_flags(directory=True), dir_fd=ins.parent_fd)
            except OSError:
                continue
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                os.close(fd)
                raise _Refused("filesystem_error")
            state, marker = self._marker(fd, ctx.job_id)
            if state == "mismatch":
                os.close(fd)
                raise _Refused("marker_mismatch")
            if state == "owned":
                owned.append((length, name, fd, (opened.st_dev, opened.st_ino), marker))
            elif state == "absent" and pending is not None and length == journal_length:
                unmarked[length] = (name, fd, (opened.st_dev, opened.st_ino))
            else:
                os.close(fd)
        if len(owned) > 1:
            for item in owned:
                os.close(item[2])
            raise _Refused("directory_ambiguous")
        if owned:
            length, name, fd, dir_id, marker = owned[0]
            for _, (_, other_fd, _) in unmarked.items():
                os.close(other_fd)
            if pending is not None and length != journal_length:
                os.close(fd)
                raise _Refused("directory_ambiguous")
        elif pending is not None and journal_length in unmarked:
            name, fd, dir_id = unmarked[journal_length]
            length, marker = journal_length, None
        elif pending is not None:
            return  # the directory is gone: nothing left on disk for this job
        else:
            raise _Refused("directory_missing")
        ins.dir_fd, ins.dir_name, ins.id_length = fd, name, length
        ins.dir_id, ins.marker = dir_id, marker
        self._inspect_entries(ins, expected, resume=pending is not None)

    def _inspect_entries(
        self, ins: _Inspection, expected: dict[str, tuple[str, int]], *, resume: bool
    ) -> None:
        assert ins.dir_fd is not None and ins.dir_name is not None
        try:
            entries = sorted(os.listdir(ins.dir_fd))
        except OSError:
            raise _Refused("filesystem_error") from None
        by_name = {f"{ins.dir_name}.{fmt}": fmt for fmt in FORMATS}
        present: dict[str, str] = {}
        for entry in entries:
            if entry == OUTPUT_MARKER:
                if ins.marker is None:
                    raise _Refused("unexpected_entries")  # appeared during a resume
                continue
            fmt = by_name.get(entry)
            if fmt is None or fmt not in expected:
                raise _Refused("unexpected_entries")
            present[fmt] = entry
        for fmt in FORMATS:
            if fmt not in present:
                continue
            name = present[fmt]
            try:
                info = os.stat(name, dir_fd=ins.dir_fd, follow_symlinks=False)
            except OSError:
                raise _Refused("filesystem_error") from None
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _Refused("unsafe_entry")
            sha, size = expected[fmt]
            if info.st_size != size:
                raise _Refused("artifact_changed")
            if info.st_size > HASH_LIMIT:
                raise _Refused("unsafe_entry")
            read = _read_file(ins.dir_fd, name)
            if read is None:
                raise _Refused("unsafe_entry")
            opened, found = read
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise _Refused("filesystem_error")
            if not hmac.compare_digest(found, sha):
                raise _Refused("artifact_changed")  # never "matches another format"
            ins.files.append(
                _File(
                    fmt,
                    name,
                    opened.st_size,
                    found,
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_mtime_ns,
                )
            )
        if not resume and set(present) != set(expected):
            raise _Refused("artifact_missing")

    @contextmanager
    def _inspect(self, ctx: DeletionContext, mode: str) -> Iterator[_Inspection]:
        ins = _Inspection(mode=mode, resume=ctx.pending is not None)
        try:
            if not ctx.finished:
                raise _Refused("not_finished")
            if mode == "history":
                if ctx.pending is not None:
                    raise _Refused("deletion_in_progress")
            else:
                self._inspect_artifacts(ctx, ins)
            yield ins
        finally:
            ins.close()

    # ------------------------------------------------------------------ plan token

    def _mac(self, ctx: DeletionContext, mode: str, expiry: int, binding: str, fp: str) -> str:
        message = f"dp1|{ctx.job_id}|{mode}|{expiry}|{binding}|{fp}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def _retained(self, ctx: DeletionContext, mode: str, *, files_left: bool) -> list[str]:
        codes = set(_BASE_RETAINED)
        if ctx.related:
            codes.add("related_jobs")
        if files_left:
            codes.add("output_files")
        return [code for code in _RETAINED_ORDER if code in codes]

    @staticmethod
    def _files_may_remain(ctx: DeletionContext) -> bool:
        if ctx.snapshot.output_stamp is None:
            return ctx.status == "succeeded"
        return owned_directory(ctx.snapshot, ctx.job_id) is not None

    def plan(self, job_id: str, body: Any, *, session: str) -> JobDeletionPlan:
        """Read-only preflight. Allowed plans carry a short-lived token bound to the facts."""
        mode = JobDeletionPlanRequest.model_validate(body).mode
        ctx = self.store.deletion_context(job_id)
        base: dict[str, Any] = {
            "schema_version": 1,
            "job_id": job_id,
            "mode": mode,
            "resume": ctx.pending is not None,
            "related_jobs": ctx.related,
        }
        files_left = mode == "history" and self._files_may_remain(ctx)
        try:
            with self._inspect(ctx, mode) as ins:
                now = self._clock()
                expires = now + self._ttl
                expiry = int(expires.timestamp() * 1000)
                fp = ins.fingerprint(ctx.revision)
                mac = self._mac(ctx, mode, expiry, ResolutionTokens.binding(session), fp)
                return JobDeletionPlan.model_validate(
                    {
                        **base,
                        "allowed": True,
                        "refusal": None,
                        "plan_token": f"dp1.{expiry:013d}.{mac}",
                        "expires_at": expires,
                        "artifacts": [{"format": f.fmt, "size": f.size} for f in ins.files],
                        "retained": self._retained(ctx, mode, files_left=files_left),
                    }
                )
        except _Refused as refused:
            return JobDeletionPlan.model_validate(
                {
                    **base,
                    "allowed": False,
                    "refusal": refused.code,
                    "plan_token": None,
                    "expires_at": None,
                    "artifacts": [],
                    "retained": self._retained(ctx, mode, files_left=files_left),
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
        """Re-verify, check the token, then delete. Never issues a new plan by itself."""
        digest = self.store.request_digest(
            {"op": "delete", "job": job_id, "expected_revision": expected_revision, "body": body}
        )
        with self._lock:
            replay = self.store.lookup_deletion(scope, digest)
            if replay is not None:
                return replay
            request = JobDeletionRequest.model_validate(body)
            ctx = self.store.deletion_context(job_id)
            if ctx.revision != expected_revision:
                raise ControlError(E.REVISION_CONFLICT)
            parts = request.plan_token.split(".")
            expiry = int(parts[1])
            try:
                with self._inspect(ctx, request.mode) as ins:
                    fp = ins.fingerprint(ctx.revision)
                    binding = ResolutionTokens.binding(session)
                    expected_mac = self._mac(ctx, request.mode, expiry, binding, fp)
                    if not hmac.compare_digest(parts[2], expected_mac):
                        raise ControlError(E.DELETION_PLAN_STALE)
                    if self._clock() >= datetime.fromtimestamp(expiry / 1000, UTC):
                        raise ControlError(E.DELETION_PLAN_EXPIRED)
                    if request.mode == "history":
                        return self.store.complete_deletion(
                            job_id,
                            expected_revision,
                            "history",
                            scope,
                            digest,
                            retained=self._retained(
                                ctx, "history", files_left=self._files_may_remain(ctx)
                            ),
                        )
                    return self._delete_artifacts(ctx, ins, scope, digest)
            except _Refused as refused:
                if refused.code == "deletion_in_progress":
                    raise ControlError(E.DELETION_IN_PROGRESS) from None
                raise ControlError(E.DELETION_PLAN_STALE) from None  # facts changed since

    def _delete_artifacts(
        self,
        ctx: DeletionContext,
        ins: _Inspection,
        scope: IdempotencyScope,
        digest: str,
    ) -> AcceptedDeletion:
        if not ins.resume:
            targets = {
                "id_length": ins.id_length,
                "files": [{"format": f.fmt, "size": f.size, "sha256": f.sha256} for f in ins.files],
            }
            self.store.begin_artifact_deletion(ctx.job_id, ctx.revision, targets)
        self._hook("verified")
        removed, failed = 0, False
        for item in ins.files:
            self._current = item.fmt
            self._hook(f"before_remove:{item.fmt}")
            assert ins.dir_fd is not None
            outcome = self._remove(ins.dir_fd, item.name, partial(self._same_file, item=item))
            self._current = None
            if outcome == "removed":
                removed += 1
            elif outcome == "failed":
                failed = True
                break
        files_left = False
        if not failed and ins.dir_fd is not None and ins.marker is not None:
            marker = ins.marker
            if (
                self._remove(
                    ins.dir_fd, OUTPUT_MARKER, lambda fd: self._same_marker(fd, ctx, marker)
                )
                == "failed"
            ):
                failed = True
        if not failed and ins.dir_fd is not None:
            files_left = not self._remove_directory(ins)
        self._hook("after_files")
        if failed:
            remaining = self._remaining(ins)
            deletion = JobDeletion.model_validate(
                {
                    "schema_version": 1,
                    "job_id": ctx.job_id,
                    "mode": "history_and_artifacts",
                    "outcome": "incomplete",
                    "deleted_at": None,
                    "artifacts_removed": removed,
                    "artifacts_remaining": remaining,
                    "retained": self._retained(ctx, "history_and_artifacts", files_left=True),
                }
            )
            # No replay: the same request is not "done"; a new plan resumes from the journal.
            return AcceptedDeletion(200, {"Cache-Control": "no-store"}, deletion, False)
        return self.store.complete_deletion(
            ctx.job_id,
            ctx.revision,
            "history_and_artifacts",
            scope,
            digest,
            removed=removed,
            retained=self._retained(ctx, "history_and_artifacts", files_left=files_left),
        )

    @staticmethod
    def _same_file(fd: int, *, item: _File) -> bool:
        info = os.fstat(fd)
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and (info.st_dev, info.st_ino) == (item.dev, item.ino)
            and info.st_size == item.size
            and info.st_mtime_ns == item.mtime_ns
            and hmac.compare_digest(_hash_fd(fd), item.sha256)
        )

    @staticmethod
    def _same_marker(fd: int, ctx: DeletionContext, marker: tuple[int, int]) -> bool:
        info = os.fstat(fd)
        return (
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and (info.st_dev, info.st_ino) == marker
            and os.read(fd, MARKER_LIMIT + 1) == (ctx.job_id + "\n").encode()
        )

    @staticmethod
    def _remove(dir_fd: int, name: str, same: Callable[[int], bool]) -> str:
        """'removed' | 'gone' | 'failed'. Never unlinks bytes it has not re-verified."""
        quarantine = QUARANTINE_PREFIX + secrets.token_hex(8)
        try:
            os.rename(name, quarantine, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except FileNotFoundError:
            return "gone"
        except OSError:
            return "failed"
        verified = False
        try:
            fd = os.open(quarantine, _open_flags(), dir_fd=dir_fd)
        except OSError:
            fd = None
        if fd is not None:
            try:
                verified = same(fd)
            except OSError:
                verified = False
            finally:
                os.close(fd)
        if verified:
            try:
                os.unlink(quarantine, dir_fd=dir_fd)
                return "removed"
            except OSError:
                pass
        # Put it back under its own name without overwriting anything, then stop.
        with suppress(OSError):
            os.link(quarantine, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
            os.unlink(quarantine, dir_fd=dir_fd)
        return "failed"

    @staticmethod
    def _remove_directory(ins: _Inspection) -> bool:
        """rmdir only an empty directory that is still the one verified. True when removed."""
        assert ins.dir_fd is not None and ins.parent_fd is not None and ins.dir_name
        try:
            if os.listdir(ins.dir_fd):
                return False  # someone put something there meanwhile: leave it
            info = os.stat(ins.dir_name, dir_fd=ins.parent_fd, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != ins.dir_id or not stat.S_ISDIR(info.st_mode):
                return False
            os.rmdir(ins.dir_name, dir_fd=ins.parent_fd)
            return True
        except OSError:
            return False

    @staticmethod
    def _remaining(ins: _Inspection) -> int:
        if ins.dir_fd is None or ins.dir_name is None:
            return 0
        try:
            entries = set(os.listdir(ins.dir_fd))
        except OSError:
            return len(ins.expected)
        return sum(1 for fmt in ins.expected if f"{ins.dir_name}.{fmt}" in entries)
