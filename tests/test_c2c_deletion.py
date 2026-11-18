"""Bundle C2c: tombstoning a history record (③) and the owned-artifact deletion (④).

The point of these tests is as much "what is never deleted" as "what is deleted":
the original input, other jobs' outputs, files the user placed, the skeleton the B9 sync
and the retry chain rely on, and every byte that cannot be proven to be this job's own
output. Synthetic data under ``tmp_path`` only. Residues are measured, not assumed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_b7_api import AUTHORITY, ROOT
from test_b7_jobs import SENTINEL, SESSION, Env, FakeRunner, dispatcher, scope
from test_history_visibility import TABLES, _setup, dump, version
from utteran_contracts.jobs import JobDeletion, JobDeletionPlan
from utteran_contracts.output_naming import job_output_name
from utteran_contracts.worker import WorkerArtifact, WorkerCompleted
from utteran_control.deletion_repository import DeletionControlStore
from utteran_control.errors import ControlError
from utteran_control.errors import ErrorCode as E
from utteran_control.events_repository import DiagnosticsService
from utteran_control.history_repository import HistoryControlStore
from utteran_control.job_repository import JobControlStore
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.job_deletion import JobDeletionService
from utteran_gui.job_dispatcher import OUTPUT_MARKER
from utteran_gui.job_runtime import JobRuntime
from utteran_gui.operation_queue import OperationQueue

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="④ is refused on Windows (platform_unverified); see below"
)
FORMATS = ("srt", "vtt", "json", "txt", "md")
OTHER = "job_" + "e" * 32


class WritingRunner(FakeRunner):
    """Writes real artifact bytes into the job's own directory and reports their digests."""

    formats: tuple[str, ...] = FORMATS

    def execute(self, request: Any, **kwargs: Any) -> WorkerCompleted:
        super().execute(request, **kwargs)  # the real child for the launch journal
        general = request.context.base_config["general"]
        directory = Path(str(general["output_dir"]))
        stem = request.context.output_stem
        assert stem is not None and directory.name == stem
        artifacts = []
        for fmt in self.formats:
            content = f"synthetic {fmt} for {request.job_id}\n".encode()
            target = directory / f"{stem}.{fmt}"
            target.write_bytes(content)
            artifacts.append(
                WorkerArtifact(
                    path=str(target), size=len(content), sha256=hashlib.sha256(content).hexdigest()
                )
            )
        manifest = WorkerArtifact(path=str(self.env.tmp / "manifest.json"), size=1, sha256="b" * 64)
        return WorkerCompleted(
            sequence=5,
            request_id=request.request_id,
            job_id=request.job_id,
            attempt_id=request.attempt_id,
            core_job_id="0123456789abcdef",
            artifacts=tuple(artifacts),
            manifest=manifest,
        )


class V8(Env):
    def __init__(self, tmp: Path, **kwargs: Any) -> None:
        super().__init__(tmp, None)
        self.store = DeletionControlStore(self.store.path, **kwargs)
        self.admission.store = self.store
        self.now = datetime(2026, 11, 17, tzinfo=UTC)
        self.service = JobDeletionService(self.store, clock=lambda: self.now)

    def finished(self, name: str = "a.wav", runner: FakeRunner | None = None) -> str:
        job = self.submit(self.file(name)).batch.jobs[0]
        assert dispatcher(self, runner or WritingRunner(self)).run_once()
        assert self.store.get_job(job.id).status == "succeeded"
        return job.id

    def failed(self, name: str = "f.wav") -> str:
        job = self.submit(self.file(name)).batch.jobs[0]

        class Failing(FakeRunner):
            def execute(self, request: Any, **kwargs: Any) -> Any:
                raise RuntimeError("synthetic failure")

        assert dispatcher(self, Failing(self)).run_once()
        assert self.store.get_job(job.id).status == "failed"
        return job.id

    def directory(self, job_id: str) -> Path:
        snapshot = self.store.get_snapshot(job_id)
        owned = [
            p
            for p in self.output.iterdir()
            if (p / OUTPUT_MARKER).is_file()
            and (p / OUTPUT_MARKER).read_text(encoding="utf-8").strip() == job_id
        ]
        assert len(owned) == 1, owned
        assert snapshot.output_stamp is not None
        return owned[0]

    def plan(self, job_id: str, mode: str = "history_and_artifacts", who: str = SESSION):
        return self.service.plan(job_id, {"schema_version": 1, "mode": mode}, session=who)

    def delete(
        self,
        job_id: str,
        mode: str = "history_and_artifacts",
        *,
        plan: JobDeletionPlan | None = None,
        key: str | None = None,
        revision: int | None = None,
        who: str = SESSION,
    ):
        plan = plan or self.plan(job_id, mode, who)
        assert plan.allowed, plan.refusal
        body = {"schema_version": 1, "mode": mode, "plan_token": plan.plan_token}
        expected = self.store.get_job(job_id).revision if revision is None else revision
        endpoint = f"/api/v1/jobs/{job_id}/deletions"
        return self.service.execute(
            job_id, expected, scope(endpoint, key or self.key("del")), body, session=who
        )


@pytest.fixture
def env(tmp_path: Path) -> V8:
    return V8(tmp_path)


def code(excinfo: pytest.ExceptionInfo[BaseException]) -> str:
    value = excinfo.value
    return value.code.value if isinstance(value, ControlError) else str(getattr(value, "code", ""))


def tree(root: Path) -> dict[str, tuple[bytes | None, int]]:
    """Every entry below ``root`` (control DB excluded): bytes (None for dirs) and inode."""
    out: dict[str, tuple[bytes | None, int]] = {}
    for path in sorted(root.rglob("*")):
        if "control" in path.relative_to(root).parts:
            continue
        info = path.lstat()
        data = (
            None
            if path.is_dir() and not path.is_symlink()
            else (os.readlink(path).encode() if path.is_symlink() else path.read_bytes())
        )
        out[str(path.relative_to(root))] = (data, info.st_ino)
    return out


