"""B6 durable control store: no models, GPU, network or real child processes."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from utteran_contracts.control import (
    PUBLIC_MODELS,
    EventEnvelope,
    OperationStatus,
    OperationView,
    OwnerIdentity,
    ProcessIdentity,
    QueueView,
    SubmitOperation,
    TransitionRequest,
)
from utteran_contracts.control_schema import schema_bytes
from utteran_control import transitions
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.recovery import Liveness, Outcome, UnknownVerifier, decide
from utteran_control.store import AcceptedQueue, ControlStore, IdempotencyScope
from utteran_paths import DataPaths

ROOT = Path(__file__).parents[1]
SENTINEL = "B6_PRIVATE_SENTINEL_プロンプト_C:/Users/synthetic/audio.wav"
NONCE = "f" * 40
E = ErrorCode


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 2, 3, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)
        return self.now


class Fixed:
    def __init__(self, owner: Liveness, child: Liveness | None = None) -> None:
        self.owner, self.child = owner, child
        self.seen: list[ProcessIdentity] = []

    def check(self, process: ProcessIdentity) -> Liveness:
        self.seen.append(process)
        if process.pid == 4242:
            return self.child or self.owner
        return self.owner


def body(kind: str = "job", payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": kind,
        "private_payload": payload if payload is not None else {"prompt": SENTINEL},
    }


def owner(number: int = 1, nonce: str = NONCE) -> OwnerIdentity:
    return OwnerIdentity(
        owner_id="own_" + f"{number:032x}",
        nonce=nonce,
        process=ProcessIdentity(pid=1000 + number, start_identity=f"boot-{number}"),
    )


CHILD = ProcessIdentity(pid=4242, start_identity="child-start")
_keys = iter(range(10**6))


def key() -> str:
    return f"synthetic-key-{next(_keys):08d}"


def submit(store: ControlStore, kind: str = "job", k: str | None = None) -> OperationView:
    endpoint = {"job": "/api/v1/jobs", "setup_operation": "/api/v1/setup-operations"}.get(
        kind, "/api/v1/environment-refreshes"
    )
    return store.submit(endpoint, k or key(), body(kind)).operation


def move(store: ControlStore, op: OperationView, to: str, reason: str, **kw: Any) -> OperationView:
    request = {
        "schema_version": 1,
        "expected_revision": op.revision,
        "to_status": to,
        "reason_code": reason,
    }
    return store.transition(op.id, request, **kw).operation


def running(store: ControlStore, kind: str = "job", launch: bool = True) -> tuple[Any, int]:
    op = submit(store, kind)
    grant = store.begin_attempt(op.id, op.revision, owner())
    current = grant.operation
    if launch:
        current = store.record_launch(op.id, grant.fencing_token, outcome="confirmed", child=CHILD)
    return current, grant.fencing_token


def table_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: db.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in names}


def code(excinfo: pytest.ExceptionInfo[ControlError]) -> ErrorCode:
    return excinfo.value.code


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "private" / "control-v1" / "operations.sqlite3"


@pytest.fixture
def store(path: Path) -> ControlStore:
    return ControlStore(path, clock=Clock())


# ----------------------------------------------------------------- schema / DTO


def test_schema_is_deterministic_checked_and_validates_store_output(store):
    first = schema_bytes()
    assert first == schema_bytes()
    assert (ROOT / "docs/architecture/b6/control.schema.json").read_bytes() == first
    schema = json.loads(first)
    jsonschema.Draft202012Validator.check_schema(schema)
    op = submit(store)
    for name, value in (
        ("OperationView", op.to_wire()),
        ("ControlSnapshot", store.snapshot().to_wire()),
        ("EventPage", store.read_events().to_wire()),
        ("QueueView", store.queue().to_wire()),
    ):
        jsonschema.Draft202012Validator({**schema, "$ref": f"#/$defs/{name}"}).validate(value)
    text = first.decode()
    for private in ("nonce", "fencing_token", "owner_pid", "child_pid", "PrivateOperation"):
        assert private not in text
    # Only the request DTO names the private payload; no public view carries it.
    assert set(schema["$defs"]["SubmitOperation"]["properties"]) == {
        "schema_version",
        "kind",
        "private_payload",
    }
    for name in ("OperationView", "EventEnvelope", "EventPayload", "ControlSnapshot"):
        assert "private_payload" not in json.dumps(schema["$defs"][name])


def test_examples_validate_against_schema():
    schema = json.loads(schema_bytes())
    examples = json.loads((ROOT / "docs/architecture/b6/examples.json").read_text("utf-8"))
    for name, value in examples.items():
        if name.startswith("_"):
            continue
        definition = name.split("_")[0]
        jsonschema.Draft202012Validator({**schema, "$ref": f"#/$defs/{definition}"}).validate(value)
    assert "nonce" not in json.dumps(examples) and "fencing" not in json.dumps(examples)


def test_schema_cli_check(tmp_path):
    target = tmp_path / "control.schema.json"
    run = [sys.executable, "-m", "utteran_contracts.control_schema", "--output", str(target)]
    assert subprocess.run([*run, "--check"], capture_output=True).returncode == 1
    subprocess.run(run, check=True)
    assert subprocess.run([*run, "--check"], capture_output=True).returncode == 0


def test_dto_strictness():
    valid = body()
    SubmitOperation.model_validate(valid)
    for broken in (
        {**valid, "extra": 1},
        {**valid, "schema_version": 2},
        {**valid, "schema_version": "1"},
        {**valid, "kind": "download"},
        {**valid, "private_payload": []},
    ):
        with pytest.raises(ValidationError):
            SubmitOperation.model_validate(broken)
    request = {"schema_version": 1, "expected_revision": 1, "to_status": "held"}
    with pytest.raises(ValidationError):
        TransitionRequest.model_validate({**request, "reason_code": "free text"})
    with pytest.raises(ValidationError):
        TransitionRequest.model_validate({**request, "reason_code": "user_hold", "x": 1})
    with pytest.raises(ValidationError):
        TransitionRequest.model_validate({**request, "reason_code": "user_hold", "to_status": "x"})
    with pytest.raises(ValidationError):
        OwnerIdentity(
            owner_id="own_1", nonce=NONCE, process=ProcessIdentity(pid=1, start_identity=None)
        )
    with pytest.raises(ValidationError):
        owner(nonce="short")
    event = {
        "schema_version": 1,
        "id": "0" * 32 + ":1",
        "resource_id": "queue",
        "resource_kind": "queue",
        "resource_revision": 1,
        "type": "queue_changed",
        "payload": {"status": "paused", "reason_code": None, "attempt_id": None},
    }
    EventEnvelope.model_validate({**event, "timestamp": "2026-11-02T00:00:00Z"})
    for timestamp in ("2026-11-02T00:00:00", "2026-11-02T09:00:00+09:00"):
        with pytest.raises(ValidationError):
            EventEnvelope.model_validate({**event, "timestamp": timestamp})
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(
            {**event, "timestamp": "2026-11-02T00:00:00Z", "payload": {"prompt": SENTINEL}}
        )
    assert SENTINEL not in repr(SubmitOperation.model_validate(valid))


def test_naive_clock_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        ControlStore(tmp_path / "c" / "o.sqlite3", clock=lambda: datetime(2026, 1, 1))


# ----------------------------------------------------------------- durability


def test_reopen_preserves_state_and_revisions_are_immutable(store, path):
    op = submit(store)
    held = move(store, op, "held", "user_hold")
    reopened = ControlStore(path)
    assert reopened.get(op.id) == held
    assert reopened.read_events().events == store.read_events().events
    with sqlite3.connect(path) as db:
        rows = db.execute(
            "SELECT revision, status FROM operation_history WHERE operation_id=? ORDER BY 1",
            (op.id,),
        ).fetchall()
        assert rows == [(1, "queued"), (2, "held")]
        for statement in (
            "UPDATE operation_history SET status='succeeded'",
            "DELETE FROM operation_history",
            "UPDATE outbox SET status='succeeded'",
            "DELETE FROM outbox",
            "DELETE FROM replays",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)
    with pytest.raises(ControlError) as missing:
        store.get("job_" + "0" * 32)
    assert code(missing) is E.RESOURCE_NOT_FOUND and missing.value.status == 404


@pytest.mark.parametrize(
    ("prepare", "expected"),
    [
        (lambda p: p.write_bytes(b""), E.STORE_SCHEMA_UNSUPPORTED),
        (lambda p: p.write_bytes(b"not a database" * 100), E.STORE_CORRUPT),
    ],
)
def test_existing_foreign_or_corrupt_files_are_never_reinitialized(tmp_path, prepare, expected):
    path = tmp_path / "d" / "operations.sqlite3"
    path.parent.mkdir()
    prepare(path)
    before = path.read_bytes()
    with pytest.raises(ControlError) as excinfo:
        ControlStore(path)
    assert code(excinfo) is expected
    assert path.read_bytes() == before
    assert os.listdir(path.parent) == ["operations.sqlite3"]


def test_unknown_version_and_missing_tables_are_rejected_without_change(store, path):
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=3")
    before = path.read_bytes()
    with pytest.raises(ControlError) as excinfo:
        ControlStore(path)
    assert code(excinfo) is E.STORE_SCHEMA_UNSUPPORTED and path.read_bytes() == before
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=1")
        db.execute("DROP TABLE leases")
    with pytest.raises(ControlError) as excinfo:
        ControlStore(path)
    assert code(excinfo) is E.STORE_CORRUPT


def test_open_existing_does_not_create(tmp_path):
    path = tmp_path / "absent" / "operations.sqlite3"
    with pytest.raises(ControlError) as excinfo:
        ControlStore(path, create=False)
    assert code(excinfo) is E.STORE_UNAVAILABLE and not path.parent.exists()
    path.parent.mkdir()
    with pytest.raises(ControlError):
        ControlStore(path, create=False)
    assert not path.exists()


def test_store_deleted_while_open_is_not_recreated(store, path):
    path.unlink()
    with pytest.raises(ControlError) as excinfo:
        submit(store)
    assert code(excinfo) is E.STORE_UNAVAILABLE and not path.exists()


@pytest.mark.parametrize(
    "candidate",
    [Path("relative/operations.sqlite3"), Path("/tmp/x/../operations.sqlite3"), Path("/tmp/a.db")],
)
def test_unsafe_locations_are_rejected(candidate):
    with pytest.raises(ControlError) as excinfo:
        ControlStore(candidate)
    assert code(excinfo) is E.STORE_UNSAFE_PATH


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes/symlinks; Windows ACL unverified")
def test_posix_permissions_and_symlinks(store, path, tmp_path):
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ControlError) as excinfo:
        ControlStore(link / "operations.sqlite3")
    assert code(excinfo) is E.STORE_UNSAFE_PATH and not any(target.iterdir())
    file_link = tmp_path / "dir2"
    file_link.mkdir(mode=0o700)
    (file_link / "operations.sqlite3").symlink_to(path)
    with pytest.raises(ControlError) as excinfo:
        ControlStore(file_link / "operations.sqlite3")
    assert code(excinfo) is E.STORE_UNSAFE_PATH


def test_concurrent_first_creation_yields_one_installation(path):
    results: list[Any] = []
    barrier = threading.Barrier(8)

    def create() -> None:
        barrier.wait()
        try:
            results.append(ControlStore(path).snapshot().event_cursor)
        except ControlError as exc:  # pragma: no cover - must not happen
            results.append(exc.code)

    threads = [threading.Thread(target=create) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(results)) == 1 and isinstance(results[0], str)
    assert os.listdir(path.parent) == ["operations.sqlite3"]


def test_control_store_path_is_separate_from_b5(tmp_path, monkeypatch):
    paths = DataPaths(root=tmp_path, project_root=tmp_path)
    assert paths.control_store == tmp_path / "data" / "control-v1" / "operations.sqlite3"
    assert paths.control_store.parent != paths.settings_resources.parent
    default = DataPaths(root=None, project_root=tmp_path)
    assert default.control_store.parts[-2:] == ("control-v1", "operations.sqlite3")
    assert default.control_store.parent.parent == default.settings_resources.parent.parent
    assert not paths.control_store.exists()  # resolving a path performs no I/O


# ----------------------------------------------------------------- atomicity


def full(point: str) -> Any:
    def hook(where: str) -> None:
        if where == point:
            error = sqlite3.OperationalError("disk full")
            error.sqlite_errorcode = sqlite3.SQLITE_FULL  # type: ignore[attr-defined]
            raise error

    return hook


def test_failure_before_commit_leaves_nothing(path):
    ControlStore(path)
    before = table_counts(path)
    failing = ControlStore(path, _fault=full("before_commit"))
    with pytest.raises(ControlError) as excinfo:
        failing.submit("/api/v1/jobs", "synthetic-key-full-1", body())
    assert code(excinfo) is E.STORE_FULL and excinfo.value.status == 503
    assert table_counts(path) == before
    created = ControlStore(path).submit("/api/v1/jobs", "synthetic-key-full-1", body())
    assert created.status == 201 and not created.replayed


def test_response_lost_after_commit_replays_same_result(path):
    lost = ControlStore(path, _fault=full("after_commit"))
    with pytest.raises(ControlError):
        lost.submit("/api/v1/jobs", "synthetic-key-lost-1", body())
    after = table_counts(path)
    assert after["operations"] == 1 and after["replays"] == 1
    replay = ControlStore(path).submit("/api/v1/jobs", "synthetic-key-lost-1", body())
    assert replay.replayed and replay.status == 201
    assert table_counts(path) == after  # no second resource, event or replay
    assert replay.headers["Location"].endswith(replay.operation.id)
    assert ControlStore(path).get(replay.operation.id).revision == 1


def test_real_sqlite_full_rolls_back(store, path):
    submit(store)
    before = table_counts(path)

    def limit(db: sqlite3.Connection) -> None:
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        db.execute(f"PRAGMA max_page_count={pages}")

    limited = ControlStore(path, _connect_hook=limit)
    with pytest.raises(ControlError) as excinfo:
        limited.submit("/api/v1/jobs", key(), body(payload={"blob": "x" * 200_000}))
    assert code(excinfo) is E.STORE_FULL
    assert table_counts(path) == before


def test_busy_and_readonly_are_fixed_codes(store, path):
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(ControlError) as excinfo:
            ControlStore(path, timeout=0.05).submit("/api/v1/jobs", key(), body())
        assert code(excinfo) is E.STORE_BUSY
    finally:
        blocker.rollback()
        blocker.close()
    readonly = ControlStore(path, _connect_hook=lambda db: db.execute("PRAGMA query_only=ON"))
    with pytest.raises(ControlError) as excinfo:
        readonly.submit("/api/v1/jobs", key(), body())
    assert code(excinfo) is E.STORE_READONLY
    assert table_counts(path)["operations"] == 0


CRASH = """
import os, sys
from pathlib import Path
from utteran_control.store import ControlStore
point = sys.argv[2]
def hook(where):
    if where == point:
        os._exit(73)
