"""The UI-3 S3-A E2E harness drives the *real* legacy job routes (no model, no ASR).

``tools/ui2/serve_next_ui.py`` builds ``create_app`` with the real ``JobManager`` and
``OperationQueue`` on top of ``tools/ui2/synthetic_jobs.py`` (a synthetic transcribe process).
These tests pin the HTTP contract the frontend relies on: 202 is not terminal, ``GET`` is the
status authority, ``done`` alone is not success, cancel is a request, and refusals create no job.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "ui2"


def _load(name: str) -> ModuleType:
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    from utteran_gui.api import SESSION_HEADER, create_app
    from utteran_gui.settings import SettingsStore

    environment = _load("synthetic_environment")
    jobs = _load("synthetic_jobs")
    app = create_app(
        "s3a-session",
        repo_root=tmp_path,
        settings_store=SettingsStore(tmp_path / "gui-settings.json"),
        environment_service=environment.synthetic_environment_service(delays=False),
        job_manager=jobs.synthetic_job_manager(tmp_path),
    )
    with TestClient(app, headers={SESSION_HEADER: "s3a-session"}) as test_client:
        yield test_client


def _body(name: str, **patch: Any) -> dict[str, Any]:
    # Synthetic, relative-looking placeholder paths: nothing is read or written there.
    body: dict[str, Any] = {
        "input_path": f"synthetic-media/{name}",
        "output_dir": "synthetic-out",
        "profile": "cuda",
        "asr_backend": "faster-whisper",
        "asr_model": "small",
        "asr_device": "cpu",
        "diarization_enabled": False,
        "language": "ja",
        "formats": ["srt", "json"],
        "resume_mode": "resume",
        "recursive": False,
    }
    body.update(patch)
    return body


def _wait(
    client: TestClient, job_id: str, statuses: set[str], timeout: float = 30.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if snapshot["status"] in statuses:
            return snapshot
        time.sleep(0.1)
    raise AssertionError(f"job did not reach {statuses}")


def test_accepted_job_is_not_terminal_and_completes_through_get(client: TestClient) -> None:
    response = client.post("/api/jobs", json=_body("meeting.wav"))
    assert response.status_code == 202
    started = response.json()
    assert started["status"] in {"starting", "running"}
    done = _wait(client, started["id"], {"completed", "failed", "cancelled"})
    assert done["status"] == "completed"
    assert [Path(p).name for p in done["outputs"]] == ["meeting.srt", "meeting.json"]
    assert any(e.get("event") == "progress" and e.get("stage") == "asr" for e in done["events"])
    assert any("synthetic raw log" in line for line in done["logs"])  # dropped by the UI DTO
    queue = client.get("/api/queue").json()
    assert [(item["kind"], item["status"]) for item in queue] == [("transcription", "completed")]


def test_done_event_alone_is_not_success(client: TestClient) -> None:
    job_id = client.post("/api/jobs", json=_body("lying-done.wav")).json()["id"]
    final = _wait(client, job_id, {"completed", "failed", "cancelled"})
    assert any(e.get("event") == "done" and e.get("exit_code") == 0 for e in final["events"])
    assert final["status"] == "failed"
    assert final["exit_code"] == 3


def test_failure_has_a_fixed_guidance_key(client: TestClient) -> None:
    job_id = client.post("/api/jobs", json=_body("fail-input.wav")).json()["id"]
    final = _wait(client, job_id, {"completed", "failed", "cancelled"})
    assert final["status"] == "failed"
    assert final["guidance"]["key"] == "input"


def test_cancel_is_a_request_until_get_reports_cancelled(client: TestClient) -> None:
    job_id = client.post("/api/jobs", json=_body("slow.wav")).json()["id"]
    _wait(client, job_id, {"running"})
    response = client.post(f"/api/jobs/{job_id}/cancel")
    assert response.status_code == 202
    final = _wait(client, job_id, {"cancelled", "completed", "failed"})
    assert final["status"] == "cancelled"
    assert final["guidance"]["key"] == "cancelled"
    # Cancelling a finished job again is harmless and keeps the terminal state.
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelled"


def test_refusals_create_no_job(client: TestClient) -> None:
    assert (
        client.post("/api/jobs", json=_body("meeting.wav", asr_model="large-v3")).status_code == 409
    )
    assert client.post("/api/jobs", json=_body("meeting.wav", profile="cpu")).status_code == 409
    rejected = client.post("/api/jobs", json=_body("reject.wav"))
    assert rejected.status_code == 400
    assert "synthetic-profile" in rejected.json()["detail"]  # the UI never shows this text
    assert client.post("/api/jobs", json=_body("meeting.wav", output_dir="")).status_code == 422
    assert client.post("/api/jobs", json=_body("meeting.wav", formats=[])).status_code == 422
    assert client.get("/api/queue").json() == []
    assert client.get("/api/jobs/" + "0" * 32).status_code == 404


def test_post_remembers_folders_as_settings_defaults(client: TestClient) -> None:
    """Existing legacy side effect of an accepted job (documented in S3-A, unchanged)."""
    job_id = client.post("/api/jobs", json=_body("meeting.wav")).json()["id"]
    _wait(client, job_id, {"completed", "failed", "cancelled"})
    settings = client.get("/api/settings").json()
    assert settings["default_output_dir"] == "synthetic-out"
    assert settings["default_input_dir"] == "synthetic-media"