def raw(path: Path) -> bytes:
    return path.read_bytes()


# ============================================================ 1. migration / recovery


def test_v7_migrates_to_v8_additively_and_older_classes_refuse_it(tmp_path: Path) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    HistoryControlStore(path)
    assert version(path) == 7
    backup = path.with_name("pre-v8.sqlite3")
    assert HistoryControlStore.backup_before_open(path, backup) == 7
    before = dump(path)
    DeletionControlStore(path)
    assert version(path) == 8
    assert dump(path) == before  # every existing row identical
    with sqlite3.connect(path) as db:
        for table in ("job_deletions", "job_deletion_gate", "deletion_replays"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"  # no WAL
    for old in (JobControlStore, HistoryControlStore):
        with pytest.raises(ControlError) as error:
            old(path)
        assert error.value.code is E.STORE_SCHEMA_UNSUPPORTED
    assert version(path) == 8
    assert version(backup) == 7
    HistoryControlStore(backup)  # the pre-migration backup opens with the 0.1.56 class


def test_failed_v8_migration_rolls_back_to_a_complete_v7_store(tmp_path: Path) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    HistoryControlStore(path)
    before = dump(path)

    def crash(where: str) -> None:
        if where == "before_commit":
            raise ControlError(E.STORE_FULL)

    with pytest.raises(ControlError):
        DeletionControlStore(path, _fault=crash)
    assert version(path) == 7 and dump(path) == before
    HistoryControlStore(path)


def test_runtime_activation_backs_up_v7_before_opening_v8(tmp_path: Path) -> None:
    env = Env(tmp_path, None)
    path = env.store.path
    del env.store
    HistoryControlStore(path)
    runtime = JobRuntime(
        store_path=path,
        settings_path=env.settings_path,
        legacy_queue=OperationQueue(),
        provider=env.provider,
        runner=FakeRunner(env),
        job_dir=tmp_path / "jobs",
        autostart_dispatcher=False,
        setup=_setup(tmp_path),
    )
    runtime.activate()
    try:
        assert isinstance(runtime.store, DeletionControlStore) and version(path) == 8
        assert runtime.deletion is not None
        assert runtime.last_backup is not None and version(runtime.last_backup) == 7
        restored = tmp_path / "restore-drill.sqlite3"
        shutil.copyfile(runtime.last_backup, restored)
        HistoryControlStore(restored)
        assert version(restored) == 7
    finally:
        runtime.shutdown()


def test_a_committed_gate_row_fails_closed_on_open(env: V8) -> None:
    job_id = env.finished()
    path = env.store.path
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO job_deletions VALUES (?, 'history', 'completed', NULL, 'x', 'y')",
            (job_id,),
        )
        db.execute("INSERT INTO job_deletion_gate VALUES (?)", (job_id,))
    with pytest.raises(ControlError) as error:
        DeletionControlStore(path)
    assert error.value.code is E.STORE_CORRUPT


# ============================================================ 2. immutable triggers


def test_snapshot_and_evidence_stay_immutable_outside_the_deletion_gate(env: V8) -> None:
    job_id = env.finished()
    waiting = env.submit(env.file("w.wav")).batch.jobs[0].id
    tomb = DeletionControlStore.TOMBSTONE_SNAPSHOT
    digest = hashlib.sha256(tomb.encode()).hexdigest()
    with sqlite3.connect(env.store.path) as db:
        attempts = [
            (
                "UPDATE job_snapshots SET body = ?, digest = ? WHERE operation_id = ?",
                (tomb, digest, job_id),
            ),
            ("UPDATE job_snapshots SET body = 'x' WHERE operation_id = ?", (job_id,)),
            ("UPDATE job_results SET evidence = '{}' WHERE operation_id = ?", (job_id,)),
            ("DELETE FROM job_snapshots WHERE operation_id = ?", (job_id,)),
            ("DELETE FROM job_results WHERE operation_id = ?", (job_id,)),
        ]
        for sql, params in attempts:
            with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                db.execute(sql, params)
        # With the gate open, only the fixed tombstone values pass; nothing else does.
        db.execute(
            "INSERT INTO job_deletions VALUES (?, 'history', 'completed', NULL, 'x', 'y')",
            (job_id,),
        )
        db.execute("INSERT INTO job_deletion_gate VALUES (?)", (job_id,))
        for sql, params in (
            (
                "UPDATE job_snapshots SET body = 'x', digest = ? WHERE operation_id = ?",
                (digest, job_id),
            ),
            (
                "UPDATE job_snapshots SET body = ?, digest = ?, retry_of = ? "
                "WHERE operation_id = ?",
                (tomb, digest, waiting, job_id),
            ),
            ("UPDATE job_results SET evidence = 'x' WHERE operation_id = ?", (job_id,)),
            ("DELETE FROM job_snapshots WHERE operation_id = ?", (job_id,)),
        ):
            with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                db.execute(sql, params)
        # A gate can never be opened for a job that is not finished.
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            db.execute(
                "INSERT INTO job_deletions VALUES (?, 'history', 'completed', NULL, 'x', 'y')",
                (waiting,),
            )
            db.execute("INSERT INTO job_deletion_gate VALUES (?)", (waiting,))
        db.rollback()


# ============================================================ 3. tombstone (③)


