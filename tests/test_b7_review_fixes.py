"""PR #32 review fixes: runtime queue ownership, same-key single-flight, snapshot paging, Win32.

Concurrency is driven by Barrier / Event and store fault hooks, never by sleeps.
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from test_b7_api import runtime_for
from test_b7_jobs import SESSION, Env, FakeRunner, dispatcher
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.job_repository import JobControlStore
from utteran_control.store import IdempotencyScope
from utteran_gui.job_admission import AdmissionError, JobAdmission
from utteran_gui.job_runtime import JobRuntime, RuntimeStateError
from utteran_gui.operation_queue import OperationQueue


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path, None)


def scope(endpoint: str, key: str) -> IdempotencyScope:
    return IdempotencyScope(endpoint, key)


# --------------------------------------------------------------------------- P2-1


def test_second_runtime_on_same_queue_is_refused(env: Env) -> None:
    queue = OperationQueue()
    first, second = runtime_for(env, queue), runtime_for(env, queue)
    first.activate()
    with pytest.raises(RuntimeStateError) as excinfo:
        second.activate()
    assert excinfo.value.code == "legacy_queue_owned" and second.state == "inactive"
    assert second.store is None and second.dispatcher is None
    # B never owned the queue, so its shutdown must not reopen it under A.
    second.shutdown()
    assert queue.closed and first.state == "active"
    first.deactivate(timeout=1)
    assert not queue.closed
    second.activate()  # after A released, B may take over
    assert queue.closed and second.state == "active"
    second.shutdown()
    assert not queue.closed


def test_queue_ownership_tokens_are_exclusive_and_non_reentrant() -> None:
    queue = OperationQueue()
    assert queue.close_for("a" * 32)
    assert not queue.close_for("a" * 32)  # no silent re-entry
    assert not queue.close_for("b" * 32)
    assert not queue.reopen("b" * 32) and queue.closed  # foreign release is ignored
    assert queue.reopen("a" * 32) and not queue.closed
    assert not queue.reopen("a" * 32)


def test_parallel_activation_admits_at_most_one_runtime(env: Env) -> None:
    queue = OperationQueue()
    runtimes = [runtime_for(env, queue) for _ in range(6)]
    barrier = threading.Barrier(len(runtimes))

    def activate(runtime: JobRuntime) -> str:
        barrier.wait()
        try:
            runtime.activate()
            return "active"
        except (RuntimeStateError, ControlError) as exc:
            return getattr(exc, "code", "error") if isinstance(exc, RuntimeStateError) else "store"

    with ThreadPoolExecutor(len(runtimes)) as pool:
        results = list(pool.map(activate, runtimes))
    assert results.count("active") == 1
    assert set(results) - {"active"} <= {"legacy_queue_owned"}
    active = [r for r in runtimes if r.state == "active"]
    assert len(active) == 1 and queue.closed
    active[0].shutdown()
    assert not queue.closed


@pytest.mark.parametrize("stage", ["store", "backup", "recover", "dispatcher"])
def test_failed_activation_releases_only_its_own_ownership(
    env: Env, monkeypatch, stage: str
) -> None:
    import utteran_gui.job_runtime as module

    queue = OperationQueue()
    runtime = runtime_for(env, queue)
    runtime.autostart_dispatcher = True

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise ControlError(ErrorCode.STORE_FULL)

    if stage == "store":
        monkeypatch.setattr(module, "JobControlStore", boom)
    elif stage == "backup":
        monkeypatch.setattr(JobControlStore, "backup", boom)
    elif stage == "recover":
        monkeypatch.setattr(JobControlStore, "recover", boom)
    else:
        monkeypatch.setattr(module.JobDispatcher, "start", boom)
    with pytest.raises(ControlError):
        runtime.activate()
    assert not queue.closed  # its own ownership was released
    assert runtime.state == "inactive" and runtime.store is None and runtime.dispatcher is None
    # A foreign owner must stay untouched by a failing runtime.
    assert queue.close_for("f" * 32)
    other = runtime_for(env, queue)
    with pytest.raises(RuntimeStateError):
        other.activate()
    assert queue.closed
    assert queue.reopen("f" * 32)


def test_refused_rollback_keeps_ownership(env: Env) -> None:
    from utteran_gui.process_verifier import current_owner

    queue = OperationQueue()
    runtime = runtime_for(env, queue)
    runtime.activate()
    store = runtime.store
    assert store is not None
    env.submit(env.file("a.wav"))
    store.set_dispatch(store.queue().revision, "running")
    item = store.queue().items[0]
    store.set_item_hold(
        item.id, store.queue().revision, False, scope("/api/v1/queue/items/x", "own-key-00000001")
    )
    job_id, revision = store.next_dispatchable()  # type: ignore[misc]
    store.begin_attempt(job_id, revision, current_owner())
    for _ in range(2):
        with pytest.raises(RuntimeStateError) as excinfo:
            runtime.deactivate(timeout=1)
        assert excinfo.value.code == "active_attempt" and queue.closed
    other = runtime_for(env, queue)
    with pytest.raises(RuntimeStateError):
        other.activate()
    assert queue.closed


# --------------------------------------------------------------------------- P2-2


def gate_commit(env: Env) -> tuple[threading.Event, threading.Event]:
    """Pause the leader inside its write transaction until released."""
    entered, release = threading.Event(), threading.Event()

    def hook(where: str) -> None:
        if where == "batch_before_replay":
            entered.set()
            assert release.wait(10)

    env.store._fault = hook
    return entered, release


def counts(env: Env) -> tuple[int, int, int]:
    return env.count("operations"), env.count("outbox"), env.count("batch_replays")


def test_same_key_followers_wait_for_the_leader_and_replay(env: Env) -> None:
    body = env.submit_body(env.session(env.file("a.wav"), env.file("b.wav", b"b")))
    target = scope("/api/v1/jobs", "flight-key-000001")
    entered, release = gate_commit(env)
    barrier = threading.Barrier(6)
    results: list[Any] = [None] * 6

    def send(index: int) -> None:
        barrier.wait()
        try:
            results[index] = env.admission.submit(target, body, session=SESSION)
        except AdmissionError as exc:
            results[index] = exc.code

    threads = [threading.Thread(target=send, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    assert entered.wait(10)
    release.set()
    for thread in threads:
        thread.join(15)
    env.store._fault = None
    assert not any(isinstance(r, str) for r in results), results  # no resolution_consumed
    first = results[0]
    for r in results:
        assert r.batch.to_wire() == first.batch.to_wire()
        assert dict(r.headers) == dict(first.headers) and r.status == 202
    assert sum(1 for r in results if not r.replayed) == 1
    assert counts(env) == (2, 3, 1)  # 2 jobs, 2 state + 1 queue event, 1 replay


def test_follower_times_out_with_retryable_503_then_replays(env: Env) -> None:
    body = env.submit_body(env.session(env.file("a.wav")))
    target = scope("/api/v1/jobs", "flight-key-000002")
    env.admission.inflight_wait = 0.2
    entered, release = gate_commit(env)
    leader: list[Any] = []
    thread = threading.Thread(
        target=lambda: leader.append(env.admission.submit(target, body, session=SESSION))
    )
    thread.start()
    assert entered.wait(10)
    with pytest.raises(AdmissionError) as excinfo:
        env.admission.submit(target, body, session=SESSION)
    assert excinfo.value.code == "submission_in_progress" and excinfo.value.status == 503
    assert counts(env) == (0, 0, 0)  # client timeout is not a rollback, nor a commit
    release.set()
    thread.join(10)
    env.store._fault = None
    again = env.admission.submit(target, body, session=SESSION)
    assert again.replayed and again.batch.to_wire() == leader[0].batch.to_wire()
    assert counts(env) == (1, 2, 1)


def test_leader_rollback_lets_a_follower_commit_once(env: Env) -> None:
    body = env.submit_body(env.session(env.file("a.wav")))
    target = scope("/api/v1/jobs", "flight-key-000003")
    entered, release = threading.Event(), threading.Event()
    calls = {"n": 0}

    def hook(where: str) -> None:
        if where == "batch_before_replay":
            calls["n"] += 1
            if calls["n"] == 1:
                entered.set()
                assert release.wait(10)
                raise ControlError(ErrorCode.STORE_FULL)  # leader rolls back

    env.store._fault = hook
    outcome: dict[str, Any] = {}

    def lead() -> None:
        try:
            env.admission.submit(target, body, session=SESSION)
        except ControlError as exc:
            outcome["leader"] = exc.code.value

    def follow() -> None:
        outcome["follower"] = env.admission.submit(target, body, session=SESSION)

    t1 = threading.Thread(target=lead)
    t1.start()
    assert entered.wait(10)
    t2 = threading.Thread(target=follow)
    t2.start()
    release.set()
    t1.join(10)
    t2.join(10)
    env.store._fault = None
    assert outcome["leader"] == "store_full"
    assert not outcome["follower"].replayed and len(outcome["follower"].batch.jobs) == 1
    assert counts(env) == (1, 2, 1)


def test_same_key_different_body_in_flight_is_409(env: Env) -> None:
    body = env.submit_body(env.session(env.file("a.wav")))
    other = dict(body, resume_mode="fresh")
    target = scope("/api/v1/jobs", "flight-key-000004")
    entered, release = gate_commit(env)
    thread = threading.Thread(target=lambda: env.admission.submit(target, body, session=SESSION))
    thread.start()
    assert entered.wait(10)
    with pytest.raises(AdmissionError) as excinfo:
        env.admission.submit(target, other, session=SESSION)
    assert excinfo.value.code == "idempotency_conflict" and excinfo.value.status == 409
    release.set()
    thread.join(10)
    env.store._fault = None


def test_token_reuse_under_another_key_and_session_stays_refused(env: Env) -> None:
    session = env.session(env.file("a.wav"))
    body = env.submit_body(session)
    entered, release = gate_commit(env)
    thread = threading.Thread(
        target=lambda: env.admission.submit(
            scope("/api/v1/jobs", "flight-key-000005"), body, session=SESSION
        )
    )
    thread.start()
    assert entered.wait(10)
    with pytest.raises(AdmissionError) as excinfo:  # same token, other key, while in flight
        env.admission.submit(scope("/api/v1/jobs", "flight-key-000006"), body, session=SESSION)
    assert excinfo.value.code == "resolution_consumed"
    release.set()
    thread.join(10)
    env.store._fault = None
    with pytest.raises(AdmissionError) as excinfo:  # and after the commit
        env.admission.submit(scope("/api/v1/jobs", "flight-key-000007"), body, session=SESSION)
    assert excinfo.value.code == "resolution_consumed"
    fresh = env.submit_body(env.session(env.file("b.wav", b"b")))
    with pytest.raises(AdmissionError) as excinfo:
        env.admission.submit(scope("/api/v1/jobs", "flight-key-000008"), fresh, session="other")
    assert excinfo.value.code == "resolution_invalid"


def test_shared_token_registry_across_store_connections_never_409s(env: Env) -> None:
    """Separate admission instances (own connections) sharing one token registry."""
    body = env.submit_body(env.session(env.file("a.wav")))
    target = scope("/api/v1/jobs", "flight-key-000009")
    entered, release = gate_commit(env)
    others = [
        JobAdmission(
            store=JobControlStore(env.store.path, timeout=10),
            settings_path=env.settings_path,
            provider=env.provider,
            tokens=env.admission.tokens,
            inflight_wait=10,
        )
        for _ in range(3)
    ]
    leader: list[Any] = []
    thread = threading.Thread(
        target=lambda: leader.append(env.admission.submit(target, body, session=SESSION))
    )
    thread.start()
    assert entered.wait(10)
    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(o.submit, target, body, session=SESSION) for o in others]
        release.set()
        results = [f.result(15) for f in futures]
    thread.join(10)
    env.store._fault = None
    assert all(r.replayed and r.batch.to_wire() == leader[0].batch.to_wire() for r in results)
    assert counts(env) == (1, 2, 1)


def test_rerun_same_key_is_single_flight(env: Env) -> None:
    path = env.file("a.wav")
    job = env.submit(path).batch.jobs[0]
    dispatcher(env, FakeRunner(env)).run_once()
    done = env.store.get_job(job.id)
    body = {"schema_version": 1, "submission": env.submit_body(env.session(path))}
    target = scope(f"/api/v1/jobs/{job.id}/rerun", "rerun-key-0000001")
    entered, release = gate_commit(env)
    barrier = threading.Barrier(4)
    results: list[Any] = [None] * 4

    def send(index: int) -> None:
        barrier.wait()
        try:
            results[index] = env.admission.rerun(
                job.id, done.revision, target, body, session=SESSION
            )
        except AdmissionError as exc:
            results[index] = exc.code

    threads = [threading.Thread(target=send, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    assert entered.wait(10)
    release.set()
    for t in threads:
        t.join(15)
    env.store._fault = None
    assert not any(isinstance(r, str) for r in results), results
    ids = {r.batch.jobs[0].id for r in results}
    assert len(ids) == 1 and results[0].batch.jobs[0].rerun_of == job.id
    assert env.store.get_job(job.id).status == "succeeded"
    assert env.count("operations") == 2


# --------------------------------------------------------------------------- P2-3


def advance_states(env: Env, jobs: list[Any]) -> None:
    """queued -> running -> succeeded for the first job, a new job is added."""
    dispatcher(env, FakeRunner(env)).run_once()
    env.submit(env.file("late.wav", b"late"))


def test_list_pages_are_a_single_point_in_time_snapshot(env: Env) -> None:
    jobs = list(env.submit(*(env.file(f"s{i}.wav", bytes([i + 1])) for i in range(3))).batch.jobs)
    first = env.store.list_jobs(limit=1, scope="viewer")
    assert [j.status for j in first.items] == ["queued"]
    advance_states(env, jobs)
    assert env.store.get_job(jobs[0].id).status == "succeeded"  # latest view moved on
    second = env.store.list_jobs(first.next_cursor, limit=1, scope="viewer")
    third = env.store.list_jobs(second.next_cursor, limit=1, scope="viewer")
    pages = [*first.items, *second.items, *third.items]
    assert [j.id for j in pages] == [j.id for j in jobs] and third.next_cursor is None
    assert all(j.status == "queued" and j.attempts == () for j in pages)  # frozen DTOs
    assert first.revision == second.revision == third.revision
    fresh = env.store.list_jobs(limit=10, scope="viewer")
    assert len(fresh.items) == 4 and fresh.revision > first.revision
    assert fresh.items[0].status == "succeeded"


def test_list_cursor_is_bound_to_snapshot_scope_and_page_size(env: Env) -> None:
    env.submit(*(env.file(f"c{i}.wav", bytes([i + 1])) for i in range(3)))
    first = env.store.list_jobs(limit=1, scope="viewer")
    cursor = first.next_cursor
    assert cursor is not None
    for bad, kwargs in (
        (cursor[:-1] + ("0" if cursor[-1] != "0" else "1"), {"scope": "viewer", "limit": 1}),
        (cursor, {"scope": "other-session", "limit": 1}),
        (cursor, {"scope": "viewer", "limit": 2}),
        ("j1.1." + "0" * 32, {"scope": "viewer", "limit": 1}),
    ):
        with pytest.raises(ControlError) as excinfo:
            env.store.list_jobs(bad, **kwargs)
        assert excinfo.value.code.value == "cursor_invalid"


def test_list_snapshot_expiry_capacity_and_restart(env: Env) -> None:
    clock = {"now": 0.0}
    store = JobControlStore(
        env.store.path, list_clock=lambda: clock["now"], list_ttl=60, list_capacity=2
    )
    env.submit(*(env.file(f"e{i}.wav", bytes([i + 1])) for i in range(2)))
    first = store.list_jobs(limit=1, scope="v")
    clock["now"] = 61
    with pytest.raises(ControlError) as excinfo:
        store.list_jobs(first.next_cursor, limit=1, scope="v")
    assert excinfo.value.code.value == "cursor_expired" and excinfo.value.status == 409
    assert store.list_jobs(limit=1, scope="v").items  # a new snapshot works
    # Capacity: the oldest snapshot is evicted explicitly (its cursor -> cursor_expired).
    a = store.list_jobs(limit=1, scope="v")
    store.list_jobs(limit=1, scope="v")
    store.list_jobs(limit=1, scope="v")
    with pytest.raises(ControlError) as excinfo:
        store.list_jobs(a.next_cursor, limit=1, scope="v")
    assert excinfo.value.code.value == "cursor_expired"
    live = store.list_jobs(limit=1, scope="v")
    restarted = JobControlStore(env.store.path)
    with pytest.raises(ControlError) as excinfo:
        restarted.list_jobs(live.next_cursor, limit=1, scope="v")
    assert excinfo.value.code.value == "cursor_expired"


def test_list_snapshot_size_limit_is_explicit(env: Env) -> None:
    store = JobControlStore(env.store.path, list_max_items=2)
    env.submit(*(env.file(f"z{i}.wav", bytes([i + 1])) for i in range(3)))
    with pytest.raises(ControlError) as excinfo:
        store.list_jobs(limit=1, scope="v")
    assert excinfo.value.code.value == "list_snapshot_too_large" and excinfo.value.status == 503


def test_list_cursor_and_pages_carry_no_private_values(env: Env) -> None:
    from test_b7_jobs import SENTINEL

    env.submit(*(env.file(f"p{i}.wav", bytes([i + 1])) for i in range(2)))
    first = env.store.list_jobs(limit=1, scope="v")
    text = (first.next_cursor or "") + first.model_dump_json()
    for secret in (SENTINEL, str(env.tmp), "p0.wav"):
        assert secret not in text


# --------------------------------------------------------------------------- P3


class FakeWin32:
    """Records the handle values passed through the observation logic."""

    def __init__(
        self, *, open_error: int | None = None, times: bool = True, code: int | None = 259
    ) -> None:
        self.handle = 0x7FFF_FFFF_FFF0  # wider than 32 bits: must pass through unchanged
        self.open_error = open_error
        self.times_ok = times
        self.code = code
        self.seen: list[int] = []
        self.closed: list[int] = []

    def open(self, pid: int) -> tuple[int | None, int]:
        return (None, self.open_error) if self.open_error is not None else (self.handle, 0)

    def creation_time(self, handle: int) -> int | None:
        self.seen.append(handle)
        return 1234 if self.times_ok else None

    def exit_code(self, handle: int) -> int | None:
        self.seen.append(handle)
        return self.code

    def close(self, handle: int) -> bool:
        self.closed.append(handle)
        return False  # a failing CloseHandle does not change the observation


def test_win32_observation_logic_with_64bit_handles() -> None:
    from utteran_control.recovery import Liveness
    from utteran_gui.process_verifier import observe_windows

    api = FakeWin32()
    assert observe_windows(api, 42) == ("1234", True)
    assert api.seen == [api.handle, api.handle] and api.closed == [api.handle]
    assert observe_windows(FakeWin32(code=0), 42) == ("1234", False)
    assert observe_windows(FakeWin32(open_error=87), 42) is None  # PID provably absent
    assert observe_windows(FakeWin32(open_error=5), 42) is Liveness.UNKNOWN  # access denied
    failing = FakeWin32(times=False)
    assert observe_windows(failing, 42) is Liveness.UNKNOWN and failing.closed == [failing.handle]
    assert observe_windows(FakeWin32(code=None), 42) is Liveness.UNKNOWN


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 API declarations")
def test_win32_declarations_and_real_processes() -> None:  # pragma: no cover - Windows CI
    import ctypes
    import os
    import subprocess
    from ctypes import wintypes

    import utteran_gui.process_verifier as module
    from utteran_contracts.control import ProcessIdentity
    from utteran_control.recovery import Liveness

    filetime = ctypes.POINTER(wintypes.FILETIME)
    assert tuple(module._GetProcessTimes.argtypes) == (wintypes.HANDLE, *([filetime] * 4))
    assert module._GetProcessTimes.restype is wintypes.BOOL
    assert tuple(module._GetExitCodeProcess.argtypes) == (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    assert module._GetExitCodeProcess.restype is wintypes.BOOL
    assert tuple(module._CloseHandle.argtypes) == (wintypes.HANDLE,)
    assert module._CloseHandle.restype is wintypes.BOOL
    assert tuple(module._OpenProcess.argtypes) == (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    assert module._OpenProcess.restype is wintypes.HANDLE
    verifier = module.OsProcessVerifier()
    me = module.start_identity(os.getpid())
    assert (
        me and verifier.check(ProcessIdentity(pid=os.getpid(), start_identity=me)) is Liveness.ALIVE
    )
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    identity = module.start_identity(child.pid)
    child.wait()
    if identity is not None:
        assert verifier.check(ProcessIdentity(pid=child.pid, start_identity=identity)) in {
            Liveness.GONE,
            Liveness.PID_REUSED,
        }
    assert verifier.check(ProcessIdentity(pid=2**31 - 1, start_identity="win:1")) in {
        Liveness.GONE,
        Liveness.UNKNOWN,
    }
    assert (
        verifier.check(ProcessIdentity(pid=os.getpid(), start_identity="win:1"))
        is Liveness.PID_REUSED
    )
