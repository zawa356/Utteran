"""B9 events (SSE), sync, compaction, diagnostics and the pure client reducer."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_b7_jobs import SESSION, Clock, Env, FakeRunner
from test_b8_setup import SECRET, World, approve, plan, run_once, start
from utteran_contracts.control import EventEnvelope
from utteran_contracts.events import EventReducer, SyncSnapshot
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.events_repository import (
    ManagedControlStore,
    SyncService,
    diagnostics,
    read_sync,
)
from utteran_control.job_repository import JobControlStore
from utteran_control.setup_repository import SetupControlStore
from utteran_control.store import ControlStore
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.events_api import EventStreamConfig
from utteran_gui.job_runtime import JobRuntime, SetupComponents
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.setup_executor import ProductionStepAdapter

E = ErrorCode
ROOT = Path(__file__).parents[1]
AUTHORITY = "127.0.0.1:8765"
SENTINEL = "PRIVATE_B9_PROMPT_秘密 /home/secret/path.wav"
_n = iter(range(10**6))


def submit(store: ControlStore, payload: dict[str, Any] | None = None) -> str:
    accepted = store.submit(
        "/api/v1/jobs",
        f"b9-submit-key-{next(_n):06d}",
        {"schema_version": 1, "kind": "job", "private_payload": payload or {}},
    )
    return accepted.operation.id


def cancel(store: ControlStore, operation_id: str) -> None:
    view = store.get(operation_id)
    store.transition(
        operation_id,
        {
            "schema_version": 1,
            "expected_revision": view.revision,
            "to_status": "cancelled",
            "reason_code": "user_cancel",
        },
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> ManagedControlStore:
    return ManagedControlStore(tmp_path / "c" / "operations.sqlite3", clock=clock)


# --------------------------------------------------------------------------- HTTP fixture


def build(tmp_path: Path, config: EventStreamConfig):
    (tmp_path / "env").mkdir()
    (tmp_path / "world").mkdir()
    env = Env(tmp_path / "env", None)
    world = World(tmp_path / "world")
    queue = OperationQueue()
    runtime = JobRuntime(
        store_path=env.store.path,
        settings_path=env.settings_path,
        legacy_queue=queue,
        provider=env.provider,
        runner=FakeRunner(env),
        job_dir=env.tmp / "jobs",
        autostart_dispatcher=False,
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
        v1_events_config=config,
    )
    return app, runtime, world


FAST = EventStreamConfig(
    poll_interval=0.01, heartbeat_seconds=0.05, max_stream_seconds=0.4, max_subscribers=4
)


@pytest.fixture
def http(tmp_path: Path):
    app, runtime, world = build(tmp_path, FAST)
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        c.runtime = runtime  # type: ignore[attr-defined]
        c.world = world  # type: ignore[attr-defined]
        yield c


def read_stream(client: TestClient, url: str, **kw: Any) -> tuple[Any, list[dict[str, str]]]:
    frames: list[dict[str, str]] = []
    with client.stream("GET", url, **kw) as response:
        if response.status_code != 200:
            response.read()
            return response, frames
        current: dict[str, str] = {}
        for line in response.iter_lines():
            if line == "":
                if current:
                    frames.append(current)
                current = {}
                continue
            name, _, value = line.partition(":")
            current[name or "comment"] = value.lstrip(" ")
    return response, frames


# --------------------------------------------------------------------------- 1. outbox


def test_outbox_is_monotonic_and_only_committed(tmp_path, clock) -> None:
    fail = {"on": False}

    def fault(where: str) -> None:
        if fail["on"] and where == "before_commit":
            raise ControlError(E.STORE_FULL)

    store = ManagedControlStore(tmp_path / "c" / "operations.sqlite3", clock=clock, _fault=fault)
    a = submit(store)
    fail["on"] = True
    with pytest.raises(ControlError):
        submit(store)
    fail["on"] = False
    cancel(store, a)
    page = store.read_events()
    seqs = [int(e.id.split(":")[1]) for e in page.events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # Every revision of the operation has exactly one committed state_changed event.
    revisions = [e.resource_revision for e in page.events if e.resource_id == a]
    assert revisions == [1, 2]
    assert {e.resource_id for e in page.events} == {a, "queue"}  # rolled-back op absent


# --------------------------------------------------------------------------- 2. SSE


def test_sse_framing_headers_and_filters(http: TestClient) -> None:
    store = http.runtime.store  # type: ignore[attr-defined]
    base = store.read_events().next_cursor
    first = submit(store, {"note": SENTINEL})
    submit(store)
    response, frames = read_stream(http, f"/api/v1/events?after={base}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"
    events = [f for f in frames if "id" in f]
    assert [f["event"] for f in events] == [
        "state_changed",
        "queue_changed",
        "state_changed",
        "queue_changed",
    ]
    for f in events:
        envelope = EventEnvelope.model_validate_json(f["data"])
        assert envelope.id == f["id"] and "\n" not in f["data"]
    assert any("comment" in f for f in frames)  # keepalive comments are distinguishable
    assert SENTINEL not in "".join(f.get("data", "") for f in frames)
    _, filtered = read_stream(http, f"/api/v1/events?after={base}&resource_id={first}")
    ids = [f for f in filtered if "id" in f]
    assert len(ids) == 1 and json.loads(ids[0]["data"])["resource_id"] == first
    # Same cursor via Last-Event-ID; both given and equal is accepted.
    _, by_header = read_stream(http, "/api/v1/events", headers={"Last-Event-ID": base})
    assert [f["id"] for f in by_header if "id" in f] == [f["id"] for f in events]
    ok, _ = read_stream(http, f"/api/v1/events?after={base}", headers={"Last-Event-ID": base})
    assert ok.status_code == 200


@pytest.mark.parametrize(
    "query, headers, status, code",
    [
        ("?after=a:1", {"Last-Event-ID": "b:1"}, 400, "cursor_conflict"),
        ("?after=not-a-cursor", {}, 400, "cursor_invalid"),
        ("?after=" + "0" * 32 + ":1", {}, 410, "event_cursor_expired"),
        ("?resource_id=../etc", {}, 400, "request_invalid"),
    ],
)
def test_sse_rejections_before_connect(http, query, headers, status, code) -> None:
    response = http.get("/api/v1/events" + query, headers=headers)
    assert response.status_code == status and response.json()["error"]["code"] == code


def test_sse_future_cursor_and_auth(http: TestClient) -> None:
    epoch = http.runtime.store.read_events().next_cursor.split(":")[0]  # type: ignore[attr-defined]
    future = http.get(f"/api/v1/events?after={epoch}:999999")
    assert future.status_code == 400
    raw = TestClient(http.app, base_url="http://" + AUTHORITY)
    assert raw.get("/api/v1/events").status_code == 401
    raw.cookies.set("utteran_gui_session", SESSION)  # EventSource: cookie only, no header
    response, _ = read_stream(raw, "/api/v1/events")
    assert response.status_code == 200
    evil, _ = read_stream(raw, "/api/v1/events", headers={"Host": "evil.example:8765"})
    assert evil.status_code == 403


def test_sse_subscriber_limit(tmp_path: Path) -> None:
    app, _, _ = build(tmp_path, EventStreamConfig(max_subscribers=0))
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        response = c.get("/api/v1/events")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "event_stream_limit"


def test_sse_resync_on_epoch_rotation_mid_stream(tmp_path: Path) -> None:
    app, runtime, _ = build(tmp_path, EventStreamConfig(poll_interval=0.02, max_stream_seconds=5))
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        frames: list[str] = []
        # TestClient buffers the whole streamed body, so rotate from another thread while
        # the server-side generator is still polling.
        threading.Timer(0.3, runtime.store.rotate_epoch).start()
        with c.stream("GET", "/api/v1/events") as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                frames.append(line)
        assert "event: resync_required" in frames
        index = frames.index("event: resync_required")
        assert json.loads(frames[index + 1].removeprefix("data: "))["reason_code"] == (
            "event_cursor_expired"
        )
        assert not any(line.startswith("id:") for line in frames)  # no fabricated id


def test_sse_reconnect_never_restarts_work(http: TestClient) -> None:
    world: World = http.world  # type: ignore[attr-defined]
    store = http.runtime.store  # type: ignore[attr-defined]
    accepted = start(store, world, approve(store, world, plan(store, world)))
    before = store.get_setup(accepted.body["id"])
    for _ in range(3):
        read_stream(http, "/api/v1/events")
    assert world.calls == [] and store.get_setup(accepted.body["id"]) == before


# --------------------------------------------------------------------------- 3. sync


def test_sync_single_transaction_and_stable_pages(store: ManagedControlStore) -> None:
    ids = [submit(store) for _ in range(5)]
    calls: list[bool] = []
    original = store._transaction

    def counting(*, write: bool = True):
        calls.append(write)
        return original(write=write)

    store._transaction = counting  # type: ignore[method-assign]
    service = SyncService(store)
    first = service.page(limit=2, scope="viewer")
    assert calls == [False]  # resources + queue + watermark in ONE read transaction
    store._transaction = original  # type: ignore[method-assign]
    cancel(store, ids[3])
    late = submit(store)
    pages = [first]
    while pages[-1].next_cursor:
        pages.append(
            service.page(
                pages[-1].next_cursor,
                snapshot_token=first.snapshot_token,
                limit=2,
                scope="viewer",
            )
        )
    seen = [r.id for p in pages for r in p.resources]
    assert seen == ids and late not in seen
    assert {p.event_cursor for p in pages} == {first.event_cursor}
    assert {p.queue.revision for p in pages} == {first.queue.revision}
    assert next(r for p in pages for r in p.resources if r.id == ids[3]).status == "queued"
    assert first.inventory_revision is None  # no setup observation yet: never invented
    with pytest.raises(ControlError) as excinfo:
        service.page(first.next_cursor, limit=2, scope="other-viewer")
    assert excinfo.value.code is E.CURSOR_INVALID
    with pytest.raises(ControlError) as excinfo:
        service.page(first.next_cursor, snapshot_token="f" * 32, limit=2, scope="viewer")
    assert excinfo.value.code is E.CURSOR_INVALID
    with pytest.raises(ControlError) as excinfo:
        SyncService(store).page(first.next_cursor, limit=2, scope="viewer")  # "restart"
    assert excinfo.value.code is E.LIST_CURSOR_EXPIRED
    with pytest.raises(ControlError):
        service.page("j2." + "0" * 32 + ".1.2." + "0" * 64, limit=2, scope="viewer")


def test_sync_then_subscribe_has_no_gap_or_duplicate(store: ManagedControlStore) -> None:
    reducer = EventReducer()
    submit(store)
    snapshot = SyncService(store).page()
    reducer.apply_sync(snapshot)
    late = submit(store)
    page = store.read_events(snapshot.event_cursor)
    outcomes = [reducer.apply_event(e) for e in page.events]
    assert outcomes and set(outcomes) == {"applied"}
    assert reducer.statuses[late] == "queued"
    # Replaying the same events (reconnect) is idempotent.
    assert {reducer.apply_event(e) for e in page.events} == {"duplicate"}
    # A slow GET answered after newer events is discarded.
    assert reducer.apply_resource(late, 1, "queued") == "stale"


def test_sync_http_and_inventory_revision(http: TestClient) -> None:
    world: World = http.world  # type: ignore[attr-defined]
    store = http.runtime.store  # type: ignore[attr-defined]
    plan(store, world)
    response = http.get("/api/v1/sync?limit=1")
    assert response.status_code == 200
    body = SyncSnapshot.model_validate(response.json())
    assert body.inventory_revision == 1
    assert http.get("/api/v1/sync?limit=0").status_code == 422
    assert http.get("/api/v1/sync?cursor=bogus").status_code == 400
    assert http.get(f"/api/v1/sync?snapshot_token={'a' * 32}").status_code == 400


# --------------------------------------------------------------------------- 4. reducer


def test_reducer_ordering_rules() -> None:
    reducer = EventReducer()

    def event(seq: int, rid: str, rev: int, epoch: str = "a" * 32) -> EventEnvelope:
        return EventEnvelope.model_validate(
            {
                "schema_version": 1,
                "id": f"{epoch}:{seq}",
                "resource_id": rid,
                "resource_kind": "job",
                "resource_revision": rev,
                "timestamp": "2026-11-02T00:00:00Z",
                "type": "state_changed",
                "payload": {"status": "running", "reason_code": None, "attempt_id": None},
            }
        )

    job = "job_" + "1" * 32
    assert reducer.apply_event(event(1, job, 1)) == "resync"  # no sync yet
    reducer.epoch = "a" * 32
    assert reducer.apply_event(event(5, job, 3)) == "applied"  # gap 1..4 is fine
    assert reducer.apply_event(event(5, job, 3)) == "duplicate"
    assert reducer.apply_event(event(4, job, 2)) == "duplicate"  # older seq
    assert reducer.apply_event(event(9, job, 2)) == "stale"  # older revision
    assert reducer.apply_event(event(10, job, 4, "b" * 32)) == "resync"  # other epoch


# --------------------------------------------------------------------------- 5. compaction


def test_compaction_respects_retention_floor_and_active_resources(store, clock) -> None:
    done, active = submit(store), submit(store)
    cancel(store, done)
    before = store.read_events().next_cursor
    with pytest.raises(ControlError):
        store.compact_events(retention_seconds=3600)  # never below 7 days
    young = store.compact_events(keep_latest=0)
    assert young.deleted == 0  # everything is younger than 7 days
    clock.advance(8 * 24 * 3600)
    fresh = submit(store)  # young event: kept
    report = store.compact_events(keep_latest=0)
    assert report.deleted > 0 and report.kept_protected > 0
    remaining = {e.resource_id for e in store.read_events().events}
    assert fresh in remaining and done not in remaining
    with sqlite3.connect(store.path) as db:
        kept = {r[0] for r in db.execute("SELECT DISTINCT resource_id FROM outbox")}
    # Rows of a non-final resource are kept as evidence (below the floor, not streamed).
    assert active in kept and done not in kept
    with pytest.raises(ControlError) as excinfo:
        store.read_events("{}:1".format(before.split(":")[0]))
    assert excinfo.value.code is E.EVENT_CURSOR_EXPIRED
    assert store.read_events(report.floor_cursor).events  # at the floor: valid
    again = store.compact_events(keep_latest=0)
    assert again.deleted == 0  # idempotent
    with sqlite3.connect(store.path) as db, pytest.raises(sqlite3.DatabaseError):
        db.execute("DELETE FROM outbox")  # only inside a compaction transaction


def test_compaction_keep_latest_and_crash(tmp_path, clock) -> None:
    fault = {"on": False}

    def hook(where: str) -> None:
        if fault["on"] and where == "compaction_before_commit":
            raise ControlError(E.STORE_FULL)

    store = ManagedControlStore(tmp_path / "c" / "operations.sqlite3", clock=clock, _fault=hook)
    for _ in range(4):
        cancel(store, submit(store))
    clock.advance(8 * 24 * 3600)
    total = len(store.read_events().events)
    fault["on"] = True
    with pytest.raises(ControlError):
        store.compact_events(keep_latest=3)
    assert len(store.read_events().events) == total  # crash: nothing deleted, floor same
    fault["on"] = False
    report = store.compact_events(keep_latest=3)
    assert len(store.read_events(report.floor_cursor).events) == 3
    assert report.deleted == total - 3


def test_v5_migration_and_refusal(tmp_path) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    v4 = SetupControlStore(path)
    op = submit(v4)
    migrated = ManagedControlStore(path)
    assert migrated.get(op).id == op
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
    for older in (ControlStore, JobControlStore, SetupControlStore):
        with pytest.raises(ControlError) as excinfo:
            older(path)
        assert excinfo.value.code is E.STORE_SCHEMA_UNSUPPORTED
    v3 = tmp_path / "d" / "operations.sqlite3"
    JobControlStore(v3)
    resources = read_sync(JobControlStore(v3))
    assert resources[3] is None  # v3: no setup observation counter


# --------------------------------------------------------------------------- 6. diagnostics


def test_setup_diagnostics_are_sanitized_and_paged(http: TestClient) -> None:
    world: World = http.world  # type: ignore[attr-defined]
    store = http.runtime.store  # type: ignore[attr-defined]
    accepted = start(store, world, approve(store, world, plan(store, world)))
    store.set_dispatch(store.queue().revision, "running")  # activation left it paused
    run_once(store, world)
    sid = accepted.body["id"]
    response = http.get(f"/api/v1/setup-operations/{sid}/diagnostics")
    assert response.status_code == 200
    body = response.json()
    codes = [i["code"] for i in body["items"]]
    assert codes[0] == "submitted" and "completion_verified" in codes
    assert any(i["code"] == "step_verified" and i["stage"] == "venv" for i in body["items"])
    assert set(body["items"][0]) == {"timestamp", "code", "message_key", "stage", "attempt_id"}
    text = response.text
    assert SECRET not in text and str(world.tmp) not in text and "powershell" not in text
    page1 = diagnostics(store, sid, "setup_operation", limit=3)
    page2 = diagnostics(store, sid, "setup_operation", page1.next_cursor, limit=3)
    assert [i.code for i in page1.items + page2.items] == codes[:6]
    assert http.get(f"/api/v1/jobs/{sid}/diagnostics").status_code == 404
    assert http.get("/api/v1/jobs/job_" + "0" * 32 + "/diagnostics").status_code == 404
    assert http.get(f"/api/v1/setup-operations/{sid}/diagnostics?cursor=x").status_code == 400
    assert http.get(f"/api/v1/setup-operations/{sid}").json()["id"] == sid  # GET unchanged


def test_private_payload_never_reaches_public_surfaces(http: TestClient) -> None:
    store = http.runtime.store  # type: ignore[attr-defined]
    op = submit(store, {"note": SENTINEL})
    surfaces = [
        http.get("/api/v1/sync").text,
        http.get(f"/api/v1/jobs/{op}/diagnostics").text,
        "".join(
            f.get("data", "")
            for f in read_stream(http, "/api/v1/events?after=" + store.read_events().events[0].id)[
                1
            ]
        ),
    ]
    for text in surfaces:
        assert SENTINEL not in text and "/home/secret" not in text


# --------------------------------------------------------------------------- 7. legacy


def test_default_app_routes_golden_and_no_b9_imports(tmp_path: Path) -> None:
    import subprocess
    import sys

    app = create_app("s", repo_root=ROOT)
    paths = sorted({getattr(r, "path", "") for r in app.routes})
    assert "/api/wizard/jobs/{job_id}/events" in paths  # legacy SSE kept
    assert not any(p.startswith("/api/v1") for p in paths)
    golden = Path(__file__).with_name("fixtures") / "b9_default_routes.json"
    assert json.loads(golden.read_text(encoding="utf-8")) == paths
    with pytest.raises(ValueError):
        create_app("s", repo_root=ROOT, enable_v1_events=True)
    script = (
        "import sys\nfrom pathlib import Path\nfrom utteran_gui.api import create_app\n"
        f"create_app('s', repo_root=Path({str(ROOT)!r}))\n"
        "bad=[m for m in ('utteran_gui.events_api','utteran_control.events_repository',"
        "'utteran_contracts.events','utteran_control') if m in sys.modules]\nprint(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_open_streams_do_not_block_writers(tmp_path: Path) -> None:
    import time

    app, runtime, _ = build(tmp_path, EventStreamConfig(poll_interval=0.01, max_stream_seconds=1))
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        threads = [
            threading.Thread(target=read_stream, args=(c, "/api/v1/events")) for _ in range(3)
        ]
        for t in threads:
            t.start()
        time.sleep(0.2)
        began = time.monotonic()
        ids = [submit(runtime.store) for _ in range(20)]
        assert time.monotonic() - began < 5  # no write transaction is held by a stream
        for t in threads:
            t.join()
        assert len(ids) == 20