def test_history_deletion_scrubs_private_values_and_keeps_the_skeleton(env: V8) -> None:
    job_id = env.finished("secret-name.wav")
    directory = env.directory(job_id)
    files_before = tree(env.tmp)
    before = dump(env.store.path)
    sync_before = env.store.snapshot()
    assert SENTINEL.encode() in raw(env.store.path)
    assert b"secret-name" in raw(env.store.path)

    accepted = env.delete(job_id, "history")
    assert accepted.status == 200 and not accepted.replayed
    result = accepted.deletion
    assert isinstance(result, JobDeletion)
    assert result.outcome == "completed" and result.mode == "history"
    assert result.artifacts_removed == 0 and "output_files" in result.retained

    after = dump(env.store.path)
    # Skeleton: operations, history, outbox, attempts, queue, counters, replays unchanged.
    for table in (
        "operations",
        "operation_history",
        "outbox",
        "attempts",
        "queue",
        "queue_items",
        "counters",
        "replays",
        "queue_replays",
        "batch_replays",
        "job_replays",
        "job_state",
    ):
        assert after[table] == before[table], table
    snap = next(r for r in after["job_snapshots"] if r[0] == job_id)
    old = next(r for r in before["job_snapshots"] if r[0] == job_id)
    assert snap[1] == old[1] and snap[4:] == old[4:]  # batch, retry_of, rerun_of, created_at
    assert snap[2] == DeletionControlStore.TOMBSTONE_SNAPSHOT
    assert not [
        r
        for r in after["attempt_private"]
        if r[0] in {a[0] for a in after["attempts"] if a[1] == job_id}
    ]
    # Measured residue: the private values are gone from the DB file itself.
    data = raw(env.store.path)
    assert SENTINEL.encode() not in data
    assert b"secret-name" not in data and str(env.inputs).encode() not in data
    assert not env.store.path.with_name(env.store.path.name + "-journal").exists()
    assert not env.store.path.with_name(env.store.path.name + "-wal").exists()
    # ③ never touches files: the output directory, marker and the input are byte-identical.
    assert tree(env.tmp) == files_before and directory.is_dir()
    # B9 sync keeps the skeleton and no event was written.
    sync_after = env.store.snapshot()
    assert sync_after.event_cursor == sync_before.event_cursor
    assert job_id in {op.id for op in sync_after.operations}
    # Diagnostics keep the value-free skeleton (codes, times, attempt ids).
    entries = DiagnosticsService(env.store).page(job_id, "job").items
    assert entries and entries[-1].code == "completion_verified"


def test_deleted_job_reads_are_410_and_lists_exclude_it(env: V8) -> None:
    job_id = env.finished()
    other = env.finished("b.wav")
    env.store.set_visibility(other, 0, True)
    env.delete(job_id, "history")
    for read in (
        lambda: env.store.get_job(job_id),
        lambda: env.store.get_snapshot(job_id),
        lambda: env.store.visibility(job_id),
        lambda: env.store.set_visibility(job_id, 0, True),
        lambda: env.store.artifact_evidence(job_id),
        lambda: env.admission.retry(job_id, 3, scope(f"/api/v1/jobs/{job_id}/retry", env.key())),
        lambda: env.store.cancel_job(job_id, 3, scope(f"/api/v1/jobs/{job_id}/cancel", env.key())),
        lambda: env.plan(job_id, "history"),
    ):
        with pytest.raises(ControlError) as error:
            read()
        assert error.value.code is E.JOB_DELETED and error.value.status == 410
    with pytest.raises(ControlError) as error:
        env.admission.rerun(
            job_id,
            3,
            scope(f"/api/v1/jobs/{job_id}/rerun", env.key()),
            {"schema_version": 1, "submission": env.submit_body(env.session(env.file("r.wav")))},
            session=SESSION,
        )
    assert error.value.code is E.JOB_DELETED
    for selected in ("visible", "hidden", "all"):
        assert job_id not in [j.id for j in env.store.list_jobs(visibility=selected).items]
    assert [j.id for j in env.store.list_jobs(visibility="hidden").items] == [other]


def test_only_finished_jobs_can_be_deleted_and_refusals_change_nothing(env: V8) -> None:
    waiting = env.submit(env.file("w.wav")).batch.jobs[0].id
    before = dump(env.store.path)
    plan = env.plan(waiting, "history")
    assert not plan.allowed and plan.refusal == "not_finished" and plan.plan_token is None
    with pytest.raises(ControlError) as error:
        env.store.complete_deletion(
            waiting,
            env.store.get_job(waiting).revision,
            "history",
            scope(f"/api/v1/jobs/{waiting}/deletions", env.key()),
            "0" * 64,
        )
    assert error.value.code is E.INVALID_TRANSITION
    assert dump(env.store.path) == before
    with pytest.raises(ControlError) as error:
        env.plan("job_" + "0" * 32, "history")
    assert error.value.code is E.RESOURCE_NOT_FOUND


def test_cas_replay_and_conflicts(env: V8) -> None:
    job_id = env.finished()
    plan = env.plan(job_id, "history")
    revision = env.store.get_job(job_id).revision
    with pytest.raises(ControlError) as error:
        env.delete(job_id, "history", plan=plan, revision=revision - 1)
    assert error.value.code is E.REVISION_CONFLICT
    assert env.store.get_job(job_id).status == "succeeded"
    key = env.key("del")
    first = env.delete(job_id, "history", plan=plan, key=key)
    # Lost response: the same request (same key, revision and body) gets the same answer.
    again = env.delete(job_id, "history", plan=plan, key=key, revision=revision)
    assert again.replayed and again.status == first.status
    assert again.deletion.to_wire() == first.deletion.to_wire()
    with pytest.raises(ControlError) as error:  # same key, other body
        env.service.execute(
            job_id,
            revision,
            scope(f"/api/v1/jobs/{job_id}/deletions", key),
            {"schema_version": 1, "mode": "history_and_artifacts", "plan_token": plan.plan_token},
            session=SESSION,
        )
    assert error.value.code is E.IDEMPOTENCY_CONFLICT
    with pytest.raises(ControlError) as error:  # a new key after deletion
        env.delete(job_id, "history", plan=plan, revision=revision)
    assert error.value.code is E.JOB_DELETED


