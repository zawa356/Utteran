"""Layout v7: hiding finished jobs from the history list, and the read-only artifact view.

Hiding is a presentation attribute. These tests pin that it changes nothing else: job
state and revision, snapshot, attempts, replays, queue, outbox, B9 sync. Synthetic data.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_application import SyntheticASR
from test_b7_api import AUTHORITY, ROOT
from test_b7_b3_model_route import B3Runner, _synthetic_core, model_files, offline
from test_b7_jobs import SESSION, Env, FakeRunner, dispatcher, scope
from test_b8_setup import World
from utteran_contracts.jobs import JobArtifacts, JobList, JobVisibility
from utteran_control.errors import ControlError
from utteran_control.errors import ErrorCode as E
from utteran_control.events_repository import ManagedControlStore
from utteran_control.history_repository import HistoryControlStore
from utteran_control.job_repository import JobControlStore
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.job_artifacts import job_artifacts, owned_directory
from utteran_gui.job_dispatcher import OUTPUT_MARKER
from utteran_gui.job_runtime import JobRuntime, SetupComponents
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.setup_executor import ProductionStepAdapter

__all__ = ["offline"]  # re-exported pytest fixture
TABLES = (
    "operations",
    "operation_history",
    "operation_private",
    "job_snapshots",
    "job_state",
    "job_results",
    "attempts",
    "attempt_private",
    "queue",
    "queue_items",
    "outbox",
    "replays",
    "queue_replays",
    "batch_replays",
    "job_replays",
    "counters",
)


def _setup(tmp: Path) -> SetupComponents:
    """The B8 wiring that makes the runtime open the managed (v7) layout."""
    root = tmp / "setup-world"
    root.mkdir()
    world = World(root)
    return SetupComponents(
        observer=world.observer,
        adapter=ProductionStepAdapter(world.cli),
        runner=world,
        work_dir=world.tmp,
    )


def version(path: Path) -> int:
    with sqlite3.connect(path) as db:
        return int(db.execute("PRAGMA user_version").fetchone()[0])


def dump(path: Path) -> dict[str, list[Any]]:
    with sqlite3.connect(path) as db:
        return {t: db.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in TABLES}


class V7(Env):
    """The B7 test environment on the layout the v1 runtime uses."""

    def __init__(self, tmp: Path) -> None:
        super().__init__(tmp, None)
        path = self.store.path
        self.store = HistoryControlStore(path)
        self.admission.store = self.store

    def finished(self, name: str = "a.wav") -> str:
        job = self.submit(self.file(name)).batch.jobs[0]
        assert dispatcher(self, FakeRunner(self)).run_once()
        assert self.store.get_job(job.id).status == "succeeded"
        return job.id


@pytest.fixture
def env(tmp_path: Path) -> V7:
    return V7(tmp_path)


# --------------------------------------------------------------------------- migration


def test_v6_migrates_to_v7_additively_and_older_classes_refuse_it(tmp_path: Path) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    ManagedControlStore(path)
    assert version(path) == 6
    backup = path.with_name("pre-v7.sqlite3")
    assert ManagedControlStore.backup_before_open(path, backup) == 6
    before = dump(path)
    store = HistoryControlStore(path)
    assert version(path) == 7
    assert dump(path) == before  # every existing table: identical rows
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM job_visibility").fetchone()[0] == 0
    for old in (JobControlStore, ManagedControlStore):
        with pytest.raises(ControlError) as error:
            old(path)
        assert error.value.code is E.STORE_SCHEMA_UNSUPPORTED
    assert version(path) == 7  # a refused open changes nothing
    # The pre-migration backup still opens with the v6 class (rollback of the build).
    assert version(backup) == 6
    ManagedControlStore(backup)
    assert version(backup) == 6
    assert store.list_jobs().items == ()


def test_failed_v7_migration_rolls_back_to_a_complete_v6_store(tmp_path: Path) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    ManagedControlStore(path)
    before = dump(path)

    def crash(where: str) -> None:
        if where == "before_commit":
            raise ControlError(E.STORE_FULL)

    with pytest.raises(ControlError):
        HistoryControlStore(path, _fault=crash)
    assert version(path) == 6 and dump(path) == before
    ManagedControlStore(path)  # still a valid v6 store


def test_runtime_activation_backs_up_v6_before_opening_v7(tmp_path: Path) -> None:
    env = Env(tmp_path, None)
    path = env.store.path
    del env.store
    ManagedControlStore(path)
    assert version(path) == 6
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
        # 0.1.57 (C2c): the runtime target is layout v8 (a HistoryControlStore subclass);
        # the v6 pre-open backup is what this test is about and is unchanged.
        assert isinstance(runtime.store, HistoryControlStore) and version(path) == 8
        assert runtime.last_backup is not None and version(runtime.last_backup) == 6
        restored = tmp_path / "restore-drill.sqlite3"
        shutil.copyfile(runtime.last_backup, restored)
        ManagedControlStore(restored)
        assert version(restored) == 6
    finally:
        runtime.shutdown()


# ------------------------------------------------------------------- hide / show (store)


def test_hiding_changes_only_the_visibility_row(env: V7) -> None:
    job_id = env.finished()
    job, snapshot = env.store.get_job(job_id), env.store.get_snapshot(job_id)
    before = dump(env.store.path)
    assert env.store.visibility(job_id) == JobVisibility(
        schema_version=1, job_id=job_id, hidden=False, revision=0, updated_at=None
    )
    hidden = env.store.set_visibility(job_id, 0, True)
    assert (hidden.hidden, hidden.revision) == (True, 1) and hidden.updated_at is not None
    # Job state, revision, snapshot, attempts, replays, queue, counters and the outbox
    # are byte-identical: no state change, no event, nothing deleted.
    assert dump(env.store.path) == before
    assert env.store.get_job(job_id) == job and env.store.get_snapshot(job_id) == snapshot
    shown = env.store.set_visibility(job_id, 1, False)
    assert (shown.hidden, shown.revision) == (False, 2)
    assert dump(env.store.path) == before


def test_visibility_cas_and_idempotent_repeat(env: V7) -> None:
    job_id = env.finished()
    with pytest.raises(ControlError) as error:
        env.store.set_visibility(job_id, 5, True)  # stale / wrong revision
    assert error.value.code is E.REVISION_CONFLICT
    first = env.store.set_visibility(job_id, 0, True)
    # Same intent again (e.g. the response was lost): no change, no new revision,
    # whatever revision the caller still holds.
    for stale in (0, 1, 99):
        assert env.store.set_visibility(job_id, stale, True) == first
    with pytest.raises(ControlError) as error:
        env.store.set_visibility(job_id, 0, False)  # a real change needs revision 1
    assert error.value.code is E.REVISION_CONFLICT
    for bad in (None, "1", 1.0):
        with pytest.raises(ControlError) as error:
            env.store.set_visibility(job_id, bad, False)  # type: ignore[arg-type]
        assert error.value.code is E.REQUEST_INVALID
    with pytest.raises(ControlError) as error:
        env.store.set_visibility(job_id, 1, 1)  # type: ignore[arg-type]
    assert error.value.code is E.REQUEST_INVALID


def test_only_finished_jobs_can_be_hidden_and_unknown_jobs_are_404(env: V7) -> None:
    waiting = env.submit(env.file("a.wav")).batch.jobs[0].id
    with pytest.raises(ControlError) as error:
        env.store.set_visibility(waiting, 0, True)
    assert error.value.code is E.INVALID_TRANSITION
    assert env.store.visibility(waiting).revision == 0
    with pytest.raises(ControlError) as error:
        env.store.visibility("job_" + "0" * 32)
    assert error.value.code is E.RESOURCE_NOT_FOUND
    with sqlite3.connect(env.store.path) as db, pytest.raises(sqlite3.DatabaseError):
        db.execute("INSERT INTO job_visibility VALUES ('job_x', 1, 1, 'now')")
        db.execute("DELETE FROM job_visibility")  # rows are never removed


def test_list_filter_and_cursor_are_bound_to_the_visibility(tmp_path: Path) -> None:
    env = V7(tmp_path)
    ids = [env.finished(f"{i}.wav") for i in range(5)]
    env.store.set_visibility(ids[1], 0, True)
    env.store.set_visibility(ids[3], 0, True)

    def listed(**kwargs: Any) -> list[str]:
        return [job.id for job in env.store.list_jobs(**kwargs).items]

    assert listed() == listed(visibility="visible") == [ids[0], ids[2], ids[4]]
    assert listed(visibility="hidden") == [ids[1], ids[3]]
    assert listed(visibility="all") == ids
    with pytest.raises(ControlError) as error:
        env.store.list_jobs(visibility="everything")
    assert error.value.code is E.REQUEST_INVALID
    page = env.store.list_jobs(limit=2, visibility="all")
    assert page.next_cursor is not None
    # Hiding after the first page does not leak into the existing cursor ...
    env.store.set_visibility(ids[4], 0, True)
    rest = env.store.list_jobs(page.next_cursor, limit=2, visibility="all")
    assert [j.id for j in (*page.items, *rest.items)] == ids[:4]
    # ... and a cursor is refused with a different filter.
    for other in (None, "visible", "hidden"):
        with pytest.raises(ControlError) as error:
            env.store.list_jobs(page.next_cursor, limit=2, visibility=other)
        assert error.value.code is E.CURSOR_INVALID


def test_layouts_before_v7_have_no_visibility_and_say_so(tmp_path: Path) -> None:
    env = Env(tmp_path, None)  # layout v3
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    assert [j.id for j in env.store.list_jobs().items] == [job.id]
    assert [j.id for j in env.store.list_jobs(visibility="all").items] == [job.id]
    for value in ("visible", "hidden"):
        with pytest.raises(ControlError) as error:
            env.store.list_jobs(visibility=value)
        assert error.value.code is E.REQUEST_INVALID
    assert not hasattr(env.store, "set_visibility")


def test_hidden_jobs_keep_working_for_retry_rerun_queue_and_sync(env: V7) -> None:
    failed = env.submit(env.file("a.wav")).batch.jobs[0].id

    class Failing(FakeRunner):
        def execute(self, request: Any, **kwargs: Any) -> Any:
            raise RuntimeError("synthetic failure")

    assert dispatcher(env, Failing(env)).run_once()
    assert env.store.get_job(failed).status == "failed"
    env.store.set_visibility(failed, 0, True)
    sync_before = env.store.snapshot()
    revision = env.store.get_job(failed).revision
    retry = env.admission.retry(failed, revision, scope(f"/api/v1/jobs/{failed}/retry", env.key()))
    assert retry.job.retry_of == failed  # the chain still points at the hidden job
    # The new job is visible and queued; the hidden source is unchanged.
    assert [j.id for j in env.store.list_jobs().items] == [retry.job.id]
    assert env.store.get_job(failed).revision == revision
    assert [item.resource_id for item in env.store.queue().items] == [retry.job.id]
    # B9 sync still reports every operation, hidden or not (state is not visibility).
    after = env.store.snapshot()
    known = {item.id for item in after.operations}
    assert {item.id for item in sync_before.operations} <= known and failed in known
    # Hiding itself wrote no event: the cursor moved only for the retry submission.
    cursor = env.store.snapshot().event_cursor
    env.store.set_visibility(retry.job.id, 0, False)  # no-op
    env.store.set_visibility(failed, 1, False)
    assert env.store.snapshot().event_cursor == cursor


# ------------------------------------------------------------------------------- HTTP


def _client(env: V7, runner: FakeRunner | None = None) -> tuple[Any, JobRuntime]:
    queue = OperationQueue()
    runtime = JobRuntime(
        store_path=env.store.path,
        settings_path=env.settings_path,
        legacy_queue=queue,
        provider=env.provider,
        runner=runner or FakeRunner(env),
        job_dir=env.tmp / "jobs",
        autostart_dispatcher=False,
        setup=_setup(env.tmp),
    )
    app = create_app(
        SESSION,
        repo_root=ROOT,
        operation_queue=queue,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=env.settings_path,
        enable_v1_jobs=True,
        v1_jobs_runtime=runtime,
    )
    return app, runtime


def test_http_visibility_roundtrip_guards_and_value_free_bodies(env: V7) -> None:
    job_id = env.finished()
    app, _ = _client(env)
    origin = {"Origin": "http://" + AUTHORITY}
    base = "http://" + AUTHORITY
    with TestClient(app, base_url=base, headers={SESSION_HEADER: SESSION}) as c:
        url = f"/api/v1/jobs/{job_id}/visibility"
        got = c.get(url)
        assert got.status_code == 200 and got.headers["etag"] == '"0"'
        assert got.json() == {
            "schema_version": 1,
            "job_id": job_id,
            "hidden": False,
            "revision": 0,
            "updated_at": None,
        }
        body = {"schema_version": 1, "hidden": True}
        assert c.put(url, json=body, headers=origin).status_code == 428  # If-Match required
        stale = c.put(url, json=body, headers={**origin, "If-Match": '"3"'})
        assert stale.status_code == 412 and stale.json()["error"]["code"] == "revision_conflict"
        done = c.put(url, json=body, headers={**origin, "If-Match": '"0"'})
        assert done.status_code == 200 and done.headers["etag"] == '"1"'
        assert JobVisibility.model_validate(done.json()).hidden is True
        again = c.put(url, json=body, headers={**origin, "If-Match": '"0"'})
        assert again.status_code == 200 and again.json() == done.json()
        bad = c.put(
            url, json={"schema_version": 1, "hidden": "yes"}, headers=origin | {"If-Match": '"1"'}
        )
        assert bad.status_code == 422
        extra = c.put(
            url,
            json={"schema_version": 1, "hidden": False, "delete": True},
            headers=origin | {"If-Match": '"1"'},
        )
        assert extra.status_code == 422  # no hidden side door to deletion
        assert c.get(url, headers={SESSION_HEADER: "wrong"}).status_code == 401
        assert c.get("/api/v1/jobs/job_nope/visibility").status_code == 404
        assert c.delete(url, headers=origin).status_code == 405

        def ids(query: str = "") -> list[str]:
            listing = JobList.model_validate(c.get("/api/v1/jobs" + query).json())
            return [job.id for job in listing.items]

        assert ids() == [] and ids("?visibility=visible") == []
        assert ids("?visibility=hidden") == [job_id] == ids("?visibility=all")
        assert c.get("/api/v1/jobs?visibility=nope").status_code == 422
        # The job itself is still readable by id and unchanged.
        assert c.get(f"/api/v1/jobs/{job_id}").json()["status"] == "succeeded"


def test_http_refuses_visibility_on_a_store_without_layout_v7(tmp_path: Path) -> None:
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
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        response = c.get(f"/api/v1/jobs/{job.id}/visibility")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "history_visibility_unavailable"
        assert c.get("/api/v1/jobs").status_code == 200
        assert c.get("/api/v1/jobs?visibility=hidden").status_code == 422


# ------------------------------------------------------------------ artifacts (read-only)


def _real_job(env: V7, monkeypatch: pytest.MonkeyPatch) -> str:
    model_files(env.model)
    backend = SyntheticASR()
    _synthetic_core(monkeypatch, backend)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    runner = B3Runner(env, backend)
    assert dispatcher(env, runner).run_once()
    assert env.store.get_job(job.id).status == "succeeded", runner.codes
    return job.id


def _artifacts(env: V7, job_id: str) -> JobArtifacts:
    job = env.store.get_job(job_id)
    return job_artifacts(
        env.store.get_snapshot(job_id),
        job_id,
        env.store.artifact_evidence(job_id),
        attempted=bool(job.attempts),
    )


def test_artifact_view_reports_format_size_integrity_and_never_a_name_or_path(
    env: V7, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _real_job(env, monkeypatch)
    directory = owned_directory(env.store.get_snapshot(job_id), job_id)
    assert directory is not None
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    view = _artifacts(env, job_id)
    assert view.state == "available" and view.unexpected_count == 0
    assert [item.format for item in view.items] == sorted(
        (p.suffix[1:] for p in directory.iterdir() if p.name != OUTPUT_MARKER),
        key=("srt", "vtt", "json", "txt", "md").index,
    )
    assert {item.integrity for item in view.items} == {"verified"}
    wire = str(view.to_wire())
    assert directory.name not in wire and str(env.tmp) not in wire
    assert set(view.to_wire()["items"][0]) == {"format", "size", "integrity"}
    # Reading changed nothing on disk.
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()} == before

    # An edited artifact and files Utteran did not write are reported, not hidden.
    edited = next(p for p in directory.iterdir() if p.suffix == ".srt")
    edited.write_text("edited by the user", encoding="utf-8")
    (directory / "notes.txt").write_text("mine", encoding="utf-8")
    (directory / "sub").mkdir()
    changed = _artifacts(env, job_id)
    assert {i.format: i.integrity for i in changed.items}["srt"] == "changed"
    assert changed.unexpected_count == 2
    assert all(i.integrity == "verified" for i in changed.items if i.format != "srt")


def test_artifact_view_states_for_missing_legacy_and_unstarted_jobs(
    env: V7, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _real_job(env, monkeypatch)
    snapshot = env.store.get_snapshot(job_id)
    directory = owned_directory(snapshot, job_id)
    assert directory is not None
    # Another job's marker in that directory: not ours any more -> nothing is listed.
    (directory / OUTPUT_MARKER).write_text("job_" + "f" * 32 + "\n", encoding="utf-8")
    assert _artifacts(env, job_id).state == "missing"
    assert _artifacts(env, job_id).items == ()
    data = snapshot.to_wire()
    del data["output_stamp"]
    legacy = type(snapshot).model_validate(data)
    assert job_artifacts(legacy, job_id, None, attempted=True).state == "legacy"
    waiting = env.submit(env.file("b.wav")).batch.jobs[0].id
    assert _artifacts(env, waiting).state == "none"


def test_http_artifacts_route_is_read_only_and_guarded(
    env: V7, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _real_job(env, monkeypatch)
    app, _ = _client(env)
    origin = {"Origin": "http://" + AUTHORITY}
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        url = f"/api/v1/jobs/{job_id}/artifacts"
        response = c.get(url)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        view = JobArtifacts.model_validate(response.json())
        assert view.state == "available" and view.items
        assert str(env.tmp) not in response.text
        for method in ("post", "put", "patch", "delete"):
            assert getattr(c, method)(url, headers=origin).status_code in (404, 405)
        assert c.get(url, headers={SESSION_HEADER: "wrong"}).status_code == 401
        assert c.get("/api/v1/jobs/job_" + "0" * 32 + "/artifacts").status_code == 404


# ------------------------------------------------- PR #52 review (P2-1, P2-2), 0.1.55


def test_list_offers_no_validator_that_survives_a_visibility_change(env: V7) -> None:
    """P2-1: hide/show changes the list but not the job-state revision, so the list
    must not be served with an ETag made of that revision (and is never cacheable)."""
    first, second = env.finished("a.wav"), env.finished("b.wav")
    app, _ = _client(env)
    origin = {"Origin": "http://" + AUTHORITY}
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:

        def listing(query: str = "") -> Any:
            response = c.get("/api/v1/jobs" + query)
            assert response.status_code == 200
            assert "etag" not in response.headers
            assert response.headers["cache-control"] == "no-store"
            return response.json()

        before = listing()
        job_revisions = {j["id"]: j["revision"] for j in before["items"]}
        assert [j["id"] for j in before["items"]] == [first, second]
        url = f"/api/v1/jobs/{first}/visibility"
        body = {"schema_version": 1, "hidden": True}
        assert c.put(url, json=body, headers=origin | {"If-Match": '"0"'}).status_code == 200
        hidden = listing()
        assert [j["id"] for j in hidden["items"]] == [second]  # the content changed ...
        assert hidden["revision"] == before["revision"]  # ... the state revision did not
        assert [j["id"] for j in listing("?visibility=hidden")["items"]] == [first]
        shown = {"schema_version": 1, "hidden": False}
        assert c.put(url, json=shown, headers=origin | {"If-Match": '"1"'}).status_code == 200
        after = listing()
        assert [j["id"] for j in after["items"]] == [first, second]
        assert after["revision"] == before["revision"]
        # Job state revisions and their own ETags are untouched by hide and show.
        assert {j["id"]: j["revision"] for j in after["items"]} == job_revisions
        one = c.get(f"/api/v1/jobs/{first}")
        assert one.headers["etag"] == f'"{job_revisions[first]}"'


def test_layout_without_visibility_keeps_its_list_etag(tmp_path: Path) -> None:
    from test_b7_api import runtime_for

    env = Env(tmp_path, None)
    env.submit(env.file("a.wav"))
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
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        response = c.get("/api/v1/jobs")
        assert response.headers["etag"] == f'"{response.json()["revision"]}"'
        assert response.headers["cache-control"] == "no-store"


def test_artifact_is_verified_against_the_record_of_its_own_format(
    env: V7, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P2-2: the .md bytes saved as .srt are a changed .srt, not a verified one."""
    job_id = _real_job(env, monkeypatch)
    directory = owned_directory(env.store.get_snapshot(job_id), job_id)
    assert directory is not None
    evidence = env.store.artifact_evidence(job_id)
    assert evidence is not None and set(evidence) == {"srt", "json", "md"}
    files = {p.suffix[1:]: p for p in directory.iterdir() if p.name != OUTPUT_MARKER}
    for fmt, (digest, size) in evidence.items():
        assert files[fmt].stat().st_size == size and len(digest) == 64
    assert {i.format: i.integrity for i in _artifacts(env, job_id).items} == {
        "srt": "verified",
        "json": "verified",
        "md": "verified",
    }
    # Replace the SRT with the (genuine, recorded) bytes of the MD artifact of this job.
    md_bytes = files["md"].read_bytes()
    assert md_bytes != files["srt"].read_bytes()
    files["srt"].write_bytes(md_bytes)
    swapped = {i.format: i.integrity for i in _artifacts(env, job_id).items}
    assert swapped == {"srt": "changed", "json": "verified", "md": "verified"}
    # Same size, different bytes is also a change (the digest decides, not the size).
    original_json = files["json"].read_bytes()
    files["json"].write_bytes(bytes(b ^ 1 for b in original_json))
    assert {i.format: i.integrity for i in _artifacts(env, job_id).items}["json"] == "changed"
    # A known-format file the completed job did not produce is not one of its artifacts.
    (directory / f"{directory.name}.vtt").write_text("WEBVTT", encoding="utf-8")
    view = _artifacts(env, job_id)
    assert "vtt" not in {i.format for i in view.items} and view.unexpected_count == 1


