"""PR #33 review fixes B9-P2-2 (diagnostics vs. compaction) and B9-P2-3 (fixed pages)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_b7_jobs import SESSION, Clock, Env, FakeRunner
from test_b8_setup import World, approve, plan, run_once, start
from test_b9_events import AUTHORITY, FAST, build, cancel, submit
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.events_repository import (
    DiagnosticsService,
    EventsControlStore,
    ManagedControlStore,
)
from utteran_control.job_repository import JobControlStore
from utteran_control.setup_repository import SetupControlStore
from utteran_control.store import ControlStore
from utteran_gui.api import SESSION_HEADER
from utteran_gui.job_runtime import JobRuntime, SetupComponents
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.process_verifier import current_owner
from utteran_gui.setup_executor import ProductionStepAdapter

E = ErrorCode
DAYS8 = 8 * 24 * 3600


def wire(page: Any) -> list[dict[str, Any]]:
    return [item.to_wire() for item in page.items]


def all_pages(service: DiagnosticsService, op: str, kind: str, limit: int = 200) -> list[Any]:
    page = service.page(op, kind, limit=limit)
    pages = [page]
    while pages[-1].next_cursor:
        pages.append(service.page(op, kind, pages[-1].next_cursor, limit=limit))
    return pages


_keys = iter(range(10**6))


def refresh_with_attempts(store: ControlStore) -> str:
    """A refresh operation (own slot): submitted -> running (attempt) -> failed."""
    op = store.submit(
        "/api/v1/environment-refreshes",
        f"refresh-key-{next(_keys):08d}",
        {"schema_version": 1, "kind": "refresh_operation", "private_payload": {}},
    ).operation.id
    grant = store.begin_attempt(op, store.get(op).revision, current_owner())
    store.transition(
        op,
        {
            "schema_version": 1,
            "expected_revision": store.get(op).revision,
            "to_status": "failed",
            "reason_code": "classified_failure",
        },
        fencing_token=grant.fencing_token,
    )
    return op


@pytest.fixture
def runtime(tmp_path: Path):
    (tmp_path / "env").mkdir()
    (tmp_path / "world").mkdir()
    env = Env(tmp_path / "env", None)
    world = World(tmp_path / "world")
    clock = Clock()
    runtime = JobRuntime(
        store_path=env.store.path,
        settings_path=env.settings_path,
        legacy_queue=OperationQueue(),
        provider=env.provider,
        runner=FakeRunner(env),
        job_dir=env.tmp / "jobs",
        autostart_dispatcher=False,
        clock=clock,
        setup=SetupComponents(
            observer=world.observer,
            adapter=ProductionStepAdapter(world.cli),
            runner=world,
            work_dir=world.tmp,
        ),
    )
    runtime.activate()
    # Route the B7 admission helpers through the activated (v6) store.
    env.store, env.admission = runtime.store, runtime.admission  # type: ignore[assignment]
    yield runtime, env, world, clock
    runtime.shutdown(5)


# --------------------------------------------------------------------------- B9-P2-2


def test_diagnostics_identical_before_and_after_compaction(runtime) -> None:
    rt, env, world, clock = runtime
    store: ManagedControlStore = rt.store
    store.set_dispatch(store.queue().revision, "running")
    job = env.submit(env.file("a.wav")).batch.jobs[0].id
    assert rt.dispatcher.run_once()  # real B7 dispatch: attempt + launch + terminal
    setup = start(store, world, approve(store, world, plan(store, world))).body["id"]
    run_once(store, world)  # B8 setup: attempt + step journal
    plain = submit(store)  # no attempt at all
    cancel(store, plain)
    service = DiagnosticsService(store)
    resources = [(job, "job"), (setup, "setup_operation"), (plain, "job")]
    before = {op: wire(service.page(op, kind)) for op, kind in resources}
    assert any(item["attempt_id"] for item in before[job])
    assert any(item["attempt_id"] for item in before[setup])
    assert not any(item["attempt_id"] for item in before[plain])
    with sqlite3.connect(store.path) as db:
        outbox_before = db.execute("SELECT count(*) FROM outbox").fetchone()[0]
    clock.advance(DAYS8)
    report = store.compact_events(keep_latest=0)
    with sqlite3.connect(store.path) as db:
        outbox_after = db.execute("SELECT count(*) FROM outbox").fetchone()[0]
    assert report.deleted > 0 and outbox_after < outbox_before  # really compacted
    after = {op: wire(DiagnosticsService(store).page(op, kind)) for op, kind in resources}
    assert after == before  # every public field, including attempt_id, unchanged


def test_multiple_attempts_are_not_mixed_up(runtime) -> None:
    rt, _, world, _ = runtime
    store: ManagedControlStore = rt.store
    store.set_dispatch(store.queue().revision, "running")
    setup = start(store, world, approve(store, world, plan(store, world))).body["id"]
    other = refresh_with_attempts(store)
    from test_b8_setup import Info

    world.install("vulkan")  # attempt 1 sees a changed inventory -> waiting_user
    run_once(store, world)
    world.cli.profiles["vulkan"] = Info(False, None, None)
    obs = world.observer.observe()
    view = store.get_setup(setup)
    from utteran_control.store import IdempotencyScope

    store.resume_setup(
        setup,
        view.revision,
        IdempotencyScope(f"/api/v1/setup-operations/{setup}/resume", "resume-key-0000001"),
        fingerprint=obs.fingerprint,
        credentials=obs.credentials,
    )
    run_once(store, world)  # attempt 2 succeeds
    final = store.get_setup(setup)
    assert final.status == "succeeded" and [a.number for a in final.attempts] == [1, 2]
    with sqlite3.connect(store.path) as db:
        rows = db.execute(
            "SELECT a.operation_id, a.revision, a.attempt_id, t.operation_id "
            "FROM operation_history_attempts a LEFT JOIN attempts t ON t.id = a.attempt_id"
        ).fetchall()
        history = db.execute("SELECT count(*) FROM operation_history").fetchone()[0]
    assert len(rows) == history and len({(r[0], r[1]) for r in rows}) == history  # 1:1
    for operation_id, _, attempt_id, owner in rows:
        assert attempt_id is None or owner == operation_id  # never another resource's
    first, second = (a.id for a in final.attempts)
    used = [r[2] for r in sorted(rows) if r[0] == setup and r[2]]
    assert used[: used.index(second)] and set(used[: used.index(second)]) == {first}
    assert set(used[used.index(second) :]) == {second}  # attempts in revision order
    assert {r[2] for r in rows if r[0] == other} - {None} != {first, second}


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "c" / "operations.sqlite3"


def test_v5_to_v6_backfills_known_attempts_and_keeps_lost_ones_null(store_path: Path) -> None:
    clock = Clock()
    v5 = EventsControlStore(store_path, clock=clock)
    old = refresh_with_attempts(v5)
    clock.advance(DAYS8)
    for _ in range(3):
        cancel(v5, submit(v5))
    v5.compact_events(keep_latest=4)  # the old refresh's outbox rows are gone in v5
    recent = refresh_with_attempts(v5)
    with sqlite3.connect(store_path) as db:
        history_before = db.execute(
            "SELECT * FROM operation_history ORDER BY operation_id, revision"
        ).fetchall()
    v6 = ManagedControlStore(store_path, clock=clock)
    with sqlite3.connect(store_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
        states = dict(
            ((r[0], r[1]), (r[2], r[3]))
            for r in db.execute(
                "SELECT operation_id, revision, attempt_id, attempt_state "
                "FROM operation_history_attempts"
            )
        )
        assert (
            db.execute("SELECT * FROM operation_history ORDER BY operation_id, revision").fetchall()
            == history_before
        )
    assert all(v == (None, "lost") for k, v in states.items() if k[0] == old)
    recent_states = [v for k, v in states.items() if k[0] == recent]
    assert any(a is not None and s == "backfilled" for a, s in recent_states)
    items = DiagnosticsService(v6).page(old, "refresh_operation").items
    assert all(item.attempt_id is None for item in items)  # lost: never guessed


def test_v6_migration_failure_and_concurrent_open(store_path: Path) -> None:
    EventsControlStore(store_path)

    def fault(where: str) -> None:
        if where == "before_commit":
            raise ControlError(E.STORE_FULL)

    with pytest.raises(ControlError):
        ManagedControlStore(store_path, _fault=fault)
    with sqlite3.connect(store_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 5
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "operation_history_attempts" not in tables
    EventsControlStore(store_path)  # still a complete v5 store
    errors: list[BaseException] = []

    def open_store() -> None:
        try:
            ManagedControlStore(store_path, timeout=10)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=open_store) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    for older in (ControlStore, JobControlStore, SetupControlStore, EventsControlStore):
        with pytest.raises(ControlError) as excinfo:
            older(store_path)
        assert excinfo.value.code is E.STORE_SCHEMA_UNSUPPORTED


def test_v1_to_v6_and_sidecar_is_immutable(store_path: Path) -> None:
    v1 = ControlStore(store_path)
    op = submit(v1)
    store = ManagedControlStore(store_path)
    cancel(store, op)
    with sqlite3.connect(store_path) as db:
        for sql in (
            "UPDATE operation_history_attempts SET attempt_id = 'x'",
            "DELETE FROM operation_history_attempts",
            "UPDATE operation_history SET reason_code = 'x'",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(sql)
        states = [r[0] for r in db.execute("SELECT attempt_state FROM operation_history_attempts")]
    assert sorted(states) == ["backfilled", "recorded"]


def test_compaction_policy_floor_and_epoch_unchanged(store_path: Path) -> None:
    clock = Clock()
    store = ManagedControlStore(store_path, clock=clock)
    with pytest.raises(ControlError):
        store.compact_events(retention_seconds=3600)
    for _ in range(3):
        cancel(store, submit(store))
    epoch = store.read_events().next_cursor.split(":")[0]
    clock.advance(DAYS8)
    report = store.compact_events(keep_latest=2)
    assert report.floor_cursor.split(":")[0] == epoch
    assert len(store.read_events(report.floor_cursor).events) == 2
    with pytest.raises(ControlError) as excinfo:
        store.read_events(f"{epoch}:1")
    assert excinfo.value.code is E.EVENT_CURSOR_EXPIRED


# --------------------------------------------------------------------------- B9-P2-3


def _many(store: ManagedControlStore, count: int) -> str:
    op = submit(store)
    for index in range(count):
        store.set_hold(op, store.get(op).revision, held=index % 2 == 0)
    return op


def test_pages_are_fixed_while_history_grows(store_path: Path) -> None:
    clock = Clock()  # frozen: every row has the same timestamp
    store = ManagedControlStore(store_path, clock=clock)
    op = _many(store, 5)
    service = DiagnosticsService(store)
    first = service.page(op, "job", limit=2)
    expected = wire(DiagnosticsService(store).page(op, "job"))
    store.set_hold(op, store.get(op).revision, held=False)  # new revision, same timestamp
    clock.value = clock.value.replace(year=clock.value.year - 1)  # clock goes backwards
    store.set_hold(op, store.get(op).revision, held=True)
    pages = [first]
    while pages[-1].next_cursor:
        pages.append(service.page(op, "job", pages[-1].next_cursor, limit=2))
    assert [i for p in pages for i in wire(p)] == expected  # no duplicate, no gap
    assert {p.revision for p in pages} == {first.revision}
    fresh = service.page(op, "job")
    assert fresh.revision == first.revision + 2 and len(fresh.items) == len(expected) + 2


def test_pages_are_fixed_across_compaction_and_setup_journal(runtime) -> None:
    rt, _, world, clock = runtime
    store: ManagedControlStore = rt.store
    store.set_dispatch(store.queue().revision, "running")
    setup = start(store, world, approve(store, world, plan(store, world))).body["id"]
    run_once(store, world)
    service = DiagnosticsService(store)
    expected = wire(service.page(setup, "setup_operation"))
    first = service.page(setup, "setup_operation", limit=3)
    clock.advance(DAYS8)
    cancel(store, submit(store))
    store.compact_events(keep_latest=0)
    pages = [first]
    while pages[-1].next_cursor:
        pages.append(service.page(setup, "setup_operation", pages[-1].next_cursor, limit=3))
    assert [i for p in pages for i in wire(p)] == expected
    assert {"venv", None} <= {i["stage"] for i in expected}


def _flip(text: str, index: int) -> str:
    replacement = "0" if text[index] != "0" else "1"
    return text[:index] + replacement + text[index + 1 :]


def test_cursor_binding_tampering_and_expiry(store_path: Path) -> None:
    store = ManagedControlStore(store_path)
    op, other = _many(store, 4), _many(store, 4)
    now = [0.0]
    service = DiagnosticsService(store, clock=lambda: now[0], ttl=10, capacity=2)
    cursor = service.page(op, "job", limit=2, viewer="alice").next_cursor
    assert cursor and cursor.startswith("d2.")
    for kwargs in (
        {"operation_id": other, "kind": "job", "viewer": "alice"},
        {"operation_id": op, "kind": "refresh_operation", "viewer": "alice"},
        {"operation_id": op, "kind": "job", "viewer": "mallory"},
    ):
        with pytest.raises(ControlError) as excinfo:
            service.page(cursor=cursor, limit=2, **kwargs)
        assert excinfo.value.code is E.CURSOR_INVALID
    _, snap, offset, limit, mac = cursor.split(".")
    for forged in (
        _flip(cursor, len(cursor) - 1),  # signature
        f"d2.{snap}.{int(offset) + 1}.{limit}.{mac}",  # offset
        f"d2.{snap}.{offset}.3.{mac}",  # page size
        "d1.2.2",  # previous unsigned format
        "j2." + cursor[3:],  # another list protocol
    ):
        with pytest.raises(ControlError) as excinfo:
            limit_of = int(forged.split(".")[3]) if forged.startswith("d2") else 2
            service.page(op, "job", forged, limit=limit_of, viewer="alice")
        assert excinfo.value.code is E.CURSOR_INVALID
    with pytest.raises(ControlError) as excinfo:
        DiagnosticsService(store).page(op, "job", cursor, limit=2, viewer="alice")  # restart
    assert excinfo.value.code is E.LIST_CURSOR_EXPIRED
    now[0] = 11.0
    with pytest.raises(ControlError) as excinfo:
        service.page(op, "job", cursor, limit=2, viewer="alice")  # TTL
    assert excinfo.value.code is E.LIST_CURSOR_EXPIRED
    first = service.page(op, "job", limit=1, viewer="a").next_cursor
    service.page(op, "job", limit=1, viewer="b")
    service.page(op, "job", limit=1, viewer="c")  # capacity 2: evicts the first
    with pytest.raises(ControlError) as excinfo:
        service.page(op, "job", first, limit=1, viewer="a")
    assert excinfo.value.code is E.LIST_CURSOR_EXPIRED


def test_oversized_diagnostics_are_refused(store_path: Path) -> None:
    store = ManagedControlStore(store_path)
    op = _many(store, 6)
    with pytest.raises(ControlError) as excinfo:
        DiagnosticsService(store, max_items=3).page(op, "job")
    assert excinfo.value.code is E.LIST_SNAPSHOT_TOO_LARGE


def test_http_diagnostics_snapshot_pages_and_errors(tmp_path: Path) -> None:
    app, runtime, world = build(tmp_path, FAST)
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        store: ManagedControlStore = runtime.store  # type: ignore[assignment]
        op = _many(store, 205)  # > one 200-item page
        first = c.get(f"/api/v1/jobs/{op}/diagnostics")
        assert first.status_code == 200
        body = first.json()
        assert len(body["items"]) == 200 and body["next_cursor"].startswith("d2.")
        store.set_hold(op, store.get(op).revision, held=False)
        second = c.get(f"/api/v1/jobs/{op}/diagnostics", params={"cursor": body["next_cursor"]})
        assert second.status_code == 200
        assert second.json()["revision"] == body["revision"] and len(second.json()["items"]) == 6
        assert second.json()["next_cursor"] is None
        other = c.get(
            f"/api/v1/jobs/{op}/diagnostics",
            params={"cursor": body["next_cursor"]},
            headers={SESSION_HEADER: SESSION, "Cookie": ""},
        )
        assert other.status_code == 200  # same session: allowed
        foreign = TestClient(app, base_url="http://" + AUTHORITY)
        assert foreign.get(f"/api/v1/jobs/{op}/diagnostics").status_code == 401
        evil = c.get(f"/api/v1/jobs/{op}/diagnostics", headers={"Host": "evil.example:8765"})
        assert evil.status_code == 403
        bad = c.get(f"/api/v1/jobs/{op}/diagnostics", params={"cursor": "d2.x"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "cursor_invalid"
        moved = c.get(
            f"/api/v1/setup-operations/setup_{'0' * 32}/diagnostics",
            params={"cursor": body["next_cursor"]},
        )
        assert moved.status_code == 400  # cursor of another resource/kind
        setup = start(store, world, approve(store, world, plan(store, world))).body["id"]
        assert c.get(f"/api/v1/setup-operations/{setup}/diagnostics").status_code == 200
        assert "Prompt" not in second.text and str(tmp_path) not in second.text