def test_replays_that_name_a_deleted_job_are_not_served(env: V8) -> None:
    submit_key = env.key()
    path = env.file("a.wav")
    body = env.submit_body(env.session(path))
    accepted = env.admission.submit(scope("/api/v1/jobs", submit_key), body, session=SESSION)
    job_id = accepted.batch.jobs[0].id
    cancel = scope(f"/api/v1/jobs/{job_id}/cancel", env.key())
    env.store.cancel_job(job_id, 1, cancel)
    rows = len(dump(env.store.path)["job_replays"])
    env.delete(job_id, "history")
    with pytest.raises(ControlError) as error:
        env.store.cancel_job(job_id, 1, cancel)  # same key, same body
    assert error.value.code is E.JOB_DELETED
    with pytest.raises(ControlError) as error:
        env.admission.submit(scope("/api/v1/jobs", submit_key), body, session=SESSION)
    assert error.value.code is E.JOB_DELETED
    assert len(dump(env.store.path)["job_replays"]) == rows  # rows kept, not rewritten


def test_retry_chain_members_keep_their_own_copy_and_keep_working(env: V8) -> None:
    source = env.failed()
    retry = env.admission.retry(
        source,
        env.store.get_job(source).revision,
        scope(f"/api/v1/jobs/{source}/retry", env.key()),
    ).job.id
    plan = env.plan(source, "history")
    assert plan.related_jobs == 1 and "related_jobs" in plan.retained
    env.delete(source, "history", plan=plan)
    child = env.store.get_job(retry)
    assert child.retry_of == source  # the chain still points at the tombstone
    assert env.store.get_snapshot(retry).resolved.settings.asr.initial_prompt == SENTINEL
    assert SENTINEL.encode() in raw(env.store.path)  # residue: the child's own copy
    assert dispatcher(env, WritingRunner(env)).run_once()
    assert env.store.get_job(retry).status == "succeeded"
    assert env.plan(retry, "history").allowed


def test_failed_cancelled_and_hidden_jobs_can_be_tombstoned(env: V8) -> None:
    failed = env.failed()
    cancelled = env.submit(env.file("c.wav")).batch.jobs[0].id
    env.store.cancel_job(cancelled, 1, scope(f"/api/v1/jobs/{cancelled}/cancel", env.key()))
    hidden = env.finished("h.wav")
    env.store.set_visibility(hidden, 0, True)
    for job_id in (failed, cancelled, hidden):
        assert env.delete(job_id, "history").deletion.outcome == "completed"


def test_list_cursors_issued_before_a_deletion_expire(tmp_path: Path) -> None:
    env = V8(tmp_path)
    ids = [env.finished(f"{i}.wav") for i in range(3)]
    first = env.store.list_jobs(limit=1)
    assert first.next_cursor is not None
    env.delete(ids[1], "history")
    with pytest.raises(ControlError) as error:
        env.store.list_jobs(first.next_cursor, limit=1)
    assert error.value.code is E.LIST_CURSOR_EXPIRED


def test_measured_residue_db_journal_replays_and_backup(tmp_path: Path) -> None:
    seen: dict[str, bool] = {}
    holder: list[V8] = []

    def fault(where: str) -> None:
        if where == "before_commit" and holder and holder[0].store._deleting:
            journal = holder[0].store.path.with_name("operations.sqlite3-journal")
            seen["journal_during_tx"] = (
                journal.exists() and SENTINEL.encode() in journal.read_bytes()
            )

    env = V8(tmp_path, _fault=fault)
    holder.append(env)
    job_id = env.finished("residue.wav")
    backup = env.store.path.with_name("operations-pre-v1-test.sqlite3")
    env.store.backup(backup)
    env.delete(job_id, "history")
    assert seen == {"journal_during_tx": True}  # the rollback journal held it until commit
    assert SENTINEL.encode() not in raw(env.store.path)
    assert SENTINEL.encode() in raw(backup)  # backups are never rewritten
    # Public projections in the replay tables survive (immutable); no private value in them.
    with sqlite3.connect(env.store.path) as db:
        bodies = " ".join(r[0] for r in db.execute("SELECT body FROM batch_replays"))
    assert job_id in bodies and SENTINEL not in bodies and str(env.inputs) not in bodies


def test_secure_delete_clears_one_job_among_many_from_the_db_file(tmp_path: Path) -> None:
    """Page splits of 40 jobs, then one deletion: its values leave the file, others stay.

    Control measurement (design doc §6): the same run with ``secure_delete`` forced off
    left 2 copies of the deleted job's input name in the file.
    """
    env = V8(tmp_path)
    ids = [env.finished(f"bulk{i:03d}.wav") for i in range(40)]
    assert raw(env.store.path).count(b"bulk017") == 2  # input_path, file.input_path
    env.delete(ids[17], "history")
    data = raw(env.store.path)
    assert data.count(b"bulk017") == 0
    assert data.count(b"bulk018") == 2  # the other jobs are untouched
    assert [j.id for j in env.store.list_jobs().items] == ids[:17] + ids[18:]


# ============================================================ 4. artifacts (④)


@POSIX_ONLY
def test_artifact_deletion_removes_only_verified_files_marker_and_directory(env: V8) -> None:
    job_id = env.finished("talk.wav")
    other = env.finished("other.wav")
    directory = env.directory(job_id)
    keep_other = tree(env.directory(other))
    (env.output / "user-notes.txt").write_text("mine", encoding="utf-8")
    input_bytes = (env.inputs / "talk.wav").read_bytes()
    plan = env.plan(job_id)
    assert plan.allowed and plan.refusal is None and not plan.resume
    assert sorted(a.format for a in plan.artifacts) == sorted(FORMATS)
    wire = str(plan.to_wire())
    assert directory.name not in wire and str(env.tmp) not in wire
    result = env.delete(job_id, plan=plan).deletion
    assert result.outcome == "completed" and result.artifacts_removed == 5
    assert result.artifacts_remaining == 0
    assert not directory.exists()
    assert tree(env.directory(other)) == keep_other
    assert (env.output / "user-notes.txt").read_text(encoding="utf-8") == "mine"
    assert (env.inputs / "talk.wav").read_bytes() == input_bytes
    with pytest.raises(ControlError) as error:
        env.store.get_job(job_id)
    assert error.value.code is E.JOB_DELETED


