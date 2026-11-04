"""UI-1 characterization of the legacy (default GUI) job SSE stream.

The legacy ``/api/jobs/{id}/events`` route writes ``id: N`` on every frame but always
restarts from cursor 0, so a browser ``EventSource`` reconnect (which sends
``Last-Event-ID``) replays every earlier event. The UI-1 audit records this in
``docs/architecture/ui1/defects-and-repros.md`` (D-08). The opt-in B9 ``/api/v1/events``
stream already resumes from ``after=``/``Last-Event-ID``; the legacy stream is not
changed in UI-1. The xfail is strict, so fixing the legacy stream makes this test fail
until the marker is removed.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.jobs import JobManager
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.settings import SettingsStore


class _TerminalJobs:
    """Minimal stand-in for JobManager: one finished job with three retained events."""

    def __init__(self) -> None:
        self.queue = OperationQueue()
        self.events: list[dict[str, object]] = [
            {"event": "stage_start", "stage": "asr", "id": 0},
            {"event": "stage_done", "stage": "asr", "id": 1},
            {"event": "done", "exit_code": 0, "id": 2},
        ]

    def snapshot(self, job_id: str) -> dict[str, object]:
        return {"id": job_id, "status": "completed"}

    def events_since(self, job_id: str, cursor: int) -> tuple[list[dict[str, object]], bool]:
        return [dict(item) for item in self.events[cursor:]], True

    def shutdown(self) -> None:
        return None


def _frame_ids(body: str) -> list[int]:
    return [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        "session-secret",
        repo_root=tmp_path,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        job_manager=cast(JobManager, _TerminalJobs()),
    )
    return TestClient(app, headers={SESSION_HEADER: "session-secret"})


def test_legacy_job_stream_numbers_frames_from_zero(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/jobs/job-1/events")
    assert response.status_code == 200
    assert _frame_ids(response.text) == [0, 1, 2]


@pytest.mark.xfail(
    strict=True,
    reason="UI-1 D-08: legacy job SSE ignores Last-Event-ID and replays from 0 on reconnect",
)
def test_legacy_job_stream_resumes_after_last_event_id(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/jobs/job-1/events", headers={"Last-Event-ID": "1"})
    assert response.status_code == 200
    assert _frame_ids(response.text) == [2]
