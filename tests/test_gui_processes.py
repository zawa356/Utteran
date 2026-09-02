from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from utteran_gui import api, hardware, processes
from utteran_gui import app as gui_app
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.cli import CliAdapter

NO_WINDOW = 0x08000000
NEW_PROCESS_GROUP = 0x00000200


def _simulate_windows(monkeypatch: Any) -> None:
    windows_os = SimpleNamespace(name="nt")
    monkeypatch.setattr(processes, "os", windows_os)
    monkeypatch.setattr(api, "os", windows_os)
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

    class FakeProcess:
        returncode = 0

        def __init__(self, command: list[str], **kwargs: Any) -> None:
            self.args = command
            captured.update(kwargs)

        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            return "{}", ""

        # `cli.py` casts through `subprocess.Popen[str]` for typing, which
        # evaluates the subscript at runtime; replacing `subprocess.Popen`
        # module-wide means that subscript now targets this fake class.
        @classmethod
        def __class_getitem__(cls, _item: object) -> type[FakeProcess]:
            return cls

    # CliAdapter runs the profile CLI through Popen + communicate (not
    # subprocess.run) so a timeout can tree-kill any isolated device-probe
    # grandchild instead of leaking it - see AISTATE.md Phase 5l.
    monkeypatch.setattr("utteran_gui.cli.subprocess.Popen", FakeProcess)

    assert CliAdapter(tmp_path).run_json("cpu", ["devices", "--json"]) == {}
    assert captured["creationflags"] == NO_WINDOW


def test_profile_manifest_detects_current_and_stale_dependencies(tmp_path: Path) -> None:
    _create_profile(tmp_path)
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    cli = CliAdapter(tmp_path)

    missing = cli.profile_info("cpu")
    assert missing.compatible is False
    assert missing.compatibility_reason == "profile_manifest_missing"

    manifest = missing.path / ".utteran-profile.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": "cpu",
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
                "extras": ["cpu", "japanese"],
            }
        ),
        encoding="utf-8",
    )
    assert cli.profile_info("cpu").compatible is True

    lock.write_text("version = 2\n", encoding="utf-8")
    changed = cli.profile_info("cpu")
    assert changed.compatible is False
    assert changed.compatibility_reason == "dependency_lock_changed"


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
    monkeypatch.setattr(processes, "os", SimpleNamespace(name="posix"))

    assert processes.build_creation_kwargs() == {}
    assert processes.build_creation_kwargs(new_process_group=True) == {}


def test_uninstaller_command_deletes_only_the_saved_token(monkeypatch: Any) -> None:
    cleared: list[bool] = []

    class FakeTokenStore:
        @staticmethod
        def clear() -> None:
            cleared.append(True)

    monkeypatch.setattr(gui_app, "TokenStore", FakeTokenStore)
    monkeypatch.setattr(gui_app.sys, "argv", ["utteran-gui.exe", "--delete-keyring-token"])

    gui_app.main()

    assert cleared == [True]


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


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object regression")
def test_supervisor_shutdown_terminates_a_real_child(tmp_path: Path) -> None:
    supervisor = processes.ProcessSupervisor()
    process = supervisor.popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        **processes.build_popen_kwargs(cwd=tmp_path, env=dict(os.environ)),
    )

    supervisor.shutdown()

    assert process.poll() is not None
    assert supervisor.active_pids() == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object parent-death regression")
def test_supervisor_job_closes_after_forced_parent_exit(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    helper = tmp_path / "job_parent.py"
    helper.write_text(
        "\n".join(
            [
                "import os, sys, time",
                "from pathlib import Path",
                "from utteran_gui.processes import ProcessSupervisor, build_popen_kwargs",
                "supervisor = ProcessSupervisor()",
                "child = supervisor.popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
                "**build_popen_kwargs(cwd=Path.cwd(), env=dict(os.environ)))",
                "Path(sys.argv[1]).write_text(str(child.pid), encoding='ascii')",
                "time.sleep(60)",
            ]
        ),
        encoding="utf-8",
    )
    parent = subprocess.Popen(
        [sys.executable, str(helper), str(pid_file)],
        cwd=Path(__file__).parents[1],
        env=dict(os.environ),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    deadline = time.monotonic() + 10
    while not pid_file.is_file() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pid_file.is_file()
    child_pid = int(pid_file.read_text(encoding="ascii"))

    parent.kill()
    parent.wait(timeout=10)
    deadline = time.monotonic() + 10
    while _process_exists(child_pid) and time.monotonic() < deadline:
        time.sleep(0.05)

    assert not _process_exists(child_pid)


def _process_exists(pid: int) -> bool:
    try:
        process = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        return False
    return str(pid) in process.stdout