def _mutate(env: V8, job_id: str, case: str) -> None:
    directory = env.directory(job_id)
    srt = directory / f"{directory.name}.srt"
    md = directory / f"{directory.name}.md"
    if case == "edited_same_size":
        data = bytearray(srt.read_bytes())
        data[0] ^= 1
        srt.write_bytes(bytes(data))
    elif case == "other_format_bytes":
        srt.write_bytes(md.read_bytes())
    elif case == "extra_file":
        (directory / "notes.txt").write_text("user", encoding="utf-8")
    elif case == "subdirectory":
        (directory / "sub").mkdir()
    elif case == "symlink_artifact":
        target = env.tmp / "elsewhere.srt"
        target.write_bytes(srt.read_bytes())
        srt.unlink()
        srt.symlink_to(target)
    elif case == "hardlink_artifact":
        os.link(srt, env.tmp / "hard.srt")
    elif case == "marker_mismatch":
        (directory / OUTPUT_MARKER).write_text(OTHER + "\n", encoding="utf-8")
    elif case == "marker_symlink":
        real = env.tmp / "marker"
        real.write_text(job_id + "\n", encoding="utf-8")
        (directory / OUTPUT_MARKER).unlink()
        (directory / OUTPUT_MARKER).symlink_to(real)
    elif case == "missing_artifact":
        md.unlink()
    elif case == "directory_symlink":
        moved = env.tmp / "moved"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
    elif case == "quarantine_leftover":
        (directory / ".utteran-deleting-0123456789abcdef").write_text("x", encoding="utf-8")
    else:
        raise AssertionError(case)


REFUSALS = {
    "edited_same_size": "artifact_changed",
    "other_format_bytes": "artifact_changed",
    "extra_file": "unexpected_entries",
    "subdirectory": "unexpected_entries",
    "symlink_artifact": "unsafe_entry",
    "hardlink_artifact": "unsafe_entry",
    "marker_mismatch": "directory_missing",
    "marker_symlink": "directory_missing",
    "missing_artifact": "artifact_missing",
    "directory_symlink": "directory_missing",
    "quarantine_leftover": "unexpected_entries",
}


@POSIX_ONLY
@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_unprovable_artifacts_are_refused_with_zero_side_effects(env: V8, case: str) -> None:
    job_id = env.finished()
    _mutate(env, job_id, case)
    files = tree(env.tmp)
    db = dump(env.store.path)
    plan = env.plan(job_id)
    assert not plan.allowed and plan.refusal == REFUSALS[case] and plan.plan_token is None
    assert tree(env.tmp) == files and dump(env.store.path) == db
    assert env.plan(job_id, "history").allowed  # ③ stays possible


@POSIX_ONLY
def test_a_plan_made_before_a_change_is_stale_and_deletes_nothing(env: V8) -> None:
    job_id = env.finished()
    plan = env.plan(job_id)
    assert plan.allowed
    _mutate(env, job_id, "edited_same_size")
    files = tree(env.tmp)
    with pytest.raises(ControlError) as error:
        env.delete(job_id, plan=plan)
    assert error.value.code is E.DELETION_PLAN_STALE
    assert tree(env.tmp) == files
    assert env.store.get_job(job_id).status == "succeeded"


@POSIX_ONLY
def test_jobs_without_provable_ownership_are_refused(env: V8) -> None:
    failed = env.failed()
    assert env.plan(failed).refusal == "not_succeeded"
    # Evidence written before the per-format record: unverified.
    old = env.finished("old.wav")
    with sqlite3.connect(env.store.path) as db:
        db.execute("DROP TRIGGER job_results_guarded_update")
        db.execute(
            "UPDATE job_results SET evidence = ? WHERE operation_id = ?",
            ('{"artifacts":["' + "a" * 64 + '"],"manifest":"' + "c" * 64 + '"}', old),
        )
    assert env.plan(old).refusal == "evidence_unverified"
    # A snapshot accepted before 0.1.54 (no output_stamp) cannot prove its files.
    legacy = env.finished("legacy.wav")
    real_context = env.store.deletion_context

    def without_stamp(job_id: str) -> Any:
        ctx = real_context(job_id)
        data = ctx.snapshot.to_wire()
        data.pop("output_stamp", None)
        return dataclasses.replace(ctx, snapshot=type(ctx.snapshot).model_validate(data))

    env.store.deletion_context = without_stamp  # type: ignore[method-assign]
    assert env.plan(legacy).refusal == "legacy_output"


@POSIX_ONLY
def test_longer_id_directories_and_ambiguous_ownership(env: V8) -> None:
    path = env.file("same.wav")
    job = env.submit(path).batch.jobs[0]
    snapshot = env.store.get_snapshot(job.id)
    assert snapshot.output_stamp is not None
    short = env.output / job_output_name(
        str(path), snapshot.output_stamp, job.id, parent=str(env.output), id_length=8
    )
    short.mkdir()
    (short / "foreign.txt").write_text("someone else's", encoding="utf-8")
    assert dispatcher(env, WritingRunner(env)).run_once()
    owned = env.directory(job.id)
    assert owned.name.endswith(job.id[4:16])  # the 12-digit form
    plan = env.plan(job.id)
    assert plan.allowed
    # A copy with this job's marker at another id length: ownership is ambiguous.
    copy = env.output / job_output_name(
        str(path), snapshot.output_stamp, job.id, parent=str(env.output), id_length=16
    )
    shutil.copytree(owned, copy)
    assert env.plan(job.id).refusal == "directory_ambiguous"
    shutil.rmtree(copy)  # test cleanup of the synthetic copy only
    env.delete(job.id)
    assert not owned.exists()
    assert (short / "foreign.txt").read_text(encoding="utf-8") == "someone else's"