def test_evidence_without_a_per_file_record_never_verifies(
    env: V7, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rows written before per-file records (or by a caller that has none) give no basis:
    every file is unverified, even though its digest is in the job-wide list."""
    job_id = _real_job(env, monkeypatch)
    snapshot = env.store.get_snapshot(job_id)
    directory = owned_directory(snapshot, job_id)
    assert directory is not None
    for no_basis in ({}, None):
        view = job_artifacts(snapshot, job_id, no_basis, attempted=True)
        assert view.state == "available" and view.unexpected_count == 0
        assert {i.integrity for i in view.items} == {"unverified"}
    assert env.store.artifact_evidence(env.submit(env.file("q.wav")).batch.jobs[0].id) is None
    with sqlite3.connect(env.store.path) as db:
        db.execute("DROP TRIGGER job_results_immutable_UPDATE")
        # A row as written before per-file records existed: job-wide digests only.
        old = '{"artifacts":["' + "a" * 64 + '"],"manifest":"' + "c" * 64 + '"}'
        db.execute("UPDATE job_results SET evidence = ? WHERE operation_id = ?", (old, job_id))
    assert env.store.artifact_evidence(job_id) == {}
    legacy = _artifacts(env, job_id)
    assert legacy.items and {i.integrity for i in legacy.items} == {"unverified"}
    # A record naming one format twice is refused as corrupt, not guessed.
    twice = (
        '{"artifacts":[],"manifest":"x","files":[{"format":"srt","sha256":"'
        + "a" * 64
        + '","size":1},{"format":"srt","sha256":"'
        + "b" * 64
        + '","size":1}]}'
    )
    with sqlite3.connect(env.store.path) as db:
        db.execute("UPDATE job_results SET evidence = ? WHERE operation_id = ?", (twice, job_id))
    with pytest.raises(ControlError) as error:
        env.store.artifact_evidence(job_id)
    assert error.value.code is E.STORE_CORRUPT


def test_runtime_can_open_layout_v7_without_setup_services_and_not_by_default(
    tmp_path: Path,
) -> None:
    """Development harnesses (tools/ui2) need history visibility without B8 setup."""

    def runtime(root: Path, **kwargs: Any) -> tuple[JobRuntime, Path]:
        root.mkdir()
        env = Env(root, None)
        path = env.store.path
        return (
            JobRuntime(
                store_path=path,
                settings_path=env.settings_path,
                legacy_queue=OperationQueue(),
                provider=env.provider,
                runner=FakeRunner(env),
                job_dir=root / "jobs",
                autostart_dispatcher=False,
                **kwargs,
            ),
            path,
        )

    explicit, path = runtime(tmp_path / "a", store_type=HistoryControlStore)
    explicit.activate()
    try:
        assert isinstance(explicit.store, HistoryControlStore) and version(path) == 7
    finally:
        explicit.shutdown()
    default, path = runtime(tmp_path / "b")
    default.activate()
    try:
        assert not isinstance(default.store, HistoryControlStore) and version(path) == 3
    finally:
        default.shutdown()
