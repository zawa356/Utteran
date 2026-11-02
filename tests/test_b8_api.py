"""B8 setup routes over HTTP: same /api/v1 mount, fixed errors, shared runtime and slot."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_b7_jobs import SESSION, Env, FakeRunner
from test_b8_setup import FW, SECRET, World
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.job_runtime import JobRuntime, SetupComponents
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.setup_executor import ProductionStepAdapter

ROOT = Path(__file__).parents[1]
AUTHORITY = "127.0.0.1:8765"


@pytest.fixture
def setup_client(tmp_path: Path):
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
    )
    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        c.runtime = runtime  # type: ignore[attr-defined]
        c.world = world  # type: ignore[attr-defined]
        c.env = env  # type: ignore[attr-defined]
        yield c


def h(key: str, match: str | None = None) -> dict[str, str]:
    result = {"Idempotency-Key": key}
    if match is not None:
        result["If-Match"] = match
    return result


PLAN = {
    "schema_version": 1,
    "profile_kind": "cuda",
    "model_refs": [FW],
    "diarization_enabled": False,
}


def test_setup_end_to_end_over_http(setup_client: TestClient) -> None:
    c = setup_client
    world: World = c.world  # type: ignore[attr-defined]
    created = c.post("/api/v1/setup-plans", json=PLAN, headers=h("plan-http-key-0001"))
    assert created.status_code == 201, created.text
    plan = created.json()
    assert created.headers["Location"] == f"/api/v1/setup-plans/{plan['id']}"
    replay = c.post("/api/v1/setup-plans", json=PLAN, headers=h("plan-http-key-0001"))
    assert replay.status_code == 201 and replay.json() == plan
    conflict = c.post(
        "/api/v1/setup-plans", json={**PLAN, "profile_kind": "cpu"}, headers=h("plan-http-key-0001")
    )
    assert (
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    )
    got = c.get(f"/api/v1/setup-plans/{plan['id']}")
    assert got.status_code == 200 and got.headers["ETag"] == f'"{plan["revision"]}"'
    approve_body = {
        "schema_version": 1,
        "plan_hash": plan["plan_hash"],
        "accepted_agreement_ids": [],
    }
    missing = c.post(
        f"/api/v1/setup-plans/{plan['id']}/approve",
        json=approve_body,
        headers=h("apr-http-key-00001"),
    )
    assert missing.status_code == 428
    stale = c.post(
        f"/api/v1/setup-plans/{plan['id']}/approve",
        json=approve_body,
        headers=h("apr-http-key-00002", '"7"'),
    )
    assert stale.status_code == 412
    approved = c.post(
        f"/api/v1/setup-plans/{plan['id']}/approve",
        json=approve_body,
        headers=h("apr-http-key-00003", f'"{plan["revision"]}"'),
    )
    assert approved.status_code == 201, approved.text
    approval = approved.json()
    start = {
        "schema_version": 1,
        "plan_ref": {"id": plan["id"], "revision": approval["plan_revision"]},
        "approval_id": approval["id"],
    }
    accepted = c.post("/api/v1/setup-operations", json=start, headers=h("start-http-key-001"))
    assert accepted.status_code == 202, accepted.text
    op = accepted.json()
    assert accepted.headers["Location"] == f"/api/v1/setup-operations/{op['id']}"
    again = c.post("/api/v1/setup-operations", json=start, headers=h("start-http-key-001"))
    assert again.json() == op and again.headers["ETag"] == accepted.headers["ETag"]
    reuse = c.post("/api/v1/setup-operations", json=start, headers=h("start-http-key-002"))
    assert reuse.status_code == 409 and reuse.json()["error"]["code"] == "plan_consumed"
    listed = c.get("/api/v1/setup-operations").json()
    assert [i["id"] for i in listed["items"]] == [op["id"]]
    queue = c.get("/api/v1/queue").json()
    assert [i["resource_kind"] for i in queue["items"]] == ["setup_operation"]
    # Activation recovery left dispatch paused; nothing ran yet.
    assert queue["dispatch"] == "paused" and world.calls == []
    run = c.patch(
        "/api/v1/queue/control",
        json={"schema_version": 1, "dispatch": "running"},
        headers=h("ctl-http-key-0001", f'"{queue["revision"]}"'),
    )
    assert run.status_code == 200
    runtime: JobRuntime = c.runtime  # type: ignore[attr-defined]
    assert runtime.dispatcher is not None and runtime.dispatcher.run_once()
    final = c.get(f"/api/v1/setup-operations/{op['id']}")
    assert final.status_code == 200 and final.json()["status"] == "succeeded"
    for text in (created.text, approved.text, accepted.text, final.text):
        assert SECRET not in text and str(world.tmp) not in text
    force = c.post(
        f"/api/v1/setup-operations/{op['id']}/cancel",
        json={"schema_version": 1, "mode": "force"},
        headers=h("cancel-http-key-01", final.headers["ETag"]),
    )
    assert force.status_code == 409 and force.json()["error"]["code"] == "cancel_mode_unsupported"
    done = c.post(
        f"/api/v1/setup-operations/{op['id']}/cancel",
        json={"schema_version": 1, "mode": "cooperative"},
        headers=h("cancel-http-key-02", final.headers["ETag"]),
    )
    assert done.status_code == 200 and done.json()["status"] == "succeeded"
    retry = c.post(
        f"/api/v1/setup-operations/{op['id']}/retry",
        json={"schema_version": 1},
        headers=h("retry-http-key-01", final.headers["ETag"]),
    )
    assert retry.status_code == 409 and retry.json()["error"]["code"] == "invalid_transition"


def test_setup_http_rejections_are_fixed_codes(setup_client: TestClient) -> None:
    c = setup_client
    bad = c.post(
        "/api/v1/setup-plans",
        json={**PLAN, "model_refs": ["openvino-genai:large-v3-turbo-int8"]},
        headers=h("plan-http-key-0101"),
    )
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "unsupported_combination"
    unknown = c.post(
        "/api/v1/setup-plans", json={**PLAN, "model_refs": ["x:y"]}, headers=h("plan-http-key-0102")
    )
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "model_unknown"
    typo = c.post("/api/v1/setup-plans", json={**PLAN, "extra": 1}, headers=h("plan-http-key-0103"))
    assert typo.status_code == 422
    nokey = c.post("/api/v1/setup-plans", json=PLAN)
    assert nokey.status_code == 400
    assert c.get("/api/v1/setup-plans/plan_" + "0" * 32).status_code == 404
    assert c.get("/api/v1/setup-operations/not-an-id").status_code == 404
    created = c.post("/api/v1/setup-plans", json=PLAN, headers=h("plan-http-key-0104")).json()
    gone = c.delete(f"/api/v1/setup-plans/{created['id']}", headers={"If-Match": '"1"'})
    assert gone.status_code == 204
    assert c.get(f"/api/v1/setup-plans/{created['id']}").status_code == 404
    origin = c.post(
        "/api/v1/setup-plans",
        json=PLAN,
        headers={**h("plan-http-key-0105"), "Origin": "http://evil.example"},
    )
    assert origin.status_code == 403


def test_legacy_wizard_is_refused_while_v1_setup_runtime_is_active(setup_client) -> None:
    c = setup_client
    response = c.post("/api/wizard/jobs", json={"kind": "venv_build", "profile": "cpu"})
    assert response.status_code == 409  # legacy queue is closed: no parallel writer