store = ControlStore(Path(sys.argv[1]), _fault=hook)
store.submit('/api/v1/jobs', 'synthetic-crash-key-1', {
    'schema_version': 1, 'kind': 'job', 'private_payload': {'prompt': 'x'}})
"""


@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
def test_process_crash_around_commit(path, point):
    ControlStore(path)
    child = subprocess.run(
        [sys.executable, "-c", CRASH, str(path), point], capture_output=True, text=True
    )
    assert child.returncode == 73, child.stderr
    restarted = ControlStore(path)
    counts = table_counts(path)
    if point == "before_commit":
        assert counts["operations"] == counts["outbox"] == counts["replays"] == 0
    else:
        assert counts["operations"] == counts["replays"] == 1 and counts["outbox"] == 2
    result = restarted.submit(
        "/api/v1/jobs",
        "synthetic-crash-key-1",
        {"schema_version": 1, "kind": "job", "private_payload": {"prompt": "x"}},
    )
    assert result.replayed is (point == "after_commit")
    assert table_counts(path)["operations"] == 1 and table_counts(path)["outbox"] == 2


# ----------------------------------------------------------------- idempotency / CAS


def test_idempotency_scope_and_validation(store):
    first = store.submit("/api/v1/jobs", "synthetic-key-idem-1", body())
    reordered = {"private_payload": {"prompt": SENTINEL}, "kind": "job", "schema_version": 1}
    again = store.submit("/api/v1/jobs", "synthetic-key-idem-1", reordered)
    assert again.replayed and again.operation == first.operation
    assert dict(again.headers) == dict(first.headers) and again.status == 201
    with pytest.raises(ControlError) as excinfo:
        store.submit("/api/v1/jobs", "synthetic-key-idem-1", body(payload={"prompt": "other"}))
    assert code(excinfo) is E.IDEMPOTENCY_CONFLICT and excinfo.value.status == 409
    # Replay comparison precedes DTO validation: an invalid body still conflicts.
    with pytest.raises(ControlError) as excinfo:
        store.submit("/api/v1/jobs", "synthetic-key-idem-1", {"schema_version": 99})
    assert code(excinfo) is E.IDEMPOTENCY_CONFLICT
    with pytest.raises(ControlError) as excinfo:
        store.submit("/api/v1/jobs", "synthetic-key-idem-2", {"schema_version": 99})
    assert code(excinfo) is E.REQUEST_INVALID
    # A different endpoint is a different scope.
    other = store.submit("/api/v1/setup-operations", "synthetic-key-idem-1", body())
    assert not other.replayed and other.operation.id != first.operation.id
    for bad_key in ("short", "x" * 129, "has space in it!!", "ключ-ключ-ключ-ключ"):
        with pytest.raises(ControlError) as excinfo:
            store.submit("/api/v1/jobs", bad_key, body())
        assert code(excinfo) is E.IDEMPOTENCY_KEY_INVALID
    for endpoint in ("/jobs", "/api/v1/", "/api/v1/Jobs", "/api/v1/jobs?x=1", "/api/v1/../x"):
        with pytest.raises(ControlError) as excinfo:
            store.submit(endpoint, key(), body())
        assert code(excinfo) is E.ENDPOINT_INVALID
    with pytest.raises(ControlError) as excinfo:
        store.submit("/api/v1/jobs", key(), {"x": float("nan")})
    assert code(excinfo) is E.REQUEST_INVALID
    with pytest.raises(ControlError) as excinfo:
        store.submit("/api/v1/jobs", key(), body(payload={"blob": "x" * 300_000}))
    assert code(excinfo) is E.PAYLOAD_TOO_LARGE


def test_digest_is_hmac_not_plain_body_hash(store, path):
    import hashlib

    store.submit("/api/v1/jobs", "synthetic-key-hmac-1", body())
    with sqlite3.connect(path) as db:
        digest, stored = db.execute("SELECT digest, body FROM replays").fetchone()
    plain = json.dumps(body(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert digest != hashlib.sha256(plain.encode()).hexdigest()
    assert SENTINEL not in stored and "private_payload" not in stored


def test_transition_replay_precedes_cas(store):
    op = submit(store)
    scope = IdempotencyScope("/api/v1/jobs/" + op.id[4:] + "/hold", "synthetic-key-hold-1")
    request = {
        "schema_version": 1,
        "expected_revision": 1,
        "to_status": "held",
        "reason_code": "user_hold",
    }
    first = store.transition(op.id, request, idempotency=scope)
    again = store.transition(op.id, request, idempotency=scope)  # stale revision, but replay
    assert again.replayed and again.operation == first.operation
    with pytest.raises(ControlError) as excinfo:
        store.transition(op.id, request)
    assert code(excinfo) is E.REVISION_CONFLICT and excinfo.value.status == 412


def test_two_threads_cas_exactly_one_wins(store):
    op = submit(store)
    outcomes: list[Any] = []
    barrier = threading.Barrier(2)

    def attempt(reason: str, to: str) -> None:
        barrier.wait()
        try:
            outcomes.append(move(store, op, to, reason).status)
        except ControlError as exc:
            outcomes.append(exc.code)

    threads = [
        threading.Thread(target=attempt, args=("user_hold", "held")),
        threading.Thread(target=attempt, args=("user_cancel", "cancelled")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(outcomes) == 2 and outcomes.count(E.REVISION_CONFLICT) == 1
    assert store.get(op.id).revision == 2


CAS = """
import sys, json
from pathlib import Path
from utteran_control.store import ControlStore
from utteran_control.errors import ControlError
store = ControlStore(Path(sys.argv[1]), timeout=10)
request = {'schema_version': 1, 'expected_revision': 1, 'to_status': sys.argv[3],
           'reason_code': sys.argv[4]}
