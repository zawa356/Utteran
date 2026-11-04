"""Backend Foundation Hardening: B7-B9 cross-boundary E2E over HTTP (no models).

What this file adds over test_b7_api / test_b8_api / test_b9_events: Setup -> Queue ->
Outbox -> Sync and Job -> Dispatch -> Outbox -> SSE are driven through ONE ``create_app()``
runtime and ONE SQLite control store, over the real FastAPI/ASGI stack (``TestClient``).
Each test is independent and uses only ``tmp_path``.

Fakes and their limits (no claim beyond these):
- B4 evidence: ``FakeProvider`` (test_b7_jobs) feeds synthetic, test-only "ready" evidence
  through the real pure evaluator/resolver. Nothing is persisted as production evidence.
- B3 worker: ``FakeRunner`` starts a real short-lived local Python child, then reports a
  synthetic completion. No transcription model or GPU runs.
- B8 children: ``World`` (test_b8_setup) records argv and simulates setup.ps1 / model
  download. Real setup.ps1, downloads, and the Windows ACLs are not exercised.
- SSE: ``TestClient`` buffers the streamed body, so only the server-side framing,
  cursor, and reconnect semantics are verified -- not a browser ``EventSource``.
- Crash: fault hooks and an unverifiable owner model a crash; no real process kill.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_b7_jobs import SENTINEL, SESSION, Clock, Env, FakeRunner
from test_b8_setup import FW, SECRET, World
from test_b9_events import read_stream
from utteran_contracts.control import EventEnvelope
from utteran_contracts.events import EventReducer, SyncSnapshot
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.events_repository import EventsControlStore, ManagedControlStore
from utteran_control.job_repository import JobControlStore
from utteran_control.recovery import ProcessVerifier, UnknownVerifier
from utteran_control.setup_repository import SetupControlStore
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.events_api import EventStreamConfig
from utteran_gui.job_runtime import JobRuntime, SetupComponents
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.setup_executor import ProductionStepAdapter

ROOT = Path(__file__).parents[1]
AUTHORITY = "127.0.0.1:8765"
E = ErrorCode
DAYS8 = 8 * 24 * 3600
FAST = EventStreamConfig(
    poll_interval=0.01, heartbeat_seconds=0.05, max_stream_seconds=0.3, max_subscribers=4
)
PLAN = {
    "schema_version": 1,
    "profile_kind": "cuda",
    "model_refs": [FW],
    "diarization_enabled": False,
}
V1_READ_PATHS = (
    "/api/v1/jobs",
    "/api/v1/queue",
    "/api/v1/setup-operations",
    "/api/v1/sync",
    "/api/v1/presets",
)


# --------------------------------------------------------------------------- harness


@dataclass
class Stack:
    client: TestClient
    runtime: JobRuntime
    env: Env
    world: World
    runner: FakeRunner
    queue: OperationQueue
    transcript: list[str]

    def call(self, method: str, url: str, **kw: Any) -> Any:
        response = self.client.request(method, url, **kw)
        self.transcript.append(response.text)
        return response

    @property
    def store(self) -> ManagedControlStore:
        store = self.runtime.store
        assert isinstance(store, ManagedControlStore)
        return store

    def stream(self, url: str, **kw: Any) -> tuple[Any, list[dict[str, str]]]:
        response, frames = read_stream(self.client, url, **kw)
        self.transcript.extend(f.get("data", "") for f in frames)
        return response, frames


def h(key: str, match: str | None = None, **extra: str) -> dict[str, str]:
    # Phase A: Idempotency-Key is 16..128 visible ASCII characters.
    result = {"Idempotency-Key": f"{key}-e2e-idem".ljust(16, "0"), **extra}
    if match is not None:
        result["If-Match"] = match
    return result


@pytest.fixture
def stack_factory(tmp_path: Path) -> Iterator[Callable[..., Stack]]:
    """Build one opt-in stack (settings+jobs+setup+events) on a shared tmp layout.

    Calling it again reuses the same Env/World directories and control DB, which models
    an application restart on the same machine state.
    """
    contexts: list[TestClient] = []
    shared: dict[str, Any] = {}

    def build(
        *,
        verifier: ProcessVerifier | None = None,
        clock: Clock | None = None,
        before: Callable[[Env], None] | None = None,
    ) -> Stack:
        if "env" not in shared:
            (tmp_path / "env").mkdir()
            (tmp_path / "world").mkdir()
            shared["env"] = Env(tmp_path / "env", None)
            shared["world"] = World(tmp_path / "world")
        env: Env = shared["env"]
        world: World = shared["world"]
        if before is not None:
            before(env)
        queue = OperationQueue()
        runner = FakeRunner(env)
        runtime = JobRuntime(
            store_path=env.store.path,
            settings_path=env.settings_path,
            legacy_queue=queue,
            provider=env.provider,
            runner=runner,
            job_dir=env.tmp / "jobs",
            autostart_dispatcher=False,
            verifier=verifier,
            clock=clock,
            setup=SetupComponents(
                observer=world.observer,
                adapter=ProductionStepAdapter(world.cli),
                runner=world,
                work_dir=world.tmp,
            ),
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
            enable_v1_setup=True,
            enable_v1_events=True,
            v1_events_config=FAST,
        )
        client = TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION})
        client.__enter__()
        contexts.append(client)
        return Stack(client, runtime, env, world, runner, queue, [])

    yield build
    for client in reversed(contexts):
        # A test may already have shut one app down to model a restart.
        with contextlib.suppress(RuntimeError):
            client.__exit__(None, None, None)


def set_dispatch(s: Stack, mode: str, key: str) -> Any:
    queue = s.call("GET", "/api/v1/queue")
    response = s.call(
        "PATCH",
        "/api/v1/queue/control",
        json={"schema_version": 1, "dispatch": mode},
        headers=h(key, queue.headers["ETag"]),
    )
    assert response.status_code == 200, response.text
    return response


def create_and_approve_plan(s: Stack, suffix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    created = s.call("POST", "/api/v1/setup-plans", json=PLAN, headers=h(f"plan-e2e-{suffix}"))
    assert created.status_code == 201, created.text
    plan = created.json()
    approved = s.call(
        "POST",
        f"/api/v1/setup-plans/{plan['id']}/approve",
        json={"schema_version": 1, "plan_hash": plan["plan_hash"], "accepted_agreement_ids": []},
        headers=h(f"apr-e2e-{suffix}", f'"{plan["revision"]}"'),
    )
    assert approved.status_code == 201, approved.text
    return plan, approved.json()


def start_body(approval: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "plan_ref": {"id": approval["plan_id"], "revision": approval["plan_revision"]},
        "approval_id": approval["id"],
    }


def submit_job(s: Stack, *names: str, key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    session = s.env.session(*(s.env.file(n, n.encode()) for n in names))
    resolved = s.call(
        "POST", "/api/v1/execution-resolutions", json=session, headers=h(f"res-{key}")
    )
    assert resolved.status_code == 201, resolved.text
    body = {
        "schema_version": 1,
        "resolution_token": resolved.json()["token"],
        "editing_session": session,
        "resume_mode": "resume",
    }
    accepted = s.call("POST", "/api/v1/jobs", json=body, headers=h(f"job-{key}"))
    assert accepted.status_code == 202, accepted.text
    return body, accepted.json()


def events_since(s: Stack, cursor: str) -> list[EventEnvelope]:
    response, frames = s.stream(f"/api/v1/events?after={cursor}")
    assert response.status_code == 200
    return [EventEnvelope.model_validate_json(f["data"]) for f in frames if "id" in f]


def full_sync(s: Stack) -> list[SyncSnapshot]:
    first = s.call("GET", "/api/v1/sync?limit=2")
    assert first.status_code == 200, first.text
    pages = [SyncSnapshot.model_validate(first.json())]
    while pages[-1].next_cursor:
        nxt = s.call(
            "GET",
            "/api/v1/sync",
            params={
                "cursor": pages[-1].next_cursor,
                "snapshot_token": pages[0].snapshot_token,
                "limit": "2",
            },
        )
        assert nxt.status_code == 200, nxt.text
        pages.append(SyncSnapshot.model_validate(nxt.json()))
    return pages


def user_version(path: Path) -> int:
    with sqlite3.connect(path) as db:
        return int(db.execute("PRAGMA user_version").fetchone()[0])


# --------------------------------------------------------------------------- 1. opt-in


def test_default_app_is_legacy_only_and_creates_no_control_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(data_root))
    app = create_app(SESSION, repo_root=ROOT)
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        assert c.get("/api/health").json() == {"status": "ok"}
        assert c.get("/api/queue").status_code == 200  # legacy queue API unchanged
        for path in (*V1_READ_PATHS, "/api/v1/events"):
            assert c.get(path).status_code == 404, path
    # Default startup never constructs a control store (B6-B9 are explicit opt-in).
    assert not data_root.exists() or not list(data_root.rglob("*.sqlite3"))


def test_opt_in_exposes_one_guarded_v1_mount_and_closes_the_legacy_queue(
    stack_factory: Callable[..., Stack],
) -> None:
    s = stack_factory()
    raw = TestClient(s.client.app, base_url="http://" + AUTHORITY)
    for path in V1_READ_PATHS:
        assert s.call("GET", path).status_code == 200, path
        assert raw.get(path).status_code == 401, path  # no session
        assert s.call("GET", path, headers={"Host": "evil.example:8765"}).status_code == 403
        assert s.call("GET", path, headers={"Origin": "http://evil.example"}).status_code == 403
    # Opt-in cutover owns the legacy queue: no second dispatcher can accept work.
    assert s.queue.closed
    legacy = s.call("POST", "/api/wizard/jobs", json={"kind": "venv_build", "profile": "cpu"})
    assert legacy.status_code == 409
    # Activation recovery leaves dispatch paused until an explicit, CAS-guarded resume.
    assert s.call("GET", "/api/v1/queue").json()["dispatch"] == "paused"


# --------------------------------------------------------------------------- 2. setup


def test_setup_flow_reaches_queue_outbox_sync_and_diagnostics(
    stack_factory: Callable[..., Stack],
) -> None:
    s = stack_factory()
    base = s.store.read_events().next_cursor
    _, approval = create_and_approve_plan(s, "a-0001")
    _, stale_approval = create_and_approve_plan(s, "b-0001")
    body = start_body(approval)
    accepted = s.call("POST", "/api/v1/setup-operations", json=body, headers=h("start-e2e-a1"))
    assert accepted.status_code == 202, accepted.text
    op = accepted.json()
    replay = s.call("POST", "/api/v1/setup-operations", json=body, headers=h("start-e2e-a1"))
    assert replay.json() == op
    assert replay.headers["ETag"] == accepted.headers["ETag"]
    assert replay.headers["Location"] == accepted.headers["Location"]
    queue = s.call("GET", "/api/v1/queue").json()
    assert [i["resource_kind"] for i in queue["items"]] == ["setup_operation"]
    assert queue["dispatch"] == "paused" and s.world.calls == []  # nothing ran while paused

    set_dispatch(s, "running", "ctl-e2e-setup-1")
    assert s.runtime.dispatcher is not None and s.runtime.dispatcher.run_once()
    final = s.call("GET", f"/api/v1/setup-operations/{op['id']}")
    assert final.status_code == 200 and final.json()["status"] == "succeeded"
    assert s.world.calls, "the fake step runner was driven by the real executor"

    # The second plan was approved against the pre-setup machine state; the setup just
    # changed that state (profile installed), so it must not start.
    refused = s.call(
        "POST",
        "/api/v1/setup-operations",
        json=start_body(stale_approval),
        headers=h("start-e2e-b1"),
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "inventory_stale"

    # /sync, diagnostics and the outbox agree on the same committed final state.
    revision = final.json()["revision"]
    synced = {r.id: r for page in full_sync(s) for r in page.resources}
    assert synced[op["id"]].status.value == "succeeded"
    assert synced[op["id"]].revision == revision
    diagnostics = s.call("GET", f"/api/v1/setup-operations/{op['id']}/diagnostics")
    assert diagnostics.status_code == 200 and diagnostics.headers["ETag"] == f'"{revision}"'
    assert any(entry["attempt_id"] for entry in diagnostics.json()["items"])
    events = [e for e in events_since(s, base) if e.resource_id == op["id"]]
    revisions = [e.resource_revision for e in events]
    assert revisions == sorted(set(revisions)) and revisions[-1] == revision
    assert events[-1].payload.status == "succeeded"


# --------------------------------------------------------------------------- 3. jobs


def test_job_flow_dispatch_outbox_sse_and_reconnect_never_restarts(
    stack_factory: Callable[..., Stack],
) -> None:
    s = stack_factory()
    base = s.store.read_events().next_cursor
    body, accepted = submit_job(s, "a.wav", "b.wav", key="e2e-jobs-0001")
    replay = s.call("POST", "/api/v1/jobs", json=body, headers=h("job-e2e-jobs-0001"))
    assert replay.json() == accepted  # lost-response resend: no second batch
    jobs = [j["id"] for j in accepted["jobs"]]
    assert [j["id"] for j in s.call("GET", "/api/v1/jobs").json()["items"]] == jobs

    queue = s.call("GET", "/api/v1/queue").json()
    held_item = queue["items"][0]["id"]
    hold = s.call(
        "PATCH",
        f"/api/v1/queue/items/{held_item}",
        json={"schema_version": 1, "held": True},
        headers=h("hold-e2e-0001", f'"{queue["revision"]}"'),
    )
    assert hold.status_code == 200, hold.text
    set_dispatch(s, "running", "ctl-e2e-jobs-1")
    assert s.runtime.dispatcher is not None and s.runtime.dispatcher.run_once()
    assert not s.runtime.dispatcher.run_once()  # the held item is never dispatched
    statuses = sorted(j["status"] for j in s.call("GET", "/api/v1/jobs").json()["items"])
    assert statuses == ["held", "succeeded"]
    release = s.call(
        "PATCH",
        f"/api/v1/queue/items/{held_item}",
        json={"schema_version": 1, "held": False},
        headers=h("hold-e2e-0002", s.call("GET", "/api/v1/queue").headers["ETag"]),
    )
    assert release.status_code == 200, release.text
    assert s.runtime.dispatcher.run_once()
    finals = {j["id"]: j for j in s.call("GET", "/api/v1/jobs").json()["items"]}
    assert {j["status"] for j in finals.values()} == {"succeeded"}
    assert len(s.runner.requests) == 2  # exactly one worker launch per job

    events = events_since(s, base)
    epochs = {e.id.split(":")[0] for e in events}
    seqs = [int(e.id.split(":")[1]) for e in events]
    assert len(epochs) == 1 and seqs == sorted(set(seqs))  # one epoch, strictly monotonic
    for job_id, job in finals.items():
        own = [e for e in events if e.resource_id == job_id]
        assert own[-1].resource_revision == job["revision"]
        assert own[-1].payload.status == "succeeded"

    # Reconnecting with Last-Event-ID replays nothing and starts nothing.
    response, frames = s.stream("/api/v1/events", headers={"Last-Event-ID": events[-1].id})
    assert response.status_code == 200 and not [f for f in frames if "id" in f]
    assert not s.runtime.dispatcher.run_once() and len(s.runner.requests) == 2


def test_unverified_candidate_is_refused_without_legacy_fallback(
    stack_factory: Callable[..., Stack],
) -> None:
    s = stack_factory()
    s.env.provider.recipe = "whisper-cpp-vulkan"  # no ready evidence path for the worker
    session = s.env.session(s.env.file("a.wav"))
    resolved = s.call(
        "POST", "/api/v1/execution-resolutions", json=session, headers=h("res-e2e-unver-1")
    )
    assert resolved.status_code == 409, resolved.text
    assert resolved.json()["error"]["code"] == "execution_unavailable"
    assert s.call("GET", "/api/v1/jobs").json()["items"] == []
    assert s.call("GET", "/api/queue").json() == []  # nothing fell back to the legacy queue
    assert s.runner.requests == []


# --------------------------------------------------------------------------- 4. SSE + sync


def test_sync_then_events_converge_across_setup_and_job(
    stack_factory: Callable[..., Stack],
) -> None:
    s = stack_factory()
    reducer = EventReducer()
    initial = full_sync(s)
    reducer.apply_sync(initial[0])

    _, approval = create_and_approve_plan(s, "c-0001")
    op = s.call(
        "POST", "/api/v1/setup-operations", json=start_body(approval), headers=h("start-e2e-c1")
    ).json()
    _, accepted = submit_job(s, "c.wav", key="e2e-mix-0001")
    set_dispatch(s, "running", "ctl-e2e-mix-1")
    assert s.runtime.dispatcher is not None
    assert s.runtime.dispatcher.run_once() and s.runtime.dispatcher.run_once()

    events = events_since(s, initial[0].event_cursor)
    assert {e.resource_kind for e in events} >= {"setup_operation", "job", "queue"}
    outcomes = [reducer.apply_event(e) for e in events]
    assert set(outcomes) <= {"applied", "stale"} and "applied" in outcomes
    # Duplicates and other-epoch events never move the client state.
    assert {reducer.apply_event(e) for e in events} == {"duplicate"}
    foreign = events[-1].model_copy(update={"id": "0" * 32 + ":999999"})
    assert reducer.apply_event(foreign) == "resync"

    final = full_sync(s)
    expected = {r.id: r.status.value for page in final for r in page.resources}
    assert {k: reducer.statuses[k] for k in expected} == expected
    assert expected[op["id"]] == "succeeded"
    assert expected[accepted["jobs"][0]["id"]] == "succeeded"
    assert reducer.queue_revision == final[0].queue.revision
    assert final[0].event_cursor == events[-1].id  # sync watermark == last committed event


# --------------------------------------------------------------------------- 5. migration


def test_opt_in_migrates_v3_to_v6_with_restorable_pre_cutover_backup(
    stack_factory: Callable[..., Stack],
) -> None:
    before: dict[str, Any] = {}

    def seed_v3(env: Env) -> None:
        # Existing B7 (layout v3) store with one waiting job, written by the B7 class.
        assert user_version(env.store.path) == 3
        before["job"] = env.submit(env.file("old.wav")).batch.jobs[0].id

    s = stack_factory(before=seed_v3)
    live = s.store.path
    assert user_version(live) == 6
    backup = s.runtime.last_backup
    assert backup is not None and backup.is_file()
    # The pre-cutover backup keeps the pre-migration layout (taken before the v6 open).
    assert user_version(backup) == 3
    # One-way: every older class refuses the migrated file.
    for old in (JobControlStore, SetupControlStore, EventsControlStore):
        with pytest.raises(ControlError) as excinfo:
            old(live)
        assert excinfo.value.code is E.STORE_SCHEMA_UNSUPPORTED
    # Restore drill on a copy: the B7 class opens the backup and sees the old job.
    restored = backup.with_name("restored-drill.sqlite3")
    shutil.copyfile(backup, restored)
    old_store = JobControlStore(restored)
    assert old_store.get_job(before["job"]).status == "queued"
    assert user_version(restored) == 3  # opening with the owning class does not migrate


@pytest.mark.parametrize("old", [SetupControlStore, EventsControlStore])
def test_v4_and_v5_migrate_to_v6_and_failed_migration_rolls_back(
    tmp_path: Path, old: type[JobControlStore]
) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    old(path)
    version = user_version(path)
    assert version in {4, 5}

    def crash(where: str) -> None:
        if where == "before_commit":
            raise ControlError(E.STORE_FULL)

    with pytest.raises(ControlError):
        ManagedControlStore(path, _fault=crash)
    assert user_version(path) == version  # all-or-nothing: the old layout is intact
    old(path)  # and still opens with its owning class
    ManagedControlStore(path)
    assert user_version(path) == 6


# --------------------------------------------------------------------------- 6. restart


def test_restart_with_unverifiable_owner_never_starts_a_second_child(
    stack_factory: Callable[..., Stack],
) -> None:
    first = stack_factory()
    try:
        _, accepted = submit_job(first, "r1.wav", "r2.wav", key="e2e-restart-1")
        first.runner.keep_alive = True  # the child outlives its supervisor ("crash")
        set_dispatch(first, "running", "ctl-e2e-restart-1")
        assert first.runtime.dispatcher is not None and first.runtime.dispatcher.run_once()
        running = first.runtime.store.active_job()  # type: ignore[union-attr]
        assert running is not None
        first.client.__exit__(None, None, None)  # app shutdown leaves the attempt for recovery

        second = stack_factory(verifier=UnknownVerifier())
        jobs = {j["id"]: j for j in second.call("GET", "/api/v1/jobs").json()["items"]}
        stuck = [j for j in jobs.values() if j["status"] == "running"]
        assert len(stuck) == 1 and stuck[0]["recovery_required"] is not None
        assert second.call("GET", "/api/v1/queue").json()["dispatch"] == "paused"
        set_dispatch(second, "running", "ctl-e2e-restart-2")
        assert second.runtime.dispatcher is not None
        assert not second.runtime.dispatcher.run_once()  # slot kept: fail closed
        assert second.runner.requests == []  # no second child for any job
        waiting = [j for j in jobs.values() if j["id"] != stuck[0]["id"]]
        assert [j["id"] for j in waiting] and {j["status"] for j in waiting} <= {"held", "queued"}
        assert set(jobs) == {j["id"] for j in accepted["jobs"]}
    finally:
        for child in first.runner.children:
            child.kill()
            child.wait()


# --------------------------------------------------------------------------- 7. privacy


def test_private_values_stay_in_the_private_db_and_limits_hold(
    stack_factory: Callable[..., Stack], caplog: pytest.LogCaptureFixture
) -> None:
    clock = Clock()
    s = stack_factory(clock=clock)
    old_cursor = s.store.read_events().next_cursor
    _, approval = create_and_approve_plan(s, "p-0001")
    op = s.call(
        "POST", "/api/v1/setup-operations", json=start_body(approval), headers=h("start-e2e-p1")
    ).json()
    _, accepted = submit_job(s, "p.wav", key="e2e-priv-0001")
    job = accepted["jobs"][0]["id"]
    set_dispatch(s, "running", "ctl-e2e-priv-1")
    assert s.runtime.dispatcher is not None
    assert s.runtime.dispatcher.run_once() and s.runtime.dispatcher.run_once()

    job_diag = s.call("GET", f"/api/v1/jobs/{job}/diagnostics").json()
    setup_diag = s.call("GET", f"/api/v1/setup-operations/{op['id']}/diagnostics").json()
    events_since(s, old_cursor)
    full_sync(s)

    # Bounded inputs and tamper-evident cursors.
    assert s.call("GET", "/api/v1/sync?limit=0").status_code == 422
    assert s.call("GET", "/api/v1/sync?limit=1000").status_code == 422
    tampered = s.call(
        "GET", "/api/v1/sync", params={"cursor": "y1." + "0" * 32 + ".1.2." + "f" * 64}
    )
    assert tampered.status_code in {400, 409}
    assert s.call("GET", "/api/v1/events?after=not-a-cursor").status_code == 400
    bad_diag = s.call("GET", f"/api/v1/jobs/{job}/diagnostics", params={"cursor": "d2.bad"})
    assert bad_diag.status_code in {400, 409}

    # Compaction (after the 7-day retention) keeps diagnostics and attempt IDs intact
    # and expires old SSE cursors explicitly instead of serving newer data.
    clock.advance(DAYS8)
    report = s.store.compact_events(keep_latest=0)
    assert report.deleted > 0
    assert s.call("GET", f"/api/v1/jobs/{job}/diagnostics").json() == job_diag
    assert s.call("GET", f"/api/v1/setup-operations/{op['id']}/diagnostics").json() == setup_diag
    expired = s.call("GET", f"/api/v1/events?after={old_cursor}")
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "event_cursor_expired"
    synced = {r.id: r.status.value for page in full_sync(s) for r in page.resources}
    assert synced[job] == "succeeded" and synced[op["id"]] == "succeeded"

    public = "\n".join(s.transcript) + "\n" + caplog.text
    for secret in (SENTINEL, SECRET, str(s.env.tmp), str(s.world.tmp)):
        assert secret not in public
    # Documented, intentional: the private DB keeps the prompt in plain text with no
    # retention policy yet (owner decision pending; see persistence-recovery.md).
    assert _db_contains(s.store.path, SENTINEL)


def _db_contains(path: Path, needle: str) -> bool:
    with sqlite3.connect(path) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            for row in db.execute(f"SELECT * FROM {table}"):
                if needle in json.dumps(row, default=str, ensure_ascii=False):
                    return True
    return False
