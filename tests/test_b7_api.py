"""B7 opt-in v1 HTTP routes, single v1 mount, cutover / rollback and default isolation."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_b7_jobs import SENTINEL, SESSION, Env, FakeRunner
from utteran_control.store import IdempotencyScope
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.job_runtime import JobRuntime, RuntimeStateError
from utteran_gui.operation_queue import OperationQueue, QueueClosedError
from utteran_gui.process_verifier import current_owner

ROOT = Path(__file__).parents[1]
AUTHORITY = "127.0.0.1:8765"


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path, None)


def runtime_for(env: Env, queue: OperationQueue, runner: FakeRunner | None = None) -> JobRuntime:
    return JobRuntime(
        store_path=env.store.path,
        settings_path=env.settings_path,
        legacy_queue=queue,
        provider=env.provider,
        runner=runner or FakeRunner(env),
        job_dir=env.tmp / "jobs",
        autostart_dispatcher=False,
    )


@pytest.fixture
def client(env: Env):
    queue = OperationQueue()
    runtime = runtime_for(env, queue)
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
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        c.runtime = runtime  # type: ignore[attr-defined]
        c.queue = queue  # type: ignore[attr-defined]
        yield c


def headers(key: str, match: str | None = None, **extra: str) -> dict[str, str]:
    result = {"Idempotency-Key": key, **extra}
    if match is not None:
        result["If-Match"] = match
    return result


def test_default_app_has_no_job_routes_store_or_heavy_imports(tmp_path: Path) -> None:
    app = create_app(
        SESSION,
        repo_root=ROOT,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=tmp_path / "s" / "resources.sqlite3",
    )
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        for path in ("/api/v1/jobs", "/api/v1/queue", "/api/v1/execution-resolutions"):
            assert c.get(path).status_code in {404, 405}
        assert c.get("/api/v1/presets").status_code == 200  # B5 API still served
    with pytest.raises(ValueError):
        create_app(SESSION, repo_root=ROOT, enable_v1_jobs=True)
    script = (
        "import sys, tempfile\nfrom pathlib import Path\n"
        "from utteran_gui.api import create_app\n"
        f"create_app('s', repo_root=Path({str(ROOT)!r}))\n"
        "bad=[m for m in ('utteran_control','utteran_gui.job_runtime','utteran_gui.jobs_api',"
        "'utteran_gui.job_dispatcher','utteran','torch','faster_whisper') if m in sys.modules]\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_activation_recovers_and_starts_paused(client: TestClient) -> None:
    queue = client.get("/api/v1/queue")
    assert queue.status_code == 200 and queue.json()["dispatch"] == "paused"
    assert queue.headers["ETag"] == f'"{queue.json()["revision"]}"'
    assert client.queue.closed  # type: ignore[attr-defined]
    with pytest.raises(QueueClosedError):
        client.queue.submit(
            "x", kind="job", label="x", runner=lambda: "completed", canceller=lambda: None
        )  # type: ignore[attr-defined]
    assert client.get("/api/queue").status_code == 200  # legacy read API unchanged


@pytest.mark.parametrize(
    "extra, status, code",
    [
        ({SESSION_HEADER: "wrong"}, 401, "session_required"),
        ({"Host": "evil.example:8765"}, 403, "host_rejected"),
        ({"Origin": "http://evil.example"}, 403, "origin_rejected"),
    ],
)
def test_auth_host_origin(client: TestClient, extra, status, code) -> None:
    response = client.get("/api/v1/jobs", headers=extra)
    assert response.status_code == status and response.json()["error"]["code"] == code


def test_patch_requires_origin_or_header(client: TestClient) -> None:
    raw = TestClient(client.app, base_url="http://" + AUTHORITY)
    raw.cookies.set("utteran_gui_session", SESSION)
    response = raw.patch(
        "/api/v1/queue/control",
        json={"schema_version": 1, "dispatch": "running"},
        headers=headers("csrf-key-0000000001", '"1"'),
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "origin_required"


def test_http_end_to_end_submit_dispatch_and_controls(client: TestClient, env: Env) -> None:
    files = [env.file("a.wav"), env.file("b.wav", b"b")]
    session = env.session(*files)
    resolved = client.post(
        "/api/v1/execution-resolutions", json=session, headers=headers("http-res-key-00001")
    )
    assert resolved.status_code == 201, resolved.text
    body = resolved.json()
    assert SENTINEL not in resolved.text and str(env.tmp) not in resolved.text
    submit = {
        "schema_version": 1,
        "resolution_token": body["token"],
        "editing_session": session,
        "resume_mode": "resume",
    }
    accepted = client.post("/api/v1/jobs", json=submit, headers=headers("http-job-key-00001"))
    assert accepted.status_code == 202 and accepted.headers["Location"] == "/api/v1/jobs"
    replay = client.post("/api/v1/jobs", json=submit, headers=headers("http-job-key-00001"))
    assert replay.json() == accepted.json() and replay.headers["ETag"] == accepted.headers["ETag"]
    assert SENTINEL not in accepted.text and str(env.tmp) not in accepted.text
    jobs = accepted.json()["jobs"]
    listed = client.get("/api/v1/jobs")
    assert [j["id"] for j in listed.json()["items"]] == [j["id"] for j in jobs]
    one = client.get(f"/api/v1/jobs/{jobs[0]['id']}")
    assert one.status_code == 200 and one.headers["ETag"] == f'"{jobs[0]["revision"]}"'
    assert client.get("/api/v1/jobs/job_" + "0" * 32).status_code == 404
    assert client.get("/api/v1/jobs/not-an-id").status_code == 404
    queue = client.get("/api/v1/queue").json()
    revision = f'"{queue["revision"]}"'
    # Missing key / missing If-Match / wildcard / stale revision
    assert (
        client.patch(
            "/api/v1/queue/control",
            json={"schema_version": 1, "dispatch": "running"},
            headers={"If-Match": revision},
        ).status_code
        == 400
    )
    missing = client.patch(
        "/api/v1/queue/control",
        json={"schema_version": 1, "dispatch": "running"},
        headers=headers("ctl-http-key-00001"),
    )
    assert missing.status_code == 428
    star = client.patch(
        "/api/v1/queue/control",
        json={"schema_version": 1, "dispatch": "running"},
        headers=headers("ctl-http-key-00002", "*"),
    )
    assert star.status_code == 400
    stale = client.patch(
        "/api/v1/queue/control",
        json={"schema_version": 1, "dispatch": "running"},
        headers=headers("ctl-http-key-00003", '"999"'),
    )
    assert stale.status_code == 412 and stale.json()["error"]["code"] == "revision_conflict"
    item_ids = [i["id"] for i in queue["items"]]
    order = client.put(
        "/api/v1/queue/order",
        json={"schema_version": 1, "waiting_item_ids": item_ids[::-1]},
        headers=headers("order-http-key-001", revision),
    )
    assert order.status_code == 200 and [i["id"] for i in order.json()["items"]] == item_ids[::-1]
    bad = client.put(
        "/api/v1/queue/order",
        json={"schema_version": 1, "waiting_item_ids": item_ids[:1]},
        headers=headers("order-http-key-002", order.headers["ETag"]),
    )
    assert bad.status_code == 422
    hold = client.patch(
        f"/api/v1/queue/items/{item_ids[1]}",
        json={"schema_version": 1, "held": True},
        headers=headers("hold-http-key-0001", order.headers["ETag"]),
    )
    assert hold.status_code == 200 and hold.json()["items"][0]["held"]
    running = client.patch(
        "/api/v1/queue/control",
        json={"schema_version": 1, "dispatch": "running"},
        headers=headers("ctl-http-key-00004", hold.headers["ETag"]),
    )
    assert running.status_code == 200 and running.json()["dispatch"] == "running"
    runtime: JobRuntime = client.runtime  # type: ignore[attr-defined]
    assert runtime.dispatcher is not None and runtime.dispatcher.run_once()
    # Held item was skipped; the other (first eligible) job ran.
    states = {j["id"]: j["status"] for j in client.get("/api/v1/jobs").json()["items"]}
    assert sorted(states.values()) == ["held", "succeeded"]
    held_id = next(k for k, v in states.items() if v == "held")
    job = client.get(f"/api/v1/jobs/{held_id}").json()
    force = client.post(
        f"/api/v1/jobs/{held_id}/cancel",
        json={"schema_version": 1, "mode": "force"},
        headers=headers("cancel-http-key-01", f'"{job["revision"]}"'),
    )
    assert force.status_code == 409 and force.json()["error"]["code"] == "cancel_mode_unsupported"
    cancel = client.post(
        f"/api/v1/jobs/{held_id}/cancel",
        json={"schema_version": 1, "mode": "cooperative"},
        headers=headers("cancel-http-key-02", f'"{job["revision"]}"'),
    )
    assert cancel.status_code == 202 and cancel.json()["status"] == "cancelled"
    retry = client.post(
        f"/api/v1/jobs/{held_id}/retry",
        json={"schema_version": 1},
        headers=headers("retry-http-key-001", cancel.headers["ETag"]),
    )
    assert retry.status_code == 202 and retry.json()["retry_of"] == held_id
    for path in ("/api/v1/events", "/api/v1/sync"):
        assert client.get(path).status_code == 404
    assert (
        client.post(
            f"/api/v1/jobs/{held_id}/diagnostics",
            json={},
            headers=headers("diag-http-key-001", '"1"'),
        ).status_code
        == 404
    )


def test_http_input_failures_are_fixed_codes(client: TestClient, env: Env) -> None:
    raw = client.post(
        "/api/v1/jobs",
        content=b'{"a":1,"a":2}',
        headers={**headers("dup-http-key-0001"), "Content-Type": "application/json"},
    )
    assert raw.status_code == 400 and raw.json()["error"]["code"] == "malformed_json"
    utf = client.post(
        "/api/v1/jobs",
        content=b'{"a":"\xff"}',
        headers={**headers("utf-http-key-0001"), "Content-Type": "application/json"},
    )
    assert utf.status_code == 400
    session = env.session(env.inputs / "missing.wav")
    failed = client.post(
        "/api/v1/execution-resolutions", json=session, headers=headers("miss-http-key-001")
    )
    assert failed.status_code == 422 and str(env.tmp) not in failed.text
    forged = {
        "schema_version": 1,
        "resolution_token": "v1." + "0" * 32 + ".0000000000000." + "0" * 64,
        "editing_session": env.session(env.file()),
        "resume_mode": "resume",
    }
    response = client.post("/api/v1/jobs", json=forged, headers=headers("forge-http-key-01"))
    assert response.status_code == 409 and response.json()["error"]["code"] == "resolution_invalid"
    base64 = dict(forged, resolution_token="eyJzZXR0aW5ncyI6e319")
    assert (
        client.post("/api/v1/jobs", json=base64, headers=headers("b64-http-key-0001")).status_code
        == 422
    )


def test_runtime_cutover_rollback_and_restart(env: Env) -> None:
    queue = OperationQueue()
    gate = threading.Event()
    queue.submit(
        "legacy",
        kind="job",
        label="legacy",
        runner=lambda: (gate.wait(5), "completed")[1],
        canceller=gate.set,
    )
    runtime = runtime_for(env, queue)
    with pytest.raises(RuntimeStateError) as excinfo:
        runtime.activate()  # pending/active legacy work: refuse cutover
    assert excinfo.value.code == "legacy_busy" and not queue.closed
    gate.set()
    for _ in range(100):
        if queue.list()[0]["status"] == "completed":
            break
        threading.Event().wait(0.02)
    report = runtime.activate()
    assert report.queue_paused and queue.closed and runtime.last_backup is not None
    assert runtime.last_backup.is_file()  # store existed (fixture): backed up before v1
    env.submit(env.file("a.wav"))
    with pytest.raises(RuntimeStateError):
        runtime.activate()
    # Rollback with an active attempt is refused; the legacy queue stays closed.
    store = runtime.store
    assert store is not None
    nxt = store.queue()
    store.set_dispatch(nxt.revision, "running")
    held = store.queue().items[0]
    store.set_item_hold(
        held.id,
        store.queue().revision,
        False,
        IdempotencyScope("/api/v1/queue/items/" + held.id, "rel-key-000000001"),
    )
    job_id, revision = store.next_dispatchable()  # type: ignore[misc]
    store.begin_attempt(job_id, revision, current_owner())
    with pytest.raises(RuntimeStateError) as excinfo:
        runtime.deactivate(timeout=1)
    assert excinfo.value.code == "active_attempt" and queue.closed
    lease = store.active_job()
    assert lease is not None
    store.finish_attempt(lease[0], lease[1], "failed", error="worker_crash")
    backup = runtime.deactivate(timeout=1)
    assert backup.is_file() and not queue.closed and runtime.state == "inactive"
    queue.submit(
        "after", kind="job", label="after", runner=lambda: "completed", canceller=lambda: None
    )
    # Re-cutover after restart: history persists, recovery pauses dispatch again.
    for _ in range(100):
        if all(i["status"] == "completed" for i in queue.list()):
            break
        threading.Event().wait(0.02)
    again = runtime.activate()
    assert again.queue_paused and runtime.store is not None
    assert runtime.store.get_job(job_id).status == "failed"
    runtime.shutdown()
    assert not queue.closed


def test_resolution_and_job_errors_do_not_echo_private_values(
    client: TestClient, env: Env, caplog
) -> None:
    caplog.set_level("DEBUG")
    session = env.session(env.file("a.wav"))
    session["files"][0]["overrides"] = [{"path": "/asr/initial_prompt", "value": SENTINEL + "x"}]
    env.provider.fail = True
    response = client.post(
        "/api/v1/execution-resolutions", json=session, headers=headers("err-http-key-0001")
    )
    assert response.status_code == 409
    text = json.dumps(response.json(), ensure_ascii=False)
    assert SENTINEL not in text and str(env.tmp) not in text and SENTINEL not in caplog.text
    assert response.json()["error"]["issues"][0]["code"] == "resolver_unavailable"


def test_http_inflight_same_key_is_retryable_503_then_replays(client: TestClient, env: Env) -> None:
    """A client-side timeout (503 here) is neither a commit nor a rollback."""
    runtime: JobRuntime = client.runtime  # type: ignore[attr-defined]
    assert runtime.admission is not None and runtime.store is not None
    runtime.admission.inflight_wait = 0.2
    session = env.session(env.file("a.wav"))
    resolved = client.post(
        "/api/v1/execution-resolutions", json=session, headers=headers("inflight-res-0001")
    )
    submit = {
        "schema_version": 1,
        "resolution_token": resolved.json()["token"],
        "editing_session": session,
        "resume_mode": "resume",
    }
    entered, release = threading.Event(), threading.Event()

    def hook(where: str) -> None:
        if where == "batch_before_replay":
            entered.set()
            assert release.wait(10)

    runtime.store._fault = hook
    leader: list[Any] = []
    thread = threading.Thread(
        target=lambda: leader.append(
            runtime.admission.submit(
                IdempotencyScope("/api/v1/jobs", "inflight-job-0001"), submit, session=SESSION
            )  # type: ignore[union-attr]
        )
    )
    thread.start()
    assert entered.wait(10)
    busy = client.post("/api/v1/jobs", json=submit, headers=headers("inflight-job-0001"))
    assert busy.status_code == 503
    error = busy.json()["error"]
    assert error["code"] == "submission_in_progress" and error["retryable"] is True
    assert resolved.json()["token"] not in busy.text and str(env.tmp) not in busy.text
    release.set()
    thread.join(10)
    runtime.store._fault = None
    again = client.post("/api/v1/jobs", json=submit, headers=headers("inflight-job-0001"))
    assert again.status_code == 202
    assert again.json() == leader[0].batch.to_wire()
    assert again.headers["Location"] == leader[0].headers["Location"]
    assert again.headers["ETag"] == leader[0].headers["ETag"]
    assert len(client.get("/api/v1/jobs").json()["items"]) == 1