sys.stdin.read()
try:
    print(store.transition(sys.argv[2], request).operation.status)
except ControlError as exc:
    print(exc.code.value)
"""


def test_two_processes_cas_exactly_one_wins(store, path):
    op = submit(store)
    children = [
        subprocess.Popen(
            [sys.executable, "-c", CAS, str(path), op.id, to, reason],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        for to, reason in (("held", "user_hold"), ("cancelled", "user_cancel"))
    ]
    outputs = sorted(child.communicate("go")[0].strip() for child in children)
    assert E.REVISION_CONFLICT.value in outputs
    assert len([o for o in outputs if o in ("held", "cancelled")]) == 1
    assert store.get(op.id).revision == 2


def test_same_key_concurrent_threads_create_one_resource(store, path):
    results: list[Any] = []
    barrier = threading.Barrier(6)

    def send() -> None:
        barrier.wait()
        results.append(store.submit("/api/v1/jobs", "synthetic-key-race-1", body()))

    threads = [threading.Thread(target=send) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({r.operation.id for r in results}) == 1
    assert sum(not r.replayed for r in results) == 1
    assert table_counts(path)["operations"] == 1


# ----------------------------------------------------------------- transitions / queue


def test_transition_tables_never_leave_terminal_or_enter_running():
    for kind, table in transitions.TRANSITIONS.items():
        for status in transitions.TERMINAL:
            assert not transitions.allowed(kind, status), (kind, status)
        for source, targets in table.items():
            if OperationStatus.RUNNING in targets:
                assert source is OperationStatus.QUEUED
    assert not transitions.allowed("job", OperationStatus.INTERRUPTED)


def test_job_flags_hold_block_and_cancel(store):
    op = submit(store)
    with pytest.raises(ControlError) as excinfo:
        move(store, op, "running", "user_release")
    assert code(excinfo) is E.INVALID_TRANSITION
    with pytest.raises(ControlError) as excinfo:
        move(store, op, "held", "owner_lost")  # reserved for the store itself
    assert code(excinfo) is E.REQUEST_INVALID
    blocked = move(store, op, "blocked", "prestart_revalidation_failed")
    assert blocked.blocked and blocked.blockers == ("prestart_revalidation_failed",)
    held = store.set_hold(op.id, blocked.revision, held=True).operation
    assert held.status == "blocked" and held.held and held.blocked
    with pytest.raises(ControlError) as excinfo:
        move(store, held, "queued", "resources_available")  # unblock is not a release
    assert code(excinfo) is E.INVALID_TRANSITION
    unblocked = move(store, held, "held", "resources_available")
    assert unblocked.status == "held" and unblocked.held and not unblocked.blocked
    released = store.set_hold(op.id, unblocked.revision, held=False).operation
    assert released.status == "queued" and not released.held
    cancelled = move(store, released, "cancelled", "user_cancel")
    assert cancelled.status == "cancelled"
    assert store.queue().items == ()
    with pytest.raises(ControlError) as excinfo:
        move(store, cancelled, "queued", "user_release")
    assert code(excinfo) is E.INVALID_TRANSITION


def test_queue_order_single_slot_and_dispatch_gates(store, path):
    a, b, c = submit(store), submit(store, "setup_operation"), submit(store)
    queue = store.queue()
    assert [i.resource_id for i in queue.items] == [a.id, b.id, c.id]
    assert [i.position for i in queue.items] == [1, 2, 3]
    grant = store.begin_attempt(b.id, b.revision, owner())
    queue = store.queue()
    assert queue.active_item_id == queue.items[0].id and queue.items[0].position is None
    assert [i.position for i in queue.items[1:]] == [1, 2]
    with pytest.raises(ControlError) as excinfo:
        store.begin_attempt(a.id, a.revision, owner(2))
    assert code(excinfo) is E.SLOT_OCCUPIED
    with sqlite3.connect(path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE queue_items SET active=1, ord=NULL WHERE operation_id=?", (a.id,))
    refresh = submit(store, "refresh_operation")
    assert store.begin_attempt(refresh.id, refresh.revision, owner(3)).fencing_token
    store.transition(
        b.id,
        {
            "schema_version": 1,
            "expected_revision": grant.operation.revision,
            "to_status": "succeeded",
            "reason_code": "completion_verified",
        },
        fencing_token=grant.fencing_token,
    )
    held = store.set_hold(a.id, a.revision, held=True).operation
    with pytest.raises(ControlError) as excinfo:
        store.begin_attempt(a.id, held.revision, owner(2))
    assert code(excinfo) is E.INVALID_TRANSITION
    queue = store.set_dispatch(store.queue().revision, "paused").queue
    with pytest.raises(ControlError) as excinfo:
        store.begin_attempt(c.id, c.revision, owner(2))
    assert code(excinfo) is E.QUEUE_PAUSED
    with pytest.raises(ControlError) as excinfo:
        store.set_dispatch(queue.revision - 1, "running")
    assert code(excinfo) is E.REVISION_CONFLICT
    store.set_dispatch(queue.revision, "running")
    assert store.begin_attempt(c.id, c.revision, owner(2)).operation.status == "running"


def test_setup_waiting_user_and_interrupted_resume_go_to_tail(store):
    first = submit(store)
    op, fence = running(store, "setup_operation")
    waiting = store.transition(
        op.id,
        {
            "schema_version": 1,
            "expected_revision": op.revision,
            "to_status": "waiting_user",
            "reason_code": "credential_required",
        },
        fencing_token=fence,
    ).operation
    assert waiting.attempt and waiting.attempt.state == "released"
    assert store.queue().active_item_id is None
    resumed = move(store, waiting, "queued", "explicit_resume")
    assert [i.resource_id for i in store.queue().items] == [first.id, op.id]
    again = store.begin_attempt(op.id, resumed.revision, owner(2))
    assert again.operation.attempt.number == 2


def test_cancel_and_complete_race_is_serialized(store):
    op, fence = running(store)
    cancelling = move(store, op, "cancelling", "cancel_accepted")
    assert cancelling.attempt.state == "active"  # slot kept until exit evidence
    completion = {
        "schema_version": 1,
        "expected_revision": op.revision,
        "to_status": "succeeded",
        "reason_code": "completion_verified",
    }
    with pytest.raises(ControlError) as excinfo:
        store.transition(op.id, completion, fencing_token=fence)
    assert code(excinfo) is E.REVISION_CONFLICT
    with pytest.raises(ControlError) as excinfo:
        store.transition(
            op.id, {**completion, "expected_revision": cancelling.revision}, fencing_token=fence
        )
    assert code(excinfo) is E.INVALID_TRANSITION  # cancelling never becomes succeeded
    with pytest.raises(ControlError) as excinfo:
        move(store, cancelling, "cancelled", "exit_reconciled")  # owner evidence required
    assert code(excinfo) is E.FENCING_TOKEN_STALE
    done = move(store, cancelling, "cancelled", "exit_reconciled", fencing_token=fence)
    assert done.status == "cancelled" and store.queue().active_item_id is None
    # Completion first: a later cancel cannot rewrite the terminal state.
    other, fence2 = running(store)
    finished = move(store, other, "succeeded", "completion_verified", fencing_token=fence2)
    with pytest.raises(ControlError) as excinfo:
        move(store, other, "cancelling", "cancel_accepted")
    assert code(excinfo) is E.REVISION_CONFLICT
    with pytest.raises(ControlError) as excinfo:
        move(store, finished, "cancelling", "cancel_accepted")
    assert code(excinfo) is E.INVALID_TRANSITION


def test_fencing_and_launch_evidence(store):
    op, fence = running(store, launch=False)
    assert op.attempt.launch == "intended"
    with pytest.raises(ControlError) as excinfo:
        store.record_launch(op.id, fence + 1, outcome="confirmed", child=CHILD)
    assert code(excinfo) is E.FENCING_TOKEN_STALE
    with pytest.raises(ControlError) as excinfo:
        store.record_launch(op.id, fence, outcome="confirmed")
    assert code(excinfo) is E.REQUEST_INVALID
    confirmed = store.record_launch(op.id, fence, outcome="confirmed", child=CHILD)
    assert confirmed.attempt.launch == "confirmed" and confirmed.revision == op.revision + 1
    with pytest.raises(ControlError) as excinfo:
        store.record_launch(op.id, fence, outcome="failed")
    assert code(excinfo) is E.INVALID_TRANSITION
    store.renew_lease(op.id, fence, lease_seconds=30)
    assert store.get(op.id) == confirmed  # lease renewal is not a public revision
    with pytest.raises(ControlError) as excinfo:
        store.renew_lease(op.id, fence - 1)
    assert code(excinfo) is E.FENCING_TOKEN_STALE


# ----------------------------------------------------------------- outbox / sync


def test_outbox_sequence_allowlist_and_no_rewind(store, path):
    op = submit(store)
    move(store, op, "held", "user_hold")
    page = store.read_events()
    seqs = [int(e.id.split(":")[1]) for e in page.events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert [(e.type, e.resource_kind) for e in page.events] == [
        ("state_changed", "job"),
        ("queue_changed", "queue"),
        ("state_changed", "job"),
        ("queue_changed", "queue"),
    ]
    for event in page.events:
        assert set(event.to_wire()) == {
            "schema_version",
            "id",
            "resource_id",
            "resource_kind",
            "resource_revision",
            "timestamp",
            "type",
            "payload",
        }
        assert set(event.payload.to_wire()) == {"status", "reason_code", "attempt_id"}
    with sqlite3.connect(path) as db:
        columns = [r[1] for r in db.execute("PRAGMA table_info(outbox)")]
    assert columns == [
        "seq",
        "epoch",
        "type",
        "resource_kind",
        "resource_id",
        "resource_revision",
        "status",
        "reason_code",
        "attempt_id",
        "recorded_at",
    ]
    reopened = ControlStore(path)
    submit(reopened)
    assert int(reopened.read_events(page.next_cursor).events[0].id.split(":")[1]) == 5


def test_paging_over_hundreds_of_events_has_no_gap_or_duplicate(store):
    for _ in range(80):
        submit(store)  # 160 events
    seen: list[str] = []
    cursor = None
    while True:
        page = store.read_events(cursor, limit=37)
        seen.extend(e.id for e in page.events)
        cursor = page.next_cursor
        if not page.has_more:
            break
    assert len(seen) == len(set(seen)) == 160
    assert [int(s.split(":")[1]) for s in seen] == list(range(1, 161))
    assert store.read_events(cursor).events == () and cursor.endswith(":160")
    for limit in (0, 501, True):
        with pytest.raises(ControlError):
            store.read_events(limit=limit)


def test_filtered_read_skips_gaps_without_losing_later_events(store):
    a = submit(store)
    for _ in range(5):
        submit(store)
    page = store.read_events(resource_id=a.id)
    assert [e.resource_id for e in page.events] == [a.id]
    assert page.next_cursor.endswith(":12") and not page.has_more
    move(store, a, "held", "user_hold")
    later = store.read_events(page.next_cursor, resource_id=a.id)
    assert [e.payload.status for e in later.events] == ["held"]


def test_cursor_validation_and_epoch_rotation(store):
    submit(store)
    page = store.read_events()
    epoch = page.next_cursor.split(":")[0]
    for bad in ("x", f"{epoch}:01", f"{epoch}:-1", f"{epoch.upper()}:1", f"{epoch}:999"):
        with pytest.raises(ControlError) as excinfo:
            store.read_events(bad)
        assert code(excinfo) is E.CURSOR_INVALID and excinfo.value.status == 400
    with pytest.raises(ControlError) as excinfo:
        store.read_events("0" * 32 + ":1")
    assert code(excinfo) is E.EVENT_CURSOR_EXPIRED and excinfo.value.status == 410
    fresh = store.rotate_epoch()
    with pytest.raises(ControlError) as excinfo:
        store.read_events(page.next_cursor)
    assert code(excinfo) is E.EVENT_CURSOR_EXPIRED
    assert store.read_events().events == ()  # new epoch starts after the old watermark
    submit(store)
    after = store.read_events(fresh)
    assert [e.id.split(":")[0] for e in after.events] == [fresh.split(":")[0]] * 2
    assert store.snapshot().event_cursor.split(":")[0] == fresh.split(":")[0]


def test_uncommitted_events_are_invisible(path):
    observed: list[int] = []
    reader = ControlStore(path)

    def peek(where: str) -> None:
        if where == "before_commit":
            observed.append(len(reader.read_events().events))

    writer = ControlStore(path, _fault=peek)
    writer.submit("/api/v1/jobs", key(), body())
    assert observed == [0]
    assert len(reader.read_events().events) == 2


def test_snapshot_watermark_then_events_lose_nothing(store):
    a = submit(store)
    snap = store.snapshot()
    move(store, a, "held", "user_hold")
    b = submit(store)
    after = store.read_events(snap.event_cursor)
    assert {(e.resource_id, e.resource_revision) for e in after.events} >= {
        (a.id, 2),
        (b.id, 1),
    }
    assert all(int(e.id.split(":")[1]) > int(snap.event_cursor.split(":")[1]) for e in after.events)
    assert [o.id for o in snap.operations] == [a.id]


def test_snapshot_is_consistent_under_concurrent_writes(store, path):
    ops = [submit(store) for _ in range(4)]
    stop = threading.Event()

    def churn() -> None:
        writer = ControlStore(path)
        while not stop.is_set():
            for op in ops:
                current = writer.get(op.id)
                target = "held" if current.status == "queued" else None
                try:
                    if target:
                        writer.set_hold(op.id, current.revision, held=True)
                    else:
                        writer.set_hold(op.id, current.revision, held=False)
                except ControlError:
                    pass

    thread = threading.Thread(target=churn)
    thread.start()
    try:
        for _ in range(25):
            snap = store.snapshot()
            boundary = int(snap.event_cursor.split(":")[1])
            replay: dict[str, int] = {}
            cursor = None
            while True:
                page = store.read_events(cursor, limit=500)
                for event in page.events:
                    if int(event.id.split(":")[1]) <= boundary and event.resource_kind == "job":
                        replay[event.resource_id] = event.resource_revision
                cursor = page.next_cursor
                if not page.has_more:
                    break
            assert {o.id: o.revision for o in snap.operations} == replay
            queue_events = [
                e
                for e in store.read_events(limit=500).events
                if e.resource_kind == "queue" and int(e.id.split(":")[1]) <= boundary
            ]
            if queue_events and not store.read_events(limit=500).has_more:
                assert queue_events[-1].resource_revision == snap.queue.revision
    finally:
        stop.set()
        thread.join()


# ----------------------------------------------------------------- recovery


def test_decision_matrix_is_conservative():
    G, A, U, P = Liveness.GONE, Liveness.ALIVE, Liveness.UNKNOWN, Liveness.PID_REUSED
    assert decide(A, "intended", None).outcome is Outcome.OWNER_ALIVE
    assert decide(G, "confirmed", G).outcome is Outcome.INTERRUPTED
    assert decide(G, "failed", None).outcome is Outcome.INTERRUPTED
    for owner_state, launch, child, reason in (
        (U, "confirmed", G, "recovery_owner_unverified"),
        (P, "confirmed", G, "recovery_pid_reused"),
        (G, "intended", None, "recovery_launch_unconfirmed"),
        (G, "confirmed", A, "recovery_child_alive"),
        (G, "confirmed", P, "recovery_pid_reused"),
        (G, "confirmed", U, "recovery_owner_unverified"),
    ):
        decision = decide(owner_state, launch, child)
        assert decision.outcome is Outcome.RECOVERY_REQUIRED and decision.reason == reason


def test_recovery_pauses_holds_in_order_and_interrupts_verified_loss(store, path):
    job, fence = running(store)
    waiting = [submit(store), submit(store, "setup_operation"), submit(store)]
    blocked = move(store, waiting[2], "blocked", "prestart_revalidation_failed")
    attempts_before = table_counts(path)["attempts"]
    verifier = Fixed(Liveness.GONE)
    report = store.recover(verifier)
    assert report.queue_paused and report.changed
    assert report.interrupted == (job.id,) and report.recovery_required == ()
    assert report.held == tuple(op.id for op in waiting)
    queue = store.queue()
    assert queue.dispatch == "paused" and queue.active_item_id is None
    assert [i.resource_id for i in queue.items] == [op.id for op in waiting]
    assert all(i.held for i in queue.items)
    assert store.get(waiting[0].id).status == "held"
    assert store.get(waiting[1].id).status == "queued" and store.get(waiting[1].id).held
    assert store.get(blocked.id).status == "blocked" and store.get(blocked.id).held
    lost = store.get(job.id)
    assert lost.status == "interrupted" and lost.reason_code == "owner_lost"
    assert lost.attempt.state == "interrupted"
    events = store.read_events().events
    assert any(
        e.resource_id == job.id and e.payload.attempt_id == job.attempt.attempt_id
        for e in events
        if e.payload.status == "interrupted"
    )
    assert {p.pid for p in verifier.seen} == {1001, 4242}
    assert "nonce" not in ProcessIdentity.model_fields
    assert table_counts(path)["attempts"] == attempts_before  # nothing dispatched
    with pytest.raises(ControlError) as excinfo:
        store.renew_lease(job.id, fence)
    assert code(excinfo) is E.FENCING_TOKEN_STALE
    # Repeating recovery with the same observations is a no-op.
    high = store.read_events(limit=500).next_cursor
    again = store.recover(verifier)
    assert not again.changed and again.interrupted == () and again.held == ()
    assert store.read_events(limit=500).next_cursor == high


@pytest.mark.parametrize(
    ("verifier", "launch", "reason"),
    [
        (UnknownVerifier(), True, "recovery_owner_unverified"),
        (Fixed(Liveness.PID_REUSED), True, "recovery_pid_reused"),
        (Fixed(Liveness.GONE), False, "recovery_launch_unconfirmed"),
        (Fixed(Liveness.GONE, Liveness.ALIVE), True, "recovery_child_alive"),
    ],
)
def test_unverifiable_owner_requires_recovery_and_keeps_slot(store, verifier, launch, reason):
    op, _ = running(store, launch=launch)
    queued = submit(store)
    report = store.recover(verifier)
    assert report.recovery_required == (op.id,) and report.interrupted == ()
    flagged = store.get(op.id)
    assert flagged.status == "running" and flagged.recovery_required == reason
    assert flagged.attempt.state == "active"
    resumed = store.set_dispatch(store.queue().revision, "running").queue
    assert resumed.active_item_id is not None
    released = store.set_hold(queued.id, store.get(queued.id).revision, held=False).operation
    with pytest.raises(ControlError) as excinfo:
        store.begin_attempt(queued.id, released.revision, owner(2))
    assert code(excinfo) is E.SLOT_OCCUPIED  # never a second worker
    revision = store.get(op.id).revision
    store.recover(verifier)
    assert store.get(op.id).revision == revision  # same observation: no new revision


def test_recovery_ignores_clock_jumps_and_verifier_errors(path):
    clock = Clock()
    store = ControlStore(path, clock=clock)
    op, _ = running(store)

    class Broken:
        def check(self, process: ProcessIdentity) -> Liveness:
            raise RuntimeError(SENTINEL)

    for jump in (timedelta(days=3650), -timedelta(days=7300)):
        clock.now += jump
        report = store.recover(Broken())
        assert report.recovery_required == (op.id,)
        assert store.get(op.id).status == "running"


def test_recovery_recognises_current_owner_and_lost_lease_without_dispatch(store):
    op, _ = running(store)
    report = store.recover(UnknownVerifier(), current_owner=owner())
    assert report.owner_alive == (op.id,) and store.get(op.id).recovery_required is None


def test_recovery_flags_active_record_without_lease(store, path):
    op, _ = running(store)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM leases")
    report = store.recover(Fixed(Liveness.GONE))
    assert report.recovery_required == (op.id,)
    assert store.get(op.id).recovery_required == "recovery_owner_unverified"


# ----------------------------------------------------------------- privacy


def test_private_values_never_reach_public_tables_projections_or_errors(store, path, caplog):
    caplog.set_level("DEBUG")
    op, _ = running(store)
    store.recover(UnknownVerifier())
    snapshot = store.snapshot().to_wire()
    events = store.read_events(limit=500).to_wire()
    public = json.dumps([snapshot, events, store.get(op.id).to_wire()], ensure_ascii=False)
    for secret in (SENTINEL, NONCE, "boot-1", "child-start", "fencing"):
        assert secret not in public
    assert store.get_private(op.id).private_payload == {"prompt": SENTINEL}
    with sqlite3.connect(path) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            rows = db.execute(f"SELECT * FROM {table}").fetchall()
            dump = json.dumps(rows, default=repr, ensure_ascii=False)
            assert (SENTINEL in dump) is (table == "operation_private"), table
            assert (NONCE in dump) is (table == "attempt_private"), table
    for record in caplog.records:
        assert SENTINEL not in record.getMessage() and NONCE not in record.getMessage()
    with pytest.raises(ControlError) as excinfo:
        store.submit("/api/v1/jobs", "synthetic-key-idem-x", {"kind": SENTINEL})
    assert SENTINEL not in str(excinfo.value) + repr(excinfo.value)
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__


@pytest.mark.parametrize(
    "payload",
    [
        {"hf_token": "x"},
        {"nested": [{"Authorization": "x"}]},
        {"API_KEY": "x"},
        {"session_cookie": "x"},
        {"value": "hf_" + "A" * 34},
    ],
)
def test_credentials_are_never_persisted(store, path, payload):
    before = table_counts(path)
    with pytest.raises(ControlError) as excinfo:
        store.submit("/api/v1/jobs", key(), body(payload=payload))
    assert code(excinfo) is E.PRIVATE_PAYLOAD_FORBIDDEN
    assert table_counts(path) == before


def test_corrupt_rows_raise_value_free_errors(store, path):
    op = submit(store)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE operations SET status=? WHERE id=?", (SENTINEL, op.id))
    with pytest.raises(ControlError) as excinfo:
        store.get(op.id)
    assert code(excinfo) is E.STORE_CORRUPT and SENTINEL not in repr(excinfo.value)


# ----------------------------------------------------------------- backup


def test_online_backup_restore_requires_epoch_rotation(store, path, tmp_path):
    op = submit(store)
    destination = tmp_path / "backup" / "copy.sqlite3"
    destination.parent.mkdir()
    store.backup(destination)
    if os.name != "nt":
        assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ControlError) as excinfo:
        store.backup(destination)
    assert code(excinfo) is E.STORE_EXISTS
    copy = ControlStore(destination, create=False)
    assert copy.get(op.id) == store.get(op.id)
    old = copy.snapshot().event_cursor
    copy.rotate_epoch()
    with pytest.raises(ControlError) as excinfo:
        copy.read_events(old)
    assert code(excinfo) is E.EVENT_CURSOR_EXPIRED


# ----------------------------------------------------------------- compatibility


def test_imports_are_lightweight_and_default_gui_never_creates_store(tmp_path):
    script = """
