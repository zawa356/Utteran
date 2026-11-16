"""UI-3 S5-A: the server side of the --next-ui-v1 single-job path (synthetic only).

* The Preset template the Vue UI offers for an *explicit* first Preset is a legal B5
  ``PresetWrite``, passes B5 semantic validation, and equals the legacy core defaults except for
  a declared list of differences (diarization off for the B3 worker, explicit backend, no prompt).
* The real FastAPI app with B5+B7 mounted serves the whole create -> validate -> resolve ->
  submit -> GET flow the UI uses, through the same session/Host/Origin middleware.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_b7_api import AUTHORITY, runtime_for
from test_b7_jobs import SESSION, Env
from utteran.config import Config
from utteran.config_adapter import ExecutionContext, adapt_settings
from utteran_contracts.editing import EditingSession
from utteran_contracts.settings import PolicyWrite, PresetWrite, ProcessingSettings
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.operation_queue import OperationQueue
from utteran_settings.inheritance import semantic_issues

ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "frontend" / "src" / "domain" / "v1" / "presetTemplate.json"
ORIGIN = "http://" + AUTHORITY

# Leaves where the template deliberately differs from ``Config.model_validate({})``.
# Each is visible on the confirmation step of the UI before anything is saved.
DECLARED_DIFFERENCES = {
    # The B3 worker runs only faster-whisper without diarization (B7 WORKER_RECIPES).
    ("asr", "backend"),
    ("diarization", "enabled"),
    # Resolved per run by B4/B7, not by the Preset (adapter context).
    ("asr", "device"),
    ("asr", "compute_type"),
    ("diarization", "device"),
    # Legacy "" and structured null both mean "no prompt".
    ("asr", "initial_prompt"),
}


def template() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    return data


def test_template_is_a_valid_preset_write_and_passes_semantic_validation() -> None:
    write = PresetWrite.model_validate(
        {"schema_version": 1, "name": "S5-A 合成", "settings": template()}
    )
    assert semantic_issues(write.settings, set()) == ()
    assert write.settings.diarization.enabled is False
    assert write.settings.asr.glossary == () and write.settings.asr.extra_arguments is None


def test_template_equals_core_defaults_except_declared_differences() -> None:
    settings = ProcessingSettings.model_validate(template())
    base = Config.model_validate({})
    context = ExecutionContext(base, "faster-whisper", "large-v3-turbo", "cpu", "cpu", "int8")
    adapted = adapt_settings(settings, context).config.model_dump(mode="json")
    default = base.model_dump(mode="json")
    differences = {
        (section, key)
        for section, values in default.items()
        if isinstance(values, dict)
        for key, value in values.items()
        if adapted[section][key] != value
    }
    assert differences <= DECLARED_DIFFERENCES, differences - DECLARED_DIFFERENCES
    assert adapted["asr"]["initial_prompt"] in (None, "")


def test_policy_template_from_the_instructions_is_valid() -> None:
    PolicyWrite.model_validate(
        {
            "schema_version": 1,
            "name": "S5-A CPU",
            "mode": "auto",
            "fixed_candidate_id": None,
            "allowed_profile_ids": ["cpu"],
            "objective": "balanced",
            "max_memory_bytes": None,
            "fallback": "none",
            "execution_deadline_seconds": None,
        }
    )


@pytest.fixture
def v1(tmp_path: Path):
    env = Env(tmp_path, None)
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
    # Cookie + same Origin, like the browser's fetch(credentials: "same-origin"). No header key.
    with TestClient(app, base_url=ORIGIN, cookies={"utteran_gui_session": SESSION}) as client:
        client.env = env  # type: ignore[attr-defined]
        yield client


def post(client: TestClient, path: str, body: Any, key: str, **headers: str):
    return client.post(
        path,
        json=body,
        headers={"Origin": ORIGIN, "Idempotency-Key": key, **headers},
    )


def test_ui_flow_create_select_validate_resolve_submit_get(v1: TestClient) -> None:
    env: Env = v1.env  # type: ignore[attr-defined]
    settings = template()
    settings["asr"]["model"] = "large-v3-turbo"
    created = post(
        v1,
        "/api/v1/presets",
        {"schema_version": 1, "name": "S5-A", "settings": settings},
        "ui-s5a-preset-000001",
    )
    assert created.status_code == 201, created.text
    preset = created.json()
    listed = v1.get("/api/v1/presets").json()
    assert preset["id"] in {item["id"] for item in listed["items"]}
    frozen = v1.get(f"/api/v1/presets/{preset['id']}?revision={preset['revision']}")
    assert frozen.status_code == 200 and frozen.json()["settings"] == preset["settings"]

    session = {
        "schema_version": 1,
        "preset_ref": {"id": preset["id"], "revision": preset["revision"]},
        "policy_ref": env.policy_ref,
        "common_overrides": [{"path": "/output/formats", "value": ["srt"]}],
        "files": [
            {
                "client_file_id": "f-0123456789abcdef",
                "input_path": str(env.file("a.wav")),
                "output_dir": str(env.output),
                "overrides": [],
            }
        ],
    }
    EditingSession.model_validate(session)
    checked = post(v1, "/api/v1/validations", session, "ui-s5a-validate-0001")
    assert checked.status_code == 200, checked.text
    effective = checked.json()["files"][0]["effective_settings"]
    # The UI computes the same effective settings locally (preset + overrides, leaf replace).
    expected = json.loads(json.dumps(preset["settings"]))
    expected["output"]["formats"] = ["srt"]
    assert effective == expected

    resolved = post(v1, "/api/v1/execution-resolutions", session, "ui-s5a-resolve-00001")
    assert resolved.status_code == 201, resolved.text
    resolution = resolved.json()
    body = {
        "schema_version": 1,
        "resolution_token": resolution["token"],
        "editing_session": session,
        "resume_mode": "resume",
    }
    accepted = post(v1, "/api/v1/jobs", body, "ui-s5a-submit-000001")
    assert accepted.status_code == 202, accepted.text
    job = accepted.json()["jobs"][0]
    # Replay of the same key + same frozen body: the original answer (status 202 reproduced,
    # B7 queue-and-transaction.md), the same job, no second one.
    replay = post(v1, "/api/v1/jobs", body, "ui-s5a-submit-000001")
    assert replay.status_code == 202 and replay.json() == accepted.json()
    assert len(v1.get("/api/v1/jobs").json()["items"]) == 1
    # Same key, different body: conflict, never a second job.
    other = dict(body, resume_mode="fresh")
    assert post(v1, "/api/v1/jobs", other, "ui-s5a-submit-000001").status_code == 409
    got = v1.get(f"/api/v1/jobs/{job['id']}")
    assert got.status_code == 200 and got.json()["status"] == "queued"
    assert got.headers["etag"] == f'"{got.json()["revision"]}"'
    queue = v1.get("/api/v1/queue")
    assert queue.json()["dispatch"] == "paused"  # recovery/initial state; never auto-resumed
    # Public projections carry no path, prompt or token.
    text = got.text + queue.text + v1.get("/api/v1/jobs").text
    assert str(env.tmp) not in text and resolution["token"] not in text


def test_cookie_only_write_without_origin_is_refused(v1: TestClient) -> None:
    response = v1.post(
        "/api/v1/presets",
        json={"schema_version": 1, "name": "x", "settings": template()},
        headers={"Idempotency-Key": "ui-s5a-preset-000002"},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "origin_required"
    foreign = v1.post(
        "/api/v1/presets",
        json={"schema_version": 1, "name": "x", "settings": template()},
        headers={"Idempotency-Key": "ui-s5a-preset-000003", "Origin": "http://127.0.0.1:1"},
    )
    assert foreign.status_code == 403
    assert v1.get("/api/v1/presets", cookies={"utteran_gui_session": "wrong"}).status_code == 401
    assert v1.get("/api/v1/presets", headers={"Host": "localhost:1"}).status_code == 403
    _ = SESSION_HEADER  # the header path is the non-browser alternative; not used by the UI