@POSIX_ONLY
@pytest.mark.parametrize("swap", ["replace_inode", "rewrite_in_place"])
def test_a_file_swapped_after_verification_is_never_deleted(env: V8, swap: str) -> None:
    job_id = env.finished()
    directory = env.directory(job_id)
    srt = directory / f"{directory.name}.srt"

    def fault(stage: str) -> None:
        if stage == "before_remove:srt":
            if swap == "replace_inode":
                staged = directory.parent / "staged"
                staged.write_bytes(b"user content")
                os.replace(staged, srt)
            else:
                with srt.open("r+b") as handle:
                    handle.write(b"X")

    env.service._fault = fault
    result = env.delete(job_id).deletion
    assert result.outcome == "incomplete" and result.artifacts_removed == 0
    assert srt.exists() and srt.read_bytes() != b""  # the swapped file survives
    assert env.store.get_job(job_id).status == "succeeded"  # history not deleted
    assert env.store.deletion_state(job_id) == "artifacts_pending"
    # History-only deletion would orphan the remaining files: refused while pending.
    history = env.plan(job_id, "history")
    assert not history.allowed and history.refusal == "deletion_in_progress"
    with pytest.raises(ControlError) as error:
        env.store.complete_deletion(
            job_id,
            env.store.get_job(job_id).revision,
            "history",
            scope(f"/api/v1/jobs/{job_id}/deletions", env.key()),
            "0" * 64,
        )
    assert error.value.code is E.DELETION_IN_PROGRESS
    assert env.plan(job_id).refusal == "artifact_changed"