import sys
import utteran_control.store, utteran_control.recovery
from utteran_contracts.control_schema import schema_bytes
assert schema_bytes()
banned = {'utteran', 'utteran_gui', 'utteran_settings', 'fastapi', 'uvicorn', 'webview',
          'torch', 'faster_whisper', 'openvino', 'pyannote', 'huggingface_hub', 'keyring'}
loaded = {n.split('.')[0] for n in sys.modules}
assert not loaded & banned, loaded & banned
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True)
    gui = """
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from utteran_gui.api import create_app, SESSION_HEADER
app = create_app('synthetic-session', repo_root=Path(sys.argv[1]))
with TestClient(app, headers={SESSION_HEADER: 'synthetic-session'}) as client:
    assert client.get('/api/health').status_code == 200
    assert client.get('/api/v1/events').status_code == 404
    assert client.get('/api/v1/sync').status_code == 404
assert not any(n.startswith('utteran_control') for n in sys.modules)
"""
    env = {**os.environ, "UTTERAN_DATA_ROOT": str(tmp_path / "root")}
    subprocess.run([sys.executable, "-c", gui, str(ROOT)], check=True, capture_output=True, env=env)
    assert not (tmp_path / "root" / "data" / "control-v1").exists()


def test_public_models_are_exported_contracts():
    names = {model.__name__ for model in PUBLIC_MODELS}
    assert {"OperationView", "EventEnvelope", "ControlSnapshot", "RecoveryReport"} <= names
    assert "PrivateOperation" not in names and "LeaseGrant" not in names


