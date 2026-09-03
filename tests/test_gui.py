from __future__ import annotations

import ast
import json
import logging
import platform
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from utteran_gui import __version__
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.app import NativeDialogApi, bind_loopback_socket
from utteran_gui.cli import CliAdapter, RegenerationOptions, TranscriptionOptions
from utteran_gui.environment import (
    EnvironmentService,
    _profile_warning,
    annotate_model_capabilities,
    derive_options,
)
from utteran_gui.jobs import JobManager, guidance_for, parse_progress_line
from utteran_gui.logging_runtime import _JsonFormatter
from utteran_gui.security import mask_secrets
from utteran_gui.settings import GuiSettings, SettingsStore, TokenStore, TokenStoreUnavailable
from utteran_gui.setup_wizard import SetupWizardService


class FakeKeyring:
    def __init__(self) -> None:
        self.value: str | None = None

    def get_password(self, _service: str, _username: str) -> str | None:
        return self.value

    def set_password(self, _service: str, _username: str, password: str) -> None:
        self.value = password

    def delete_password(self, _service: str, _username: str) -> None:
        self.value = None


class UnavailableKeyring(FakeKeyring):
    def get_password(self, _service: str, _username: str) -> str | None:
        raise RuntimeError("credential vault unavailable")

    def set_password(self, _service: str, _username: str, password: str) -> None:
        del password
        raise RuntimeError("credential vault unavailable")


def _create_profile(repo: Path, name: str) -> Path:
    if platform.system() == "Windows":
        executable = repo / ".venvs" / f"win-{name}" / "Scripts" / "utteran.exe"
    else:
        executable = repo / ".venvs" / f"linux-{name}" / "bin" / "utteran"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    return executable


_FORBIDDEN_MODULES = (
    "utteran",
    "torch",
    "torchaudio",
    "openvino",
    "ctranslate2",
    "faster_whisper",
    "pyannote",
    "onnxruntime",
    "sherpa_onnx",
)


def test_gui_package_never_imports_core_package_or_inference_deps() -> None:
    """The GUI (`.venvs/win-gui`) never installs inference packages (Phase 5c

    指示書 Step 2: hardware detection must not pull torch/openvino in just to
    probe the machine before any profile venv exists), so no module under
    utteran_gui may import them, in addition to the pre-existing `utteran`
    core-package prohibition.
    """
    root = Path(__file__).parents[1] / "src" / "utteran_gui"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not _is_forbidden(alias.name), f"{path.name} imports {alias.name}"
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not _is_forbidden(node.module), f"{path.name} imports {node.module}"


def _is_forbidden(module: str) -> bool:
    return any(module == name or module.startswith(f"{name}.") for name in _FORBIDDEN_MODULES)


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


def test_packaged_gui_app_log_masks_tokens() -> None:
    record = logging.LogRecord(
        "gui", logging.ERROR, __file__, 1, "failed hf_gui_app_secret", (), None
    )

    formatted = _JsonFormatter().format(record)

    assert "hf_gui_app_secret" not in formatted
    assert "hf_****" in formatted


def test_missing_theme_migrates_to_system_but_explicit_existing_theme_is_preserved() -> None:
    assert GuiSettings.from_dict({}).theme == "system"
    assert GuiSettings.from_dict({"theme": "dark"}).theme == "dark"
    assert GuiSettings.from_dict({"theme": "light"}).theme == "light"


def test_unavailable_keyring_is_distinct_from_an_unconfigured_keyring() -> None:
    unconfigured = TokenStore(FakeKeyring()).status()
    unavailable = TokenStore(UnavailableKeyring()).status()

    assert unconfigured.available is True
    assert unconfigured.configured is False
    assert unavailable.available is False
    assert unavailable.configured is False
    assert unavailable.error_type == "RuntimeError"

    try:
        TokenStore(UnavailableKeyring()).set("hf_synthetic_test_value")
    except TokenStoreUnavailable:
        pass
    else:
        raise AssertionError("an unavailable keyring must reject token storage")


def test_keyring_diagnostic_uses_isolated_credential_and_never_returns_token() -> None:
    backend = FakeKeyring()
    result = TokenStore(backend).diagnose()

    assert result["import_success"] is True
    assert result["get_success"] is True
    assert result["set_success"] is True
    assert result["delete_success"] is True
    assert backend.value is None
    assert "hf_" not in json.dumps(result)


def test_gui_spec_collects_keyring_modules_and_entry_point_metadata() -> None:
    spec = (Path(__file__).parents[1] / "packaging" / "gui.spec").read_text(encoding="utf-8")
    assert 'collect_submodules("keyring")' in spec
    assert 'copy_metadata("keyring")' in spec


