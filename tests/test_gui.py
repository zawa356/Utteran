from __future__ import annotations

import ast
import json
import platform
import threading
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.app import bind_loopback_socket
from utteran_gui.cli import CliAdapter, TranscriptionOptions
from utteran_gui.environment import EnvironmentService, derive_options
from utteran_gui.jobs import JobManager, guidance_for, parse_progress_line
from utteran_gui.security import mask_secrets
from utteran_gui.settings import GuiSettings, SettingsStore, TokenStore


class FakeKeyring:
    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, _service: str, _username: str) -> str | None:
        return self.value

    def set_password(self, _service: str, _username: str, password: str) -> None:
        self.value = password

    def delete_password(self, _service: str, _username: str) -> None:
        self.value = None


def _create_profile(repo: Path, name: str) -> Path:
    if platform.system() == "Windows":
        executable = repo / ".venvs" / f"win-{name}" / "Scripts" / "utteran.exe"
    else:
        executable = repo / ".venvs" / f"linux-{name}" / "bin" / "utteran"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    return executable


def test_gui_package_never_imports_core_package() -> None:
    root = Path(__file__).parents[1] / "src" / "utteran_gui"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name != "utteran" and not alias.name.startswith("utteran.")
                    for alias in node.names
                )
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module != "utteran" and not node.module.startswith("utteran.")