# ----------------------------------------------------------------- PR #28 review: queue replay

DISPATCH = "/api/v1/queue/dispatch"


def qscope(k: str) -> IdempotencyScope:
    return IdempotencyScope(DISPATCH, k)


def outbox_high(path: Path) -> tuple[int, int]:
    with sqlite3.connect(path) as db:
        seq = db.execute("SELECT value FROM counters WHERE name='outbox_seq'").fetchone()[0]
        revision = db.execute("SELECT revision FROM queue").fetchone()[0]
    return seq, revision


def test_dispatch_replay_returns_first_response_verbatim(store, path):
    first = store.set_dispatch(1, "paused", idempotency=qscope("queue-key-000001"))
    assert isinstance(first, AcceptedQueue) and not first.replayed
    assert first.status == 200 and dict(first.headers) == {"ETag": '"2"'}
    assert first.queue.dispatch == "paused" and first.queue.revision == 2
    before = table_counts(path), outbox_high(path)
    again = store.set_dispatch(1, "paused", idempotency=qscope("queue-key-000001"))
    assert again.replayed and again.status == first.status
    assert dict(again.headers) == dict(first.headers) and again.queue == first.queue
    assert (table_counts(path), outbox_high(path)) == before
    # Later progress under another key does not rewrite the first response or the state.
    resumed = store.set_dispatch(2, "running", idempotency=qscope("queue-key-000002"))
    submit(store)
    late = ControlStore(path).set_dispatch(1, "paused", idempotency=qscope("queue-key-000001"))
    assert late.replayed and late.queue == first.queue and late.queue.dispatch == "paused"
    current = store.queue()
    assert current.dispatch == "running" and current.revision == resumed.queue.revision + 1