def test_packaging_and_gui_use_the_supplied_icon() -> None:
    project = Path(__file__).parents[1]
    spec = (project / "packaging" / "gui.spec").read_text(encoding="utf-8")
    installer = (project / "packaging" / "installer.iss").read_text(encoding="utf-8")
    index = (project / "src" / "utteran_gui" / "web" / "index.html").read_text(encoding="utf-8")

    assert "icon=APP_ICON" in spec
    assert "SetupIconFile={#RepoRoot}\\icon\\utteran.ico" in installer
    assert installer.count("IconFilename:") == 2
    assert 'class="brand-logo" src="/logo"' in index


def test_native_dialog_returns_one_selected_path_without_persisting() -> None:
    import webview

    calls: list[tuple[object, dict[str, object]]] = []

    class Window:
        @staticmethod
        def create_file_dialog(kind: object, **kwargs: object) -> tuple[str, ...]:
            calls.append((kind, kwargs))
            return ("C:/Media/meeting.wav", "C:/Media/ignored.wav")

    api = NativeDialogApi()
    api._attach_window(Window())

    assert api.choose_path("input_file") == "C:/Media/meeting.wav"
    assert calls[0][0] == webview.FileDialog.OPEN
    assert calls[0][1]["allow_multiple"] is False
    assert api.choose_path("input_folder") == "C:/Media/meeting.wav"
    assert calls[1][0] == webview.FileDialog.FOLDER


def test_native_dialog_bridge_never_exposes_native_window_to_pywebview() -> None:
    api = NativeDialogApi()
    api._attach_window(object())

    public_members = [name for name in dir(api) if not name.startswith("_")]
    assert public_members == ["choose_path", "report_frontend_error"]
    assert api.report_frontend_error({"kind": "error", "message": "test"}) is True
    assert api.report_frontend_error(None) is False


def test_gui_assets_disable_nonfunctional_drop_and_forward_frontend_errors() -> None:
    web = Path(__file__).parents[1] / "src" / "utteran_gui" / "web"
    script = (web / "app.js").read_text(encoding="utf-8")
    index = (web / "index.html").read_text(encoding="utf-8")

    assert "file.path" not in script
    assert "dataTransfer.files" not in script
    assert "window.native" not in script
    assert "dropHint" not in index
    assert 'window.addEventListener("error"' in script
    assert 'window.addEventListener("unhandledrejection"' in script
    assert "report_frontend_error" in script


def test_cli_and_gui_use_the_same_keyring_service_and_username() -> None:
    core_config = (Path(__file__).parents[1] / "src" / "utteran" / "config.py").read_text(
        encoding="utf-8"
    )
    assert TokenStore.SERVICE == "utteran"
    assert TokenStore.USERNAME == "huggingface"
    assert 'keyring.get_password("utteran", "huggingface")' in core_config


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
    assert response.headers["cache-control"] == "no-store"
    version = client.get("/api/version", headers={SESSION_HEADER: "session-secret"})
    assert version.json() == {"version": __version__}

    launched = client.get("/launch?session=session-secret", follow_redirects=False)
    assert launched.status_code == 303
    assert "HttpOnly" in launched.headers["set-cookie"]
    assert client.get("/api/settings").status_code == 200


