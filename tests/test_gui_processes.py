from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from utteran_gui import api, hardware, processes
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.cli import CliAdapter

NO_WINDOW = 0x08000000
NEW_PROCESS_GROUP = 0x00000200


def _simulate_windows(monkeypatch: Any) -> None:
    monkeypatch.setattr(processes.os, "name", "nt")
    monkeypatch.setattr(processes.subprocess, "CREATE_NO_WINDOW", NO_WINDOW, raising=False)
    monkeypatch.setattr(
        processes.subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        NEW_PROCESS_GROUP,
        raising=False,
    )


def _create_profile(repo: Path, profile: str = "cpu") -> None:
    executable = repo / ".venvs" / f"win-{profile}" / "Scripts" / "utteran.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")


def test_job_launch_combines_no_window_with_new_process_group(
    tmp_path: Path, monkeypatch: Any
) -> None:
    _simulate_windows(monkeypatch)

    kwargs = processes.build_popen_kwargs(cwd=tmp_path, env={})

    assert kwargs["creationflags"] == NO_WINDOW | NEW_PROCESS_GROUP


def test_taskkill_uses_no_window(monkeypatch: Any) -> None:
    _simulate_windows(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    class RunningProcess:
        pid = 12345

        @staticmethod
        def poll() -> None:
            return None

    monkeypatch.setattr(processes.subprocess, "run", fake_run)

    processes.kill_process_tree(RunningProcess())  # type: ignore[arg-type]

    assert captured["creationflags"] == NO_WINDOW


def test_profile_cli_run_uses_no_window(tmp_path: Path, monkeypatch: Any) -> None:
    _simulate_windows(monkeypatch)
    monkeypatch.setattr("utteran_gui.cli.platform.system", lambda: "Windows")
    _create_profile(tmp_path)
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr("utteran_gui.cli.subprocess.run", fake_run)

    assert CliAdapter(tmp_path).run_json("cpu", ["devices", "--json"]) == {}
    assert captured["creationflags"] == NO_WINDOW


def test_hardware_powershell_probe_uses_no_window(monkeypatch: Any) -> None:
    _simulate_windows(monkeypatch)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Windows")
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.update(kwargs)
        payload = json.dumps({"Name": "Intel Arc", "AdapterCompatibility": "Intel"})
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")

    monkeypatch.setattr(hardware.subprocess, "run", fake_run)

    assert hardware.detect_gpu().dominant_vendor == "intel"
    assert captured["creationflags"] == NO_WINDOW


def test_open_folder_uses_no_window(tmp_path: Path, monkeypatch: Any) -> None:
    app = create_app("session-secret", repo_root=tmp_path)
    client = TestClient(app, headers={SESSION_HEADER: "session-secret"})
    _simulate_windows(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_popen(command: list[str], **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(api.subprocess, "Popen", fake_popen)

    response = client.post("/api/open-folder", json={"path": str(tmp_path)})

    assert response.status_code == 204
    assert captured["creationflags"] == NO_WINDOW


def test_non_windows_creation_kwargs_are_empty(monkeypatch: Any) -> None:
    monkeypatch.setattr(processes.os, "name", "posix")

    assert processes.build_creation_kwargs() == {}
    assert processes.build_creation_kwargs(new_process_group=True) == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree cancellation regression")
def test_real_hidden_process_tree_can_still_be_cancelled(tmp_path: Path) -> None:
    process = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Start-Sleep 60"],
        **processes.build_popen_kwargs(cwd=tmp_path, env=dict(os.environ)),
    )
    try:
        processes.kill_process_tree(process)
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()

    assert process.poll() is not None