def test_dispatch_response_lost_then_replayed_after_reopen(path):
    lost = ControlStore(path, _fault=full("after_commit"))
    with pytest.raises(ControlError):
        lost.set_dispatch(1, "paused", idempotency=qscope("queue-key-lost-01"))
    committed = table_counts(path), outbox_high(path)
    assert committed[0]["queue_replays"] == 1 and committed[1] == (1, 2)
    replay = ControlStore(path).set_dispatch(1, "paused", idempotency=qscope("queue-key-lost-01"))
    assert replay.replayed and replay.queue.revision == 2 and replay.queue.dispatch == "paused"
    assert (table_counts(path), outbox_high(path)) == committed


def test_dispatch_conflicts_and_unaccepted_requests_record_nothing(store, path):
    store.set_dispatch(1, "paused", idempotency=qscope("queue-key-000010"))
    before = table_counts(path), outbox_high(path), path.read_bytes()
    for revision, state in ((1, "running"), (2, "paused"), (2, "running")):
        with pytest.raises(ControlError) as excinfo:
            store.set_dispatch(revision, state, idempotency=qscope("queue-key-000010"))
        assert code(excinfo) is E.IDEMPOTENCY_CONFLICT and excinfo.value.status == 409
    # Stale CAS under a new key: 412 and no replay row; the key stays free.
    with pytest.raises(ControlError) as excinfo:
        store.set_dispatch(1, "running", idempotency=qscope("queue-key-000011"))
    assert code(excinfo) is E.REVISION_CONFLICT
    with pytest.raises(ControlError) as excinfo:
        store.set_dispatch(2, "stopped", idempotency=qscope("queue-key-000012"))  # type: ignore[arg-type]
    assert code(excinfo) is E.REQUEST_INVALID
    assert (table_counts(path), outbox_high(path), path.read_bytes()) == before
    ok = store.set_dispatch(2, "running", idempotency=qscope("queue-key-000011"))
    assert not ok.replayed and ok.queue.dispatch == "running"
    for bad in (IdempotencyScope(DISPATCH, "short"), IdempotencyScope("/queue", "queue-key-0000x")):
        with pytest.raises(ControlError):
            store.set_dispatch(3, "paused", idempotency=bad)