@POSIX_ONLY
def test_partial_failure_keeps_history_and_a_new_plan_resumes(
    env: V8, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = env.finished()
    directory = env.directory(job_id)
    real = os.unlink
    failures = {"left": 1}

    def flaky(path: Any, *args: Any, **kwargs: Any) -> None:
        if (
            str(path).startswith(".utteran-deleting-")
            and failures["left"]
            and kwargs.get("dir_fd") is not None
            and env.service._current == "json"
        ):
            failures["left"] -= 1
            raise PermissionError("synthetic")
        real(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", flaky)
    result = env.delete(job_id).deletion
    assert result.outcome == "incomplete" and result.artifacts_remaining >= 1
    assert env.store.get_job(job_id).status == "succeeded"
    assert (directory / f"{directory.name}.json").exists()  # restored, not left half-renamed
    monkeypatch.setattr(os, "unlink", real)
    resume = env.plan(job_id)
    assert resume.allowed and resume.resume
    done = env.delete(job_id, plan=resume).deletion
    assert done.outcome == "completed" and not directory.exists()


@POSIX_ONLY
def test_crash_after_the_files_before_the_tombstone_resumes(env: V8) -> None:
    job_id = env.finished()
    directory = env.directory(job_id)

    def crash(stage: str) -> None:
        if stage == "after_files":
            raise RuntimeError("synthetic crash")

    env.service._fault = crash
    with pytest.raises(RuntimeError):
        env.delete(job_id)
    assert not directory.exists()
    assert env.store.get_job(job_id).status == "succeeded"
    env.service._fault = None
    # A new service (restart): the DB journal alone is enough to finish safely.
    env.service = JobDeletionService(env.store, clock=lambda: env.now)
    plan = env.plan(job_id)
    assert plan.allowed and plan.resume and plan.artifacts == ()
    assert env.delete(job_id, plan=plan).deletion.outcome == "completed"


@POSIX_ONLY
def test_plan_token_expiry_tamper_and_session_binding(env: V8) -> None:
    job_id = env.finished()
    plan = env.plan(job_id)
    token = plan.plan_token
    assert token is not None
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    body = {"schema_version": 1, "mode": "history_and_artifacts", "plan_token": tampered}
    endpoint = f"/api/v1/jobs/{job_id}/deletions"
    revision = env.store.get_job(job_id).revision
    with pytest.raises(ControlError) as error:
        env.service.execute(job_id, revision, scope(endpoint, env.key()), body, session=SESSION)
    assert error.value.code is E.DELETION_PLAN_STALE
    with pytest.raises(ControlError) as error:
        env.delete(job_id, plan=plan, who="another-session-value")
    assert error.value.code is E.DELETION_PLAN_STALE
    with pytest.raises(ControlError) as error:  # history token cannot delete files
        env.service.execute(
            job_id,
            revision,
            scope(endpoint, env.key()),
            {
                "schema_version": 1,
                "mode": "history_and_artifacts",
                "plan_token": env.plan(job_id, "history").plan_token,
            },
            session=SESSION,
        )
    assert error.value.code is E.DELETION_PLAN_STALE
    env.now += timedelta(seconds=301)
    with pytest.raises(ControlError) as error:
        env.delete(job_id, plan=plan)
    assert error.value.code is E.DELETION_PLAN_EXPIRED
    assert env.directory(job_id).is_dir()


@POSIX_ONLY
def test_concurrent_resends_delete_once(env: V8) -> None:
    job_id = env.finished()
    plan = env.plan(job_id)
    key = env.key("del")
    results: list[Any] = []
    errors: list[BaseException] = []

    def send() -> None:
        try:
            results.append(env.delete(job_id, plan=plan, key=key))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=send) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert not errors
    assert sorted(r.replayed for r in results) == [False, True, True, True]
    assert len({str(r.deletion.to_wire()) for r in results}) == 1


def test_artifact_deletion_is_refused_where_the_platform_is_unverified(
    env: V8, monkeypatch: pytest.MonkeyPatch
) -> None:
    import utteran_gui.job_deletion as module

    job_id = env.finished()
    monkeypatch.setattr(module, "platform_supported", lambda: False)
    files = tree(env.tmp)
    plan = env.plan(job_id)
    assert not plan.allowed and plan.refusal == "platform_unverified"
    assert env.plan(job_id, "history").allowed
    assert tree(env.tmp) == files


def test_windows_is_never_reported_as_supported() -> None:
    import utteran_gui.job_deletion as module

    if sys.platform == "win32":
        assert module.platform_supported() is False
    else:
        assert module.platform_supported() is True


# ============================================================ 5. HTTP


def _client(env: V8) -> Any:
    queue = OperationQueue()
    runtime = JobRuntime(
        store_path=env.store.path,
        settings_path=env.settings_path,
        legacy_queue=queue,
        provider=env.provider,
        runner=WritingRunner(env),
        job_dir=env.tmp / "jobs",
        autostart_dispatcher=False,
        setup=_setup(env.tmp),
    )
    return create_app(
        SESSION,
        repo_root=ROOT,
        operation_queue=queue,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=env.settings_path,
        enable_v1_jobs=True,
        v1_jobs_runtime=runtime,
    )


@POSIX_ONLY
def test_http_plan_and_delete_roundtrip_guards_and_value_free_bodies(env: V8) -> None:
    job_id = env.finished("private-name.wav")
    directory = env.directory(job_id)
    origin = {"Origin": "http://" + AUTHORITY}
    with TestClient(
        _client(env), base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}
    ) as c:
        plans = f"/api/v1/jobs/{job_id}/deletion-plans"
        mode = {"schema_version": 1, "mode": "history_and_artifacts"}
        foreign = {"Origin": "http://evil.example"}
        assert c.post(plans, json=mode, headers=foreign).status_code == 403
        assert c.post(plans, json=mode, headers={**origin, SESSION_HEADER: "x"}).status_code == 401
        assert c.post(plans, json={**mode, "force": True}, headers=origin).status_code == 422
        assert (
            c.post(plans, json={"schema_version": 1, "mode": "all"}, headers=origin).status_code
            == 422
        )
        got = c.post(plans, json=mode, headers=origin)
        assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
        plan = JobDeletionPlan.model_validate(got.json())
        assert plan.allowed
        text = got.text
        assert (
            "private-name" not in text and directory.name not in text and str(env.tmp) not in text
        )
        url = f"/api/v1/jobs/{job_id}/deletions"
        body = {"schema_version": 1, "mode": "history_and_artifacts", "plan_token": plan.plan_token}
        key = {"Idempotency-Key": env.key("http-del")}
        assert c.post(url, json=body, headers={**origin, **key}).status_code == 428
        etag = c.get(f"/api/v1/jobs/{job_id}").headers["etag"]
        stale = c.post(url, json=body, headers={**origin, **key, "If-Match": '"99"'})
        assert stale.status_code == 412
        assert (
            c.post(
                url, json={**body, "extra": 1}, headers={**origin, **key, "If-Match": etag}
            ).status_code
            == 422
        )
        done = c.post(url, json=body, headers={**origin, **key, "If-Match": etag})
        assert done.status_code == 200, done.text
        result = JobDeletion.model_validate(done.json())
        assert result.outcome == "completed" and not directory.exists()
        again = c.post(url, json=body, headers={**origin, **key, "If-Match": etag})
        assert again.status_code == 200 and again.json() == done.json()
        gone = c.get(f"/api/v1/jobs/{job_id}")
        assert gone.status_code == 410 and gone.json()["error"]["code"] == "job_deleted"
        for path in ("visibility", "artifacts"):
            assert c.get(f"/api/v1/jobs/{job_id}/{path}").status_code == 410
        assert job_id not in c.get("/api/v1/jobs?visibility=all").text
        assert c.post(plans, json=mode, headers=origin).status_code == 410
        assert c.delete(f"/api/v1/jobs/{job_id}", headers=origin).status_code == 405