def test_portable_api_keeps_token_in_session_and_discloses_policy(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("UTTERAN_DISTRIBUTION", "portable")
    monkeypatch.setenv("UTTERAN_TOKEN_MODE", "session")
    app = create_app("session-secret", repo_root=tmp_path)
    client = TestClient(app, headers={SESSION_HEADER: "session-secret"})

    settings = client.get("/api/settings").json()
    assert settings["portable_distribution"] is True
    assert settings["token_session_only"] is True
    saved = client.put("/api/token", json={"token": "hf_process_memory_only"}).json()
    assert saved == {
        "configured": True,
        "available": True,
        "backend": "session",
        "error_type": "",
        "error_message": "",
    }
    assert not (tmp_path / "data" / "config" / "gui-settings.json").exists()


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

    assert 'data-theme="system"' in index
    assert 'id="theme-select"' in index and 'id="ui-language"' in index
    assert 'html[data-theme="light"]' in styles
    assert 'html[data-theme="system"]' in styles
    assert "prefers-color-scheme: light" in styles
    assert '<option value="system"' in index
    assert "dataset.theme" in script and 'api("/api/settings"' in script
    assert "window.UTTERAN_I18N" in translations
    assert "ja:" in translations and "en:" in translations
    assert 'id="result-summary"' in index
    assert 'item.event === "run_summary"' in script
    assert '"run_summary"' in script
    assert "executedStages" in translations and "reusedStages" in translations


def test_workspace_grids_and_logging_controls_cannot_force_horizontal_overflow() -> None:
    web = Path(__file__).parents[1] / "src" / "utteran_gui" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    styles = (web / "styles.css").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")

    assert "repeat(5, minmax(0, 1fr))" in styles
    assert ".stage-list li { min-width: 0; overflow-wrap: anywhere;" in styles
    assert ".stage-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }" in styles
    assert ".token-actions input { width: min(220px, 100%); min-width: 0; }" in styles
    assert ".format-chip { position: relative; }" in styles
    assert ".format-chip input { position: absolute; opacity: 0; }" in styles
    assert 'class="view viewer-view"' not in index
    assert 'id="raw-log-warning"' in index and 'id="open-logs"' in index
    assert "state.settings.raw_subprocess_logs" in script


def test_wizard_assets_wire_up_first_run_flow_and_stay_theme_i18n_aware() -> None:
    web = Path(__file__).parents[1] / "src" / "utteran_gui" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")
    translations = (web / "i18n.js").read_text(encoding="utf-8")

    assert 'id="view-wizard"' in index
    assert 'id="wizard-step-welcome"' in index
    assert 'id="wizard-step-profile"' in index
    assert 'id="wizard-step-diarization"' in index
    assert 'id="wizard-step-model"' in index
    assert 'id="wizard-step-token"' in index
    assert 'id="wizard-step-confirm"' in index
    assert 'id="wizard-step-progress"' in index
    assert 'id="wizard-step-done"' in index
    assert 'id="relaunch-wizard"' in index
    assert 'href="https://huggingface.co/join"' in index
    assert 'href="https://huggingface.co/settings/tokens/new?tokenType=read"' in index
    assert 'id="wizard-token-input" type="password"' in index
    assert 'id="app-version"' in index

    assert 'api("/api/wizard/status")' in script
    assert 'api("/api/wizard/hardware")' in script
    assert '"/api/wizard/jobs"' in script
    assert "if (wizardStatus.first_run) await openWizard()" in script
    assert "wizardShowError" in script and "wizardState.retry" in script

    assert "wizardTitle:" in translations
    assert "wizardTitle:" in translations.split("\n  en: {")[1]
    assert "guide_license:" in translations
    assert 'await saveToken("wizard-token-input")' in script
    assert 'api("/api/version")' in script
    assert "wizardState.resumeExecution ? status.token_error : null" in script
    assert '$("wizard-token-input").value = ""' in script
    assert "error.wizardKind = kind" in script
    assert '["model_download", "smoke_test"].includes(error.wizardKind)' in script
    assert "(current.logs || []).slice(-12)" in script
    assert 'await saveToken("token-input")' in script

    # A profile card's title must show a translated label, not the raw
    # technical identifier ("cpu"/"cuda"/"intel"/"vulkan").
    assert "wizardProfileLabel(alternative.profile)" in script
    assert "title.textContent = alternative.profile +" not in script
    for language_block in (translations.split("\n  en: {")[0], translations.split("\n  en: {")[1]):
        assert "wizardProfileCpu:" in language_block
        assert "wizardProfileCuda:" in language_block
        assert "wizardProfileIntel:" in language_block
        assert "wizardProfileVulkan:" in language_block


def test_settings_partial_updates_preserve_other_changes_and_wizard_state(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    settings.save(
        GuiSettings(
            theme="dark",
            language="ja",
            default_input_dir="C:/before",
            setup_wizard_completed_at="2026-08-24T00:00:00+00:00",
        )
    )
    app = create_app(
        "session-secret",
        repo_root=tmp_path,
        settings_store=settings,
        token_store=TokenStore(FakeKeyring()),
    )
    client = TestClient(app, headers={SESSION_HEADER: "session-secret"})

    first = client.patch("/api/settings", json={"theme": "light"})
    second = client.patch("/api/settings", json={"language": "en"})
    third = client.patch("/api/settings", json={"default_input_dir": "C:/after"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    saved = settings.load()
    assert saved.theme == "light"
    assert saved.language == "en"
    assert saved.default_input_dir == "C:/after"
    assert saved.setup_wizard_completed_at == "2026-08-24T00:00:00+00:00"
    script = (Path(__file__).parents[1] / "src" / "utteran_gui" / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    assert 'method: "PATCH"' in script
    assert "state.settings.language = event.target.value;\n      applySettings();" not in script


def test_input_and_output_directories_are_remembered_independently(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    input_dir = tmp_path / "private-input"
    input_dir.mkdir()
    input_file = input_dir / "must-not-be-saved.wav"
    input_file.touch()
    first_output = tmp_path / "first-output"
    second_output = tmp_path / "second-output"

    settings.save(GuiSettings(default_output_dir=str(first_output)))
    after_input = settings.remember_directories(input_path=str(input_file))

    assert after_input.default_input_dir == str(input_dir)
    assert after_input.default_output_dir == str(first_output)
    assert input_file.name not in settings.path.read_text(encoding="utf-8")

    after_output = settings.remember_directories(output_dir=str(second_output))

    assert after_output.default_input_dir == str(input_dir)
    assert after_output.default_output_dir == str(second_output)


def test_simultaneous_settings_updates_do_not_roll_each_other_back(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    settings.save(GuiSettings())

    with ThreadPoolExecutor(max_workers=2) as executor:
        updates = [
            executor.submit(settings.update, {"theme": "dark"}),
            executor.submit(settings.update, {"language": "en"}),
        ]
        for update in updates:
            update.result()

    saved = settings.load()
    assert saved.theme == "dark"
    assert saved.language == "en"


def test_viewer_assets_use_virtual_rows_and_ime_safe_ephemeral_search() -> None:
    web = Path(__file__).parents[1] / "src" / "utteran_gui" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")

    assert 'id="transcript-viewport"' in index
    assert "ROW_HEIGHT" in script and "renderVirtualRows" in script
    assert "compositionstart" in script and "compositionend" in script
    assert "replaceChildren(...nodes)" in script
    assert 'document.createElement("mark")' in script
    assert "matches.push({ originalIndex, filteredIndex, start: index })" in script
    assert "toLocaleLowerCase" in script
    assert "open.disabled = !job.result_available" not in script
    assert "if (job.result_error) row.title = job.result_error" in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "indexedDB" not in script


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


def test_python_direct_mode_uses_profile_interpreter_and_module(
    monkeypatch: Any, tmp_path: Path
) -> None:
    executable = _create_profile(tmp_path, "cpu")
    python = executable.with_name("python.exe" if platform.system() == "Windows" else "python")
    python.write_text("", encoding="utf-8")
    monkeypatch.setenv("UTTERAN_PYTHON_DIRECT", "1")

    command = CliAdapter(tmp_path).command("cpu", ["devices", "--json"])

    assert command == [str(python), "-m", "utteran", "devices", "--json"]


def test_regeneration_builder_passes_labels_as_shell_free_arguments(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _create_profile(tmp_path, "cuda")
    cli = CliAdapter(tmp_path)
    captured: list[object] = []

    def run_json(profile: str, arguments: list[str], *, timeout: float = 60.0) -> object:
        captured.extend([profile, arguments, timeout])
        return {"executed_stages": ["export"]}

    monkeypatch.setattr(cli, "run_json", run_json)
    response = cli.regenerate(
        RegenerationOptions(
            job_id="0123456789abcdef",
            profile="cuda",
            output_dir="output with space",
            formats=("txt", "json"),
            speaker_labels={"SPEAKER_00": "テスト 話者", "SPEAKER_01": ""},
        )
    )

    assert response == {"executed_stages": ["export"]}
    assert captured[0] == "cuda"
    assert captured[1] == [
        "jobs",
        "export",
        "0123456789abcdef",
        "--output-dir",
        "output with space",
        "--format",
        "txt,json",
        "--speaker-label",
        "SPEAKER_00=テスト 話者",
        "--speaker-label",
        "SPEAKER_01=",
        "--json",
    ]


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


def test_model_gpu_capability_uses_injected_detection_not_quantization() -> None:
    devices = {
        "ctranslate2": {"cuda_devices": []},
        "native": {"variants": {"vulkan": True}},
        "pytorch": {"cuda_devices": [], "xpu_devices": []},
    }
    models = [
        {"backend": "faster-whisper", "model_id": "small", "quantization": None},
        {"backend": "whisper-cpp", "model_id": "base", "quantization": "f16"},
        {"backend": "whisper-cpp", "model_id": "base-q5_0", "quantization": "q5_0"},
    ]

    annotated = annotate_model_capabilities(models, devices, {"runnable": {"vulkan": True}})

    assert annotated[0]["gpu_execution"] is False
    assert annotated[1]["gpu_execution"] is True
    assert annotated[2]["gpu_execution"] is True
    assert "量子化方式" in annotated[2]["recommendation_reason"]


def test_profile_move_warning_preserves_environment_and_requests_rebuild() -> None:
    warning = _profile_warning("cpu", "profile_path_changed")

    assert "保存場所が変わりました" in warning
    assert "既存環境は保持" in warning
    assert "再構築" in warning


def test_environment_reads_all_state_from_profile_json_contracts(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _create_profile(tmp_path, "cuda")
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
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
        if arguments == ["models", "list", "--available", "--all", "--json"]:
            return []
        if arguments == ["native", "status", "--json"]:
            return {"runnable": {}}
        if arguments == ["models", "list-openvino", "--json"]:
            return []
        raise AssertionError(arguments)

    monkeypatch.setattr(cli, "run_json", run_json)
    monkeypatch.setattr(cli, "run_text", lambda *_args, **_kwargs: "C:/models\n")
    snapshot = EnvironmentService(cli).snapshot("cuda")

    assert snapshot["active_profile"] == "cuda"
    assert snapshot["errors"] == []
    assert snapshot["profile_warnings"]
    assert calls == [
        ("cuda", ("profiles", "list", "--json")),
        ("cuda", ("devices", "--json")),
        ("cuda", ("models", "list", "--available", "--all", "--json")),
        ("cuda", ("native", "status", "--json")),
        ("cuda", ("models", "list-openvino", "--json")),
    ]


def test_environment_refresh_forwards_cli_cache_bypass(monkeypatch: Any, tmp_path: Path) -> None:
    _create_profile(tmp_path, "cpu")
    cli = CliAdapter(tmp_path)
    device_calls: list[list[str]] = []

    def run_json(profile: str, arguments: list[str], *, timeout: float = 60.0) -> object:
        del profile, timeout
        if arguments == ["profiles", "list", "--json"]:
            return {"profiles": [{"name": "cpu", "exists": True, "updated_at": None}]}
        if arguments[:2] == ["devices", "--json"]:
            device_calls.append(arguments)
            return {"backends": {}}
        if arguments == ["models", "list", "--available", "--all", "--json"]:
            return []
        if arguments == ["native", "status", "--json"]:
            return {"runnable": {}}
        if arguments == ["models", "list-openvino", "--json"]:
            return []
        raise AssertionError(arguments)

    monkeypatch.setattr(cli, "run_json", run_json)
    monkeypatch.setattr(cli, "run_text", lambda *_args, **_kwargs: "C:/models\n")

    EnvironmentService(cli).snapshot("cpu", refresh_devices=True)

    assert device_calls == [["devices", "--json", "--refresh"]]


def test_job_status_display_is_exhaustive_and_does_not_finish_on_done_event_early() -> None:
    script = (Path(__file__).parents[1] / "src" / "utteran_gui" / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    definitions = script.split("const JOB_STATUS_DEFINITIONS", 1)[1].split(");", 1)[0]
    finish = script.split("async function finishJob()", 1)[1].split("async function startJob", 1)[0]

    for status in ("starting", "running", "completed", "failed", "cancelled"):
        assert f"{status}:" in definitions
    assert 'outcome: "unknown"' in script
    assert 'kind: "unknown_job_status"' in script
    assert "if (!definition.terminal) return;" in finish
    assert finish.index("if (!definition.terminal) return;") < finish.index("state.source?.close()")
    assert 'definition.outcome === "success"' in finish
    assert 'definition.outcome === "failure"' in finish
    assert 'loadEnvironment($("profile-select").value, true)' in script


def test_cancel_explains_that_native_inference_may_take_time() -> None:
    project = Path(__file__).parents[1]
    script = (project / "src" / "utteran_gui" / "web" / "app.js").read_text(encoding="utf-8")
    translations = (project / "src" / "utteran_gui" / "web" / "i18n.js").read_text(encoding="utf-8")

    handler = script[script.index('$("cancel-button").addEventListener') :]
    assert 't("cancellationPending")' in handler
    assert "GPU約1分" in translations
    assert "NPU約3分" in translations
    assert "CPU約5分" in translations


def test_intel_auto_selection_defaults_to_whisper_cpp_not_cpu() -> None:
    devices = {
        "backends": {"faster-whisper": True, "whisper-cpp": True},
        "ctranslate2": {"available": True, "cuda_devices": []},
        "native": {"variants": {"cpu": True, "vulkan": True}},
        "auto_selection": {
            "asr_backend": "whisper-cpp",
            "asr_device": "vulkan",
            "diarization_backend": "pyannote",
            "diarization_device": "xpu:0",
        },
    }
    models = [
        {"backend": "faster-whisper", "model_id": "large-v3-turbo", "installed": True},
        {
            "backend": "whisper-cpp",
            "model_id": "large-v3-turbo-q5_0",
            "installed": True,
        },
    ]

    options = derive_options(devices, models, {"runnable": {"cpu": True, "vulkan": True}})

    assert options["defaults"] == {
        "asr_backend": "whisper-cpp",
        "asr_model": "large-v3-turbo-q5_0",
        "asr_device": "vulkan",
        "diarization_backend": "pyannote",
        "diarization_device": "xpu:0",
    }


def test_genai_gui_options_mark_npu_discouraged_without_changing_auto() -> None:
    devices = {
        "backends": {"openvino-genai": True},
        "openvino": {"values": ["CPU", "GPU.0", "NPU"]},
        "auto_selection": {
            "asr_backend": "whisper-cpp",
            "asr_device": "vulkan",
            "diarization_backend": "pyannote",
            "diarization_device": "xpu:0",
        },
    }
    models = [
        {
            "backend": "openvino-genai",
            "model_id": "large-v3-turbo-int8",
            "installed": True,
        }
    ]

    options = derive_options(devices, models, {})
    genai = next(item for item in options["asr"] if item["id"] == "openvino-genai")  # type: ignore[index]

    assert [item["id"] for item in genai["devices"]] == ["cpu", "gpu", "npu"]
    assert genai["devices"][-1]["recommended"] is False
    assert "2.06 GiB" in genai["devices"][-1]["recommendation_reason"]
    assert options["defaults"]["asr_backend"] == "whisper-cpp"  # type: ignore[index]


def test_gui_assets_explain_genai_filter_and_hide_npu_by_default() -> None:
    web = Path(__file__).parents[1] / "src" / "utteran_gui" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")
    translations = (web / "i18n.js").read_text(encoding="utf-8")

    assert 'id="show-discouraged-configurations"' in index
    assert 'id="configuration-notice"' in index
    assert "item.recommended !== false" in script
    assert 'item.id === "openvino-genai"' in script
    assert "genaiDiarizationUnavailable" in translations


def test_gui_assets_expose_model_management_dialogs_and_real_model_choice() -> None:
    web = Path(__file__).parents[1] / "src" / "utteran_gui" / "web"
    index = (web / "index.html").read_text(encoding="utf-8")
    script = (web / "app.js").read_text(encoding="utf-8")

    assert 'id="view-models"' in index
    assert 'id="wizard-model-select"' in index
    assert 'id="pick-input-file"' in index and 'id="pick-input-folder"' in index
    assert 'id="pick-output-folder"' in index
    assert 'api("/api/models/actions"' in script
    assert "bridge.choose_path(kind)" in script
    assert '"whisper-cpp:large-v3-turbo-q5_0"' in script


def test_history_api_uses_profile_cli_contracts_without_persisting_result(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _create_profile(tmp_path, "cuda")
    cli = CliAdapter(tmp_path)
    calls: list[tuple[str, object]] = []
    job_id = "0123456789abcdef"
    job_root = tmp_path / job_id
    job_root.mkdir()
    merged_path = job_root / "merged.json"
    merged_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "result": {
                    "input_path": str(tmp_path / "synthetic.wav"),
                    "transcription": {
                        "segments": [],
                        "language": "ja",
                        "duration": 3.0,
                        "backend": "faster-whisper",
                        "model_id": "synthetic-model",
                        "device": "cpu",
                    },
                    "diarization": None,
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "speaker": "SPEAKER_00",
                            "text": "合成テスト",
                            "words": [],
                        }
                    ],
                    "created_at": "2026-08-09T00:00:00+09:00",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    presentation_path = job_root / "presentation.json"
    presentation_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "speaker_labels": {"SPEAKER_00": "テスト話者"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    def list_jobs(profile: str) -> object:
        calls.append(("list", profile))
        return {
            "schema_version": 1,
            "jobs": [
                {
                    "job_id": job_id,
                    "input_name": "synthetic.wav",
                    "status": "done",
                    "output_paths": [],
                    "result_path": str(merged_path),
                    "presentation_path": str(presentation_path),
                }
            ],
        }

    def delete_job(profile: str, job_id: str) -> object:
        calls.append(("delete", (profile, job_id)))
        return {"schema_version": 1, "deleted": [job_id], "freed_bytes": 123}

    def regenerate(options: Any) -> object:
        calls.append(("regenerate", options))
        return {"schema_version": 1, "executed_stages": ["export"], "outputs": []}

    monkeypatch.setattr(cli, "list_jobs", list_jobs)
    monkeypatch.setattr(cli, "delete_job", delete_job)
    monkeypatch.setattr(cli, "regenerate", regenerate)
    app = create_app("session-secret", repo_root=tmp_path, cli=cli)
    client = TestClient(app, headers={SESSION_HEADER: "session-secret"})

    listed = client.get("/api/history?profile=cuda")
    shown = client.get("/api/history/0123456789abcdef?profile=cuda")
    incompatible_data = json.loads(merged_path.read_text(encoding="utf-8"))
    incompatible_data["schema_version"] = 999
    merged_path.write_text(json.dumps(incompatible_data), encoding="utf-8")
    incompatible = client.get("/api/history/0123456789abcdef?profile=cuda")
    deleted = client.delete("/api/history/0123456789abcdef?profile=cuda")
    regenerated = client.post(
        "/api/history/0123456789abcdef/regenerate",
        json={
            "profile": "cuda",
            "output_dir": str(tmp_path / "output"),
            "formats": ["json", "txt"],
            "speaker_labels": {"SPEAKER_00": "テスト話者"},
        },
    )

    assert listed.json()["jobs"][0]["job_id"] == "0123456789abcdef"
    assert shown.json()["result"]["segments"][0]["text"] == "合成テスト"
    assert shown.json()["result"]["segments"][0]["speaker_display"] == "テスト話者"
    assert incompatible.json()["result"] is None
    assert "対応=1、検出=999" in incompatible.json()["result_error"]
    assert deleted.json()["freed_bytes"] == 123
    assert regenerated.json()["executed_stages"] == ["export"]
    regeneration_options = calls[-1][1]
    assert regeneration_options.speaker_labels == {"SPEAKER_00": "テスト話者"}
    assert all(response.headers["cache-control"] == "no-store" for response in [listed, shown])


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


def test_guidance_distinguishes_license_not_accepted_from_invalid_token() -> None:
    """ModelAgreementError (license/terms not accepted) and

    HuggingFaceAuthenticationError (the token itself is invalid) are
    distinct Phase 1 error classes and must not collapse into one "token"
    guidance message (Phase 5c 指示書 Step 4 explicit requirement).
    """
    license_guidance = guidance_for(
        2,
        [],
        [
            {
                "event": "error",
                "error_type": "ModelAgreementError",
                "message": "利用条件に同意されていません",
            }
        ],
    )
    token_guidance = guidance_for(
        2,
        [],
        [
            {
                "event": "error",
                "error_type": "HuggingFaceAuthenticationError",
                "message": "Hugging Face トークンが無効です",
            }
        ],
    )
    assert license_guidance == {"key": "license", "settings_anchor": "token"}
    assert token_guidance == {"key": "token", "settings_anchor": "token"}


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


class FakeWizardProcess:
    pid = 99999

    def __init__(self) -> None:
        self.stdout = iter(["==> Checking Python 3.11 / 3.12\n"])
        self.stderr = iter(())
        self.returncode: int | None = 0
        self.finished = threading.Event()
        self.finished.set()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self.finished.wait(timeout):
            raise TimeoutError
        return self.returncode or 0


def test_wizard_api_routes_run_status_events_and_complete(tmp_path: Path) -> None:
    settings = SettingsStore(tmp_path / "settings.json")
    wizard = SetupWizardService(
        CliAdapter(tmp_path),
        settings_store=settings,
        popen_factory=lambda _command, **_kwargs: FakeWizardProcess(),
    )
    app = create_app(
        "session-secret",
        repo_root=tmp_path,
        settings_store=settings,
        wizard_service=wizard,
    )
    client = TestClient(app, headers={SESSION_HEADER: "session-secret"})

    status = client.get("/api/wizard/status")
    assert status.status_code == 200
    assert status.json()["first_run"] is True

    hardware = client.get("/api/wizard/hardware")
    assert hardware.status_code == 200
    assert "recommendation" in hardware.json()

    not_ready = client.post("/api/wizard/complete")
    assert not_ready.status_code == 409

    _create_profile(tmp_path, "cpu")
    started = client.post(
        "/api/wizard/jobs",
        json={"kind": "smoke_test", "profile": "cpu"},
    )
    assert started.status_code == 202
    job_id = started.json()["id"]

    deadline = time.monotonic() + 2
    while (
        client.get(f"/api/wizard/jobs/{job_id}").json()["status"] not in {"completed", "failed"}
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    snapshot = client.get(f"/api/wizard/jobs/{job_id}").json()
    assert snapshot["status"] == "completed"

    events = client.get(f"/api/wizard/jobs/{job_id}/events")
    assert events.status_code == 200
    assert "event: done" in events.text

    # The smoke_test job above already finished, so a new step is accepted rather than busy.
    next_step = client.post("/api/wizard/jobs", json={"kind": "venv_build", "profile": "cpu"})
    assert next_step.status_code == 202

    completed = client.post("/api/wizard/complete")
    assert completed.status_code == 200
    assert completed.json()["setup_wizard_completed_at"]

    missing = client.get("/api/wizard/jobs/does-not-exist")
    assert missing.status_code == 404


def test_wizard_token_preflight_uses_profile_cli_and_never_returns_token(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cli = CliAdapter(tmp_path)
    token_store = TokenStore(FakeKeyring())
    calls: list[tuple[str, list[str]]] = []

    def run_json(profile: str, arguments: list[str], *, timeout: float = 60.0) -> object:
        del timeout
        calls.append((profile, arguments))
        return {
            "configured": True,
            "source": "keyring",
            "keyring_available": True,
            "access": "available",
            "token": "hf_must_never_escape",
        }

    monkeypatch.setattr(cli, "run_json", run_json)
    app = create_app("session-secret", repo_root=tmp_path, cli=cli, token_store=token_store)
    client = TestClient(app, headers={SESSION_HEADER: "session-secret"})

    saved = client.put("/api/token", json={"token": "hf_synthetic_saved_in_settings"})
    assert saved.status_code == 200
    assert saved.json()["configured"] is True

    response = client.post(
        "/api/wizard/token-preflight",
        json={"profile": "cpu"},
    )

    assert response.status_code == 200
    assert response.json()["access"] == "available"
    assert calls[0][0] == "cpu"
    assert calls[0][1][:3] == ["config", "token-status", "--json"]
    assert "hf_" not in response.text


def test_wizard_detects_gui_keyring_success_but_profile_token_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    cli = CliAdapter(tmp_path)
    token_store = TokenStore(FakeKeyring())
    monkeypatch.setattr(
        cli,
        "run_json",
        lambda _profile, _arguments: {
            "configured": False,
            "source": "none",
            "keyring_available": False,
            "access": "token_missing",
        },
    )
    app = create_app("session-secret", repo_root=tmp_path, cli=cli, token_store=token_store)
    client = TestClient(app, headers={SESSION_HEADER: "session-secret"})

    assert client.put("/api/token", json={"token": "hf_synthetic_boundary"}).json()["configured"]
    preflight = client.post("/api/wizard/token-preflight", json={"profile": "cpu"})

    assert preflight.status_code == 200
    assert preflight.json() == {
        "configured": False,
        "source": "none",
        "keyring_available": False,
        "access": "token_missing",
    }
    assert "hf_synthetic" not in preflight.text


def test_wizard_frontend_separates_input_from_unattended_execution_and_resumes() -> None:
    app_js = (Path(__file__).parents[1] / "src" / "utteran_gui" / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    profile_next = app_js.split("async function wizardProfileNext()", 1)[1].split(
        "async function wizardDiarizationNext()", 1
    )[0]
    model_next = app_js.split("async function wizardModelChoiceNext()", 1)[1].split(
        "function renderWizardConfirmation()", 1
    )[0]
    unattended = app_js.split("async function wizardRunUnattended()", 1)[1].split(
        "async function wizardSaveToken()", 1
    )[0]

    assert 'saveWizardState("diarization")' in profile_next
    assert 'saveWizardState("confirm")' in model_next
    assert 'api("/api/wizard/token-preflight"' in unattended
    assert unattended.index('runWizardJob("venv_build")') < unattended.index(
        'api("/api/wizard/token-preflight"'
    )
    assert unattended.index("wizardState.diarizationModelRef") < unattended.index(
        "wizardState.modelRef"
    )
    assert "await showWizardToken(result.access)" in unattended
    assert 'status.step === "execution"' in app_js
    assert 'status.step === "profile"' in app_js and "renderWizardRecommendation()" in app_js
    assert "completed_stages" in app_js
    assert "wizardState.wantDiarization = false" in app_js


def test_wizard_formats_validation_errors_and_recovers_missing_profile() -> None:
    app_js = (Path(__file__).parents[1] / "src" / "utteran_gui" / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    unattended = app_js.split("async function wizardRunUnattended()", 1)[1].split(
        "async function wizardSaveToken()", 1
    )[0]

    assert "apiErrorMessage(body.detail" in app_js
    assert "Array.isArray(detail)" in app_js
    assert "item?.msg" in app_js
    assert "if (!wizardState.profile)" in unattended
    assert 'saveWizardState("profile")' in unattended
    assert "renderWizardRecommendation()" in unattended