def test_dispatch_noop_records_response_without_revision_or_event(store, path):
    before = outbox_high(path)
    noop = store.set_dispatch(1, "running", idempotency=qscope("queue-key-noop-01"))
    assert not noop.replayed and noop.queue.revision == 1 and noop.queue.dispatch == "running"
    assert outbox_high(path) == before and table_counts(path)["queue_replays"] == 1
    again = store.set_dispatch(1, "running", idempotency=qscope("queue-key-noop-01"))
    assert again.replayed and again.queue == noop.queue and outbox_high(path) == before
    assert store.set_dispatch(1, "running").queue.revision == 1  # keyless no-op unchanged


def test_dispatch_scope_is_not_shared_with_operation_replays(store):
    store.submit(DISPATCH, "queue-key-shared1", body())
    with pytest.raises(ControlError) as excinfo:
        store.set_dispatch(2, "paused", idempotency=qscope("queue-key-shared1"))
    assert code(excinfo) is E.IDEMPOTENCY_CONFLICT
    store.set_dispatch(2, "paused", idempotency=qscope("queue-key-shared2"))
    with pytest.raises(ControlError) as excinfo:
        store.submit(DISPATCH, "queue-key-shared2", body())
    assert code(excinfo) is E.IDEMPOTENCY_CONFLICT