def test_http_deletion_is_unavailable_without_layout_v8(tmp_path: Path) -> None:
    from test_b7_api import runtime_for

    env = Env(tmp_path, None)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    queue = OperationQueue()
    app = create_app(
        SESSION,
        repo_root=ROOT,
        operation_queue=queue,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=env.settings_path,
        enable_v1_jobs=True,
        v1_jobs_runtime=runtime_for(env, queue),
    )
    origin = {"Origin": "http://" + AUTHORITY}
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        response = c.post(
            f"/api/v1/jobs/{job.id}/deletion-plans",
            json={"schema_version": 1, "mode": "history"},
            headers=origin,
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "history_deletion_unavailable"


def test_default_legacy_app_has_no_deletion_route(tmp_path: Path) -> None:
    app = create_app(SESSION, repo_root=ROOT)
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        response = c.post(
            "/api/v1/jobs/" + "job_" + "0" * 32 + "/deletions",
            json={},
            headers={"Origin": "http://" + AUTHORITY},
        )
        assert response.status_code == 404


def test_tables_constant_covers_the_dumped_tables() -> None:
    assert "job_snapshots" in TABLES and "job_results" in TABLES


# ================================================== 6. owner review PR #58 (P1-1, P1-2, P2-1)
#
# Deterministic race injection, no timing: the os call the deletion would make next is
# wrapped so that a "third party" acts exactly inside the window the review describes.
# Every test first lets the deletion run as far as the code allows, then checks the
# invariants that must hold whatever the implementation: bytes that were never verified
# survive, and a job is not tombstoned while anything of it is unaccounted for.

QUARANTINE = ".utteran-deleting-"
VICTIM = b"third-party sentinel bytes, never verified by the deletion\n"


def _attempt_artifacts(env: V8, job_id: str) -> tuple[JobDeletionPlan, Any]:
    """Plan ④ and, only if the plan is allowed, execute it. Returns (plan, outcome/error)."""
    plan = env.plan(job_id)
    if not plan.allowed:
        return plan, None
    try:
        return plan, env.delete(job_id, plan=plan)
    except ControlError as error:
        return plan, error


def _not_tombstoned(env: V8, job_id: str) -> None:
    snapshot = env.store.get_snapshot(job_id)  # raises 410 once tombstoned
    assert snapshot.resolved.settings.asr.initial_prompt == SENTINEL
    assert env.store.deletion_state(job_id) != "completed"


def _victims(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.read_bytes() == VICTIM]


def test_review_p1_1_a_name_swapped_after_verification_never_loses_foreign_bytes(
    env: V8, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = env.finished()
    victim = env.output / "victim.bin"
    victim.write_bytes(VICTIM)
    real_unlink = os.unlink
    swapped: list[int] = []

    def unlink(path: Any, *args: Any, dir_fd: int | None = None, **kwargs: Any) -> None:
        # Right after the deletion verified the quarantined file through its fd, a third
        # party replaces that name with another inode (the window of review P1-1).
        if dir_fd is not None and str(path).startswith(QUARANTINE) and not swapped:
            swapped.append(victim.stat().st_ino)
            os.rename(victim, path, dst_dir_fd=dir_fd)
        real_unlink(path, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(os, "unlink", unlink)
    plan, _ = _attempt_artifacts(env, job_id)
    monkeypatch.setattr(os, "unlink", real_unlink)
    assert _victims(env.tmp), "a file the deletion never verified was deleted"
    _not_tombstoned(env, job_id)
    assert not plan.allowed and plan.refusal == "artifact_deletion_disabled"


def test_review_p1_1_a_directory_swapped_before_rmdir_is_never_removed(
    env: V8, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = env.finished()
    foreign = env.output / "foreign-empty-dir"
    foreign.mkdir()
    foreign_ino = foreign.stat().st_ino
    real_rmdir = os.rmdir

    def rmdir(path: Any, *args: Any, dir_fd: int | None = None, **kwargs: Any) -> None:
        # After the deletion's stat of the job directory, the name is pointed elsewhere.
        if dir_fd is not None:
            os.rename(path, "own-moved-aside", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.rename(foreign, path, dst_dir_fd=dir_fd)
        real_rmdir(path, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(os, "rmdir", rmdir)
    plan, _ = _attempt_artifacts(env, job_id)
    monkeypatch.setattr(os, "rmdir", real_rmdir)
    assert any(p.stat().st_ino == foreign_ino for p in env.output.iterdir() if p.is_dir()), (
        "an unrelated directory was removed"
    )
    assert plan.refusal == "artifact_deletion_disabled"


@pytest.mark.parametrize("failure", ["unknown_file", "permission", "renamed"])
def test_review_p1_2_a_directory_that_stays_is_never_reported_as_completed(
    env: V8, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    job_id = env.finished()
    directory = env.directory(job_id)
    real_rmdir = os.rmdir

    def rmdir(path: Any, *args: Any, dir_fd: int | None = None, **kwargs: Any) -> None:
        if dir_fd is not None:
            if failure == "unknown_file":
                (directory / "dropped-by-user.txt").write_bytes(VICTIM)
            elif failure == "permission":
                raise PermissionError(13, "synthetic")
            else:
                os.rename(path, "renamed-meanwhile", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        real_rmdir(path, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(os, "rmdir", rmdir)
    plan, outcome = _attempt_artifacts(env, job_id)
    monkeypatch.setattr(os, "rmdir", real_rmdir)
    completed = (
        outcome is not None
        and not isinstance(outcome, ControlError)
        and (outcome.deletion.outcome == "completed")
    )
    assert not completed, "a deletion that left a directory behind was reported completed"
    _not_tombstoned(env, job_id)
    assert plan.refusal == "artifact_deletion_disabled"


def _pending_from_an_earlier_build(env: V8, job_id: str) -> None:
    """A journal row as the pre-review build wrote it before touching any file."""
    evidence = env.store.artifact_evidence(job_id) or {}
    targets = {
        "id_length": 8,
        "files": [{"format": f, "sha256": d, "size": n} for f, (d, n) in evidence.items()],
    }
    with sqlite3.connect(env.store.path) as db:
        db.execute(
            "INSERT INTO job_deletions VALUES (?, 'history_and_artifacts', "
            "'artifacts_pending', ?, '2026-11-17T00:00:00.000000Z', NULL)",
            (job_id, json.dumps(targets)),
        )


@pytest.mark.parametrize("moved", ["output_parent", "job_directory"])
def test_review_p2_1_a_moved_output_is_not_taken_for_a_finished_deletion(
    env: V8, moved: str
) -> None:
    job_id = env.finished()
    directory = env.directory(job_id)
    names = sorted(p.name for p in directory.iterdir())
    _pending_from_an_earlier_build(env, job_id)
    if moved == "output_parent":
        target = env.tmp / "moved-output"
        env.output.rename(target)
        kept = target / directory.name
    else:
        kept = directory.with_name("renamed-by-user")
        directory.rename(kept)
    plan, outcome = _attempt_artifacts(env, job_id)
    assert (
        outcome is None
        or isinstance(outcome, ControlError)
        or (outcome.deletion.outcome != "completed")
    ), "a moved output was taken for deleted files"
    _not_tombstoned(env, job_id)
    assert env.store.deletion_state(job_id) == "artifacts_pending"
    assert sorted(p.name for p in kept.iterdir()) == names  # the moved files are all there
    history = env.plan(job_id, "history")
    assert not history.allowed and history.refusal == "deletion_in_progress"
    assert plan.refusal == "artifact_deletion_disabled"