def test_settings_round_trip_and_token_is_never_returned(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    expected = GuiSettings("light", "en", "cuda", "C:/in", "C:/out")
    store.save(expected)
    assert store.load() == expected
    assert "C:/private-file.wav" not in store.path.read_text(encoding="utf-8")

    backend = FakeKeyring()
    tokens = TokenStore(backend)
    tokens.set("hf_gui_private_token")
    assert tokens.is_configured()
    assert backend.value == "hf_gui_private_token"
    assert mask_secrets("value=hf_gui_private_token") == "value=hf_****"


def test_session_key_is_required_for_every_api_request(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    token_store = TokenStore(FakeKeyring())
    app = create_app(
        "session-secret",
        repo_root=tmp_path,
        settings_store=settings,
        token_store=token_store,
    )
    client = TestClient(app)

    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers={SESSION_HEADER: "wrong"}).status_code == 401
    response = client.get("/api/settings", headers={SESSION_HEADER: "session-secret"})
    assert response.status_code == 200
    assert response.json()["token_configured"] is False

    launched = client.get("/launch?session=session-secret", follow_redirects=False)
    assert launched.status_code == 303
    assert "HttpOnly" in launched.headers["set-cookie"]
    assert client.get("/api/settings").status_code == 200


def test_loopback_socket_uses_os_assigned_port() -> None:
    server_socket = bind_loopback_socket()
    try:
        host, port = server_socket.getsockname()
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
    finally:
        server_socket.close()


def test_theme_and_language_assets_are_externalized() -> None:
    web = Path(__file__).parents[1] / "src" / "utteran_gui" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    styles = (web / "styles.css").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")
    translations = (web / "i18n.js").read_text(encoding="utf-8")

    assert 'data-theme="dark"' in index
    assert 'id="theme-select"' in index and 'id="ui-language"' in index
    assert 'html[data-theme="light"]' in styles
    assert "dataset.theme" in script and 'api("/api/settings"' in script
    assert "window.UTTERAN_I18N" in translations
    assert "ja:" in translations and "en:" in translations


def test_command_builder_uses_profile_executable_and_argument_array(tmp_path: Path) -> None:
    executable = _create_profile(tmp_path, "cuda")
    cli = CliAdapter(tmp_path)
    options = TranscriptionOptions(
        input_path="input with space.wav",
        output_dir="output with space",
        profile="cuda",
        asr_backend="faster-whisper",
        asr_model="large-v3-turbo",
        asr_device="cuda:0",
        diarization_enabled=False,
        formats=("srt", "json"),
        resume_mode="fresh",
    )

    command, environment = cli.build_transcribe_command(options)

    assert command[0] == str(executable)
    assert command[1:3] == ["transcribe", "input with space.wav"]
    assert "--progress-json" in command
    assert "--quiet" in command
    assert "--no-diarization" in command
    assert "--no-resume" in command
    assert environment["UTTERAN_PROFILE"] == "cuda"


def test_dynamic_choices_exclude_unusable_models_and_devices() -> None:
    devices = {
        "backends": {"faster-whisper": True, "whisper-cpp": True, "pyannote": True},
        "ctranslate2": {
            "available": True,
            "cuda_devices": [
                {"index": 0, "name": "GPU 0", "usable": True},
                {"index": 1, "name": "GPU 1", "usable": False},
            ],
        },
        "pytorch": {
            "available": True,
            "cuda_devices": [{"index": 0, "name": "GPU 0", "usable": True}],
            "xpu_devices": [],
        },
        "native": {"variants": {"cpu": True, "vulkan": False}},
    }
    models = [
        {"backend": "faster-whisper", "model_id": "ready", "key": "fw:ready", "installed": True},
        {
            "backend": "faster-whisper",
            "model_id": "missing",
            "key": "fw:missing",
            "installed": False,
        },
        {"backend": "pyannote", "model_id": "diar", "key": "py:diar", "installed": True},
    ]
    options = derive_options(devices, models, {"runnable": {"cpu": True}})

    faster = options["asr"][0]  # type: ignore[index]
    assert [model["id"] for model in faster["models"]] == ["ready"]
    assert [device["id"] for device in faster["devices"]] == ["cpu", "cuda:0"]
    assert all(device["id"] != "cuda:1" for device in faster["devices"])


def test_environment_reads_all_state_from_profile_json_contracts(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _create_profile(tmp_path, "cuda")
    cli = CliAdapter(tmp_path)
    calls: list[tuple[str, tuple[str, ...]]] = []

    def run_json(profile: str, arguments: list[str], *, timeout: float = 60.0) -> object:
        del timeout
        calls.append((profile, tuple(arguments)))
        if arguments == ["profiles", "list", "--json"]:
            return {
                "profiles": [
                    {
                        "name": "cuda",
                        "exists": True,
                        "updated_at": "2026-08-09T00:00:00Z",
                    }
                ]
            }
        if arguments == ["devices", "--json"]:
            return {"backends": {}}
        if arguments == ["models", "list", "--json"]:
            return []
        if arguments == ["native", "status", "--json"]:
            return {"runnable": {}}
        raise AssertionError(arguments)

    monkeypatch.setattr(cli, "run_json", run_json)
    snapshot = EnvironmentService(cli).snapshot("cuda")

    assert snapshot["active_profile"] == "cuda"
    assert snapshot["errors"] == []
    assert calls == [
        ("cuda", ("profiles", "list", "--json")),
        ("cuda", ("devices", "--json")),
        ("cuda", ("models", "list", "--json")),
        ("cuda", ("native", "status", "--json")),
    ]


def test_progress_parser_keeps_invalid_or_partial_lines_as_raw() -> None:
    assert parse_progress_line('{"event":"progress"') is None
    parsed = parse_progress_line('{"event":"warning","message":"hf_privatevalue"}\n')
    assert parsed == {"event": "warning", "message": "hf_****"}


def test_memory_budget_error_has_specific_guidance() -> None:
    guidance = guidance_for(
        3,
        [],
        [{"event": "error", "error_type": "MemoryBudgetError", "message": "budget exceeded"}],
    )
    assert guidance == {"key": "memory", "settings_anchor": ""}


class FakeProcess:
    pid = 12345

    def __init__(self) -> None:
        self.stdout: list[str] = []
        self.stderr = iter(
            [json.dumps({"schema_version": 1, "event": "stage_start", "stage": "audio"}) + "\n"]
        )
        self.returncode: int | None = None
        self.finished = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self.finished.wait(timeout):
            raise TimeoutError
        return self.returncode or 0


def test_job_manager_cancel_uses_injected_tree_killer(tmp_path: Path) -> None:
    _create_profile(tmp_path, "cuda")
    process = FakeProcess()
    killed: list[int] = []

    def popen(_command: list[str], **_kwargs: Any) -> Any:
        return process

    def kill(fake: Any) -> None:
        killed.append(fake.pid)
        fake.returncode = 1
        fake.finished.set()

    manager = JobManager(CliAdapter(tmp_path), popen_factory=popen, tree_killer=kill)
    started = manager.start(
        TranscriptionOptions(
            input_path="in.wav",
            output_dir="out",
            profile="cuda",
            asr_backend="faster-whisper",
            asr_model="large-v3-turbo",
            asr_device="cuda:0",
            diarization_enabled=False,
        )
    )
    deadline = time.monotonic() + 2
    while (
        manager.snapshot(str(started["id"]))["status"] == "starting" and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    manager.cancel(str(started["id"]))
    while (
        manager.snapshot(str(started["id"]))["status"] not in {"cancelled", "failed"}
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)

    final = manager.snapshot(str(started["id"]))
    assert killed == [12345]
    assert final["status"] == "cancelled"
    assert final["exit_code"] == 130