@pytest.mark.parametrize("hook", ["fault", "full", "readonly", "busy"])
def test_dispatch_failures_leave_no_orphan_rows(store, path, hook):
    before = table_counts(path), outbox_high(path)
    if hook == "fault":
        failing = ControlStore(path, _fault=full("before_commit"))
    elif hook == "full":

        def limit(db: sqlite3.Connection) -> None:
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={pages}")

        for _ in range(40):  # QueueView body > one page, so the replay row needs a new page
            submit(store)
        failing = ControlStore(path, _connect_hook=limit)
        while True:  # fill every free page under the cap; queue body grows with items
            try:
                failing.submit("/api/v1/jobs", key(), body(payload={"n": "x" * 200}))
            except ControlError as exc:
                assert exc.code is E.STORE_FULL
                break
        before = table_counts(path), outbox_high(path)
    elif hook == "readonly":
        failing = ControlStore(path, _connect_hook=lambda db: db.execute("PRAGMA query_only=ON"))
    else:
        failing = ControlStore(path, timeout=0.05)
    blocker = None
    if hook == "busy":
        blocker = sqlite3.connect(path, isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
    try:
        revision = before[1][1]
        with pytest.raises(ControlError):
            failing.set_dispatch(revision, "paused", idempotency=qscope("queue-key-fail-01"))
    finally:
        if blocker is not None:
            blocker.rollback()
            blocker.close()
    assert (table_counts(path), outbox_high(path)) == before
    ok = store.set_dispatch(before[1][1], "paused", idempotency=qscope("queue-key-fail-01"))
    assert not ok.replayed


def test_dispatch_same_key_concurrent_threads_apply_once(store, path):
    results: list[AcceptedQueue] = []
    barrier = threading.Barrier(6)

    def send() -> None:
        barrier.wait()
        results.append(
            ControlStore(path).set_dispatch(1, "paused", idempotency=qscope("queue-key-race-1"))
        )

    threads = [threading.Thread(target=send) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(not r.replayed for r in results) == 1
    assert len({r.queue for r in results}) == 1 and outbox_high(path) == (1, 2)


DISPATCH_PROCESS = """
import sys
from pathlib import Path
from utteran_control.store import ControlStore, IdempotencyScope
sys.stdin.read()
store = ControlStore(Path(sys.argv[1]), timeout=10)
r = store.set_dispatch(1, 'paused', idempotency=IdempotencyScope('/api/v1/queue/dispatch',
                                                               'queue-key-proc-01'))
print(r.replayed, r.queue.revision, r.headers['ETag'])
"""


def test_dispatch_same_key_two_processes_apply_once(store, path):
    children = [
        subprocess.Popen(
            [sys.executable, "-c", DISPATCH_PROCESS, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = sorted(child.communicate("go")[0].split() for child in children)
    assert outputs == [["False", "2", '"2"'], ["True", "2", '"2"']]
    assert outbox_high(path) == (1, 2)


def test_queue_replay_body_is_public_and_schema_valid(store, path):
    op, _ = running(store)
    accepted = store.set_dispatch(
        store.queue().revision, "paused", idempotency=qscope("queue-key-public1")
    )
    with sqlite3.connect(path) as db:
        stored = db.execute("SELECT body, headers FROM queue_replays").fetchone()
    text = json.dumps(stored, ensure_ascii=False)
    for secret in (SENTINEL, NONCE, "boot-1", "child-start"):
        assert secret not in text
    schema = json.loads(schema_bytes())
    jsonschema.Draft202012Validator({**schema, "$ref": "#/$defs/QueueView"}).validate(
        json.loads(stored[0])
    )
    assert QueueView.model_validate_json(stored[0]) == accepted.queue
    assert accepted.queue.items[0].resource_id == op.id


# ----------------------------------------------------------------- PR #28 review: v1 -> v2


def v1_module() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "b6_store_v1", ROOT / "tests" / "fixtures" / "b6" / "store_v1.py"
    )
    assert spec and spec.loader
    if "b6_store_v1" in sys.modules:
        return sys.modules["b6_store_v1"]
    module = importlib.util.module_from_spec(spec)
    sys.modules["b6_store_v1"] = module
    spec.loader.exec_module(module)
    return module


def make_v1(path: Path) -> dict[str, Any]:
    old = v1_module()
    store = old.ControlStore(path, clock=Clock())
    created = store.submit("/api/v1/jobs", "v1-operation-key-1", body())
    held = store.set_hold(created.operation.id, 1, held=True)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {
            name: db.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "queue_replays" not in tables
    return {"created": created, "held": held, "tables": tables, "events": store.read_events()}


def test_v1_store_migrates_to_v2_without_losing_data(path):
    v1 = make_v1(path)
    store = ControlStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        for name, rows in v1["tables"].items():
            assert db.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall() == rows, name
        assert db.execute("SELECT count(*) FROM queue_replays").fetchone()[0] == 0
    # Old operation replay keeps its exact format and meaning.
    replay = store.submit("/api/v1/jobs", "v1-operation-key-1", body())
    assert replay.replayed and replay.operation.to_wire() == v1["created"].operation.to_wire()
    assert dict(replay.headers) == dict(v1["created"].headers)
    assert store.get(v1["held"].operation.id).status == "held"
    assert store.read_events().to_wire() == v1["events"].to_wire()  # same epoch, no rewind
    assert store.get_private(v1["created"].operation.id).private_payload == {"prompt": SENTINEL}
    accepted = store.set_dispatch(
        store.queue().revision, "paused", idempotency=qscope("queue-key-mig-01")
    )
    assert not accepted.replayed
    ControlStore(path)  # reopening v2 is a no-op
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("failure", ["fault", "full"])
def test_failed_migration_leaves_complete_v1_store(path, failure):
    make_v1(path)
    with sqlite3.connect(path) as db:
        tables = {
            n: db.execute(f"SELECT * FROM {n} ORDER BY 1").fetchall()
            for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    if failure == "fault":
        kwargs: dict[str, Any] = {"_fault": full("before_commit")}
    else:

        def limit(db: sqlite3.Connection) -> None:
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={pages}")

        kwargs = {"_connect_hook": limit}
    with pytest.raises(ControlError) as excinfo:
        ControlStore(path, **kwargs)
    assert code(excinfo) is E.STORE_FULL
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        now = {
            n: db.execute(f"SELECT * FROM {n} ORDER BY 1").fetchall()
            for (n,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert now == tables
    assert ControlStore(path).queue().revision >= 1  # a later normal open migrates


MIGRATE_CRASH = """
import os, sys
from pathlib import Path
from utteran_control.store import ControlStore
def hook(where):
    if where == 'before_commit':
        os._exit(73)
ControlStore(Path(sys.argv[1]), _fault=hook)
"""


def test_process_crash_during_migration_keeps_v1(path):
    make_v1(path)
    child = subprocess.run(
        [sys.executable, "-c", MIGRATE_CRASH, str(path)], capture_output=True, text=True
    )
    assert child.returncode == 73, child.stderr
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert ControlStore(path).submit("/api/v1/jobs", "v1-operation-key-1", body()).replayed


def test_concurrent_opens_migrate_once(path):
    make_v1(path)
    errors: list[Any] = []
    barrier = threading.Barrier(6)

    def open_store() -> None:
        barrier.wait()
        try:
            ControlStore(path, timeout=10)
        except ControlError as exc:  # pragma: no cover - must not happen
            errors.append(exc.code)

    threads = [threading.Thread(target=open_store) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            db.execute("SELECT count(*) FROM sqlite_master WHERE name='queue_replays'").fetchone()[
                0
            ]
            == 1
        )


def test_abnormal_v1_headers_fail_closed(path, tmp_path):
    ControlStore(path)  # v2 layout
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=1")  # v1 header but v2 tables: inconsistent
    before = path.read_bytes()
    with pytest.raises(ControlError) as excinfo:
        ControlStore(path)
    assert code(excinfo) is E.STORE_CORRUPT and path.read_bytes() == before
    broken = tmp_path / "broken" / "operations.sqlite3"
    make_v1(broken)
    with sqlite3.connect(broken) as db:
        db.execute("DROP TABLE leases")
    before = broken.read_bytes()
    with pytest.raises(ControlError) as excinfo:
        ControlStore(broken, create=False)
    assert code(excinfo) is E.STORE_CORRUPT and broken.read_bytes() == before
    with sqlite3.connect(broken) as db:
        db.execute("PRAGMA user_version=3")
    with pytest.raises(ControlError) as excinfo:
        ControlStore(broken)
    assert code(excinfo) is E.STORE_SCHEMA_UNSUPPORTED
