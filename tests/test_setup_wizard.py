from __future__ import annotations

import platform
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from utteran_gui.cli import CliAdapter
from utteran_gui.settings import SettingsStore
from utteran_gui.setup_wizard import (
    STAGE_MARKER,
    SetupWizardService,
    WizardNotReadyError,
    WizardProfileMissingError,
)


def _create_profile(repo: Path, name: str) -> Path:
    if platform.system() == "Windows":
        executable = repo / ".venvs" / f"win-{name}" / "Scripts" / "utteran.exe"
    else:
        executable = repo / ".venvs" / f"linux-{name}" / "bin" / "utteran"
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    return executable


class FakeWizardProcess:
    pid = 54321

    def __init__(self, stdout_lines: list[str] | None = None) -> None:
        self.stdout = iter(stdout_lines or [])
        self.stderr = iter(())
        self.returncode: int | None = None
        self.finished = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if not self.finished.wait(timeout):
            raise TimeoutError
        return self.returncode or 0


def _wait_until(predicate: Any, deadline_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + deadline_seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)


def _service(tmp_path: Path, popen: Any = None, tree_killer: Any = None) -> SetupWizardService:
    kwargs: dict[str, Any] = {"settings_store": SettingsStore(tmp_path / "settings.json")}
    if popen is not None:
        kwargs["popen_factory"] = popen
    if tree_killer is not None:
        kwargs["tree_killer"] = tree_killer
    return SetupWizardService(CliAdapter(tmp_path), **kwargs)


def test_venv_build_streams_stage_markers_and_completes(tmp_path: Path) -> None:
    process = FakeWizardProcess(
        [
            f"{STAGE_MARKER}python_check\n",
            "==> Checking Python 3.11 / 3.12\n",
            f"{STAGE_MARKER}uv_install\n",
        ]
    )
    service = _service(tmp_path, popen=lambda _command, **_kwargs: process)

    started = service.start_venv_build("cpu")
    job_id = str(started["id"])
    process.returncode = 0
    process.finished.set()
    _wait_until(lambda: service.snapshot(job_id)["status"] in {"completed", "failed"})

    snapshot = service.snapshot(job_id)
    assert snapshot["status"] == "completed"
    assert snapshot["exit_code"] == 0
    stages = [event["stage"] for event in snapshot["events"] if event.get("event") == "stage_start"]
    assert stages == ["python_check", "uv_install"]
    assert any("Checking Python" in line for line in snapshot["logs"])
    assert service.status()["completed_stages"] == ["venv"]


def test_failed_venv_build_does_not_misclassify_setup_advice_as_invalid_token(
    tmp_path: Path,
) -> None:
    process = FakeWizardProcess(
        [
            "For pyannote, create a read token at https://huggingface.co/settings/tokens\n",
            "then set HF_TOKEN in .env. The token is never printed.\n",
            "uv sync failed\n",
        ]
    )
    process.returncode = 1
    service = _service(tmp_path, popen=lambda _command, **_kwargs: process)

    started = service.start_venv_build("intel")
    job_id = str(started["id"])
    process.finished.set()
    _wait_until(lambda: service.snapshot(job_id)["status"] == "failed")

    snapshot = service.snapshot(job_id)
    assert snapshot["guidance"] == {"key": "general", "settings_anchor": ""}
    assert service.status()["completed_stages"] == []


def test_wizard_input_and_execution_progress_survive_service_restart(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    service = SetupWizardService(CliAdapter(tmp_path), settings_store=store)

    _create_profile(tmp_path, "intel")
    service.start()
    service.save_state("token", profile="intel", diarization_enabled=True)
    store.update({"setup_wizard_completed_stages": ["venv"]})
    service.record_preflight("token_invalid")

    restarted = SetupWizardService(CliAdapter(tmp_path), settings_store=store)
    status = restarted.status()
    assert status["step"] == "token"
    assert status["profile"] == "intel"
    assert status["diarization_enabled"] is True
    assert status["completed_stages"] == ["venv"]
    assert status["token_error"] == "token_invalid"

    restarted.record_preflight("available")
    resumed = SetupWizardService(CliAdapter(tmp_path), settings_store=store).status()
    assert resumed["completed_stages"] == ["venv", "preflight"]
    assert resumed["token_error"] is None


def test_wizard_does_not_replay_stale_token_error_without_profile_venv(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    service = SetupWizardService(CliAdapter(tmp_path), settings_store=store)
    service.start()
    service.save_state("token", profile="intel", diarization_enabled=True)
    service.record_preflight("token_invalid")

    status = SetupWizardService(CliAdapter(tmp_path), settings_store=store).status()

    assert status["step"] == "token"
    assert status["token_error"] is None


def test_entering_token_step_clears_error_from_previous_attempt(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    service = SetupWizardService(CliAdapter(tmp_path), settings_store=store)
    _create_profile(tmp_path, "intel")
    service.start()
    service.save_state("token", profile="intel")
    store.update({"setup_wizard_completed_stages": ["venv"]})
    service.record_preflight("token_invalid")

    service.save_state("token")

    assert store.load().setup_wizard_token_error is None


def test_failed_recheck_removes_previous_preflight_completion(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    service = SetupWizardService(CliAdapter(tmp_path), settings_store=store)
    _create_profile(tmp_path, "intel")
    service.start()
    service.save_state("token", profile="intel")
    store.update({"setup_wizard_completed_stages": ["venv", "preflight"]})

    service.record_preflight("token_missing")

    saved = store.load()
    assert saved.setup_wizard_completed_stages == ("venv",)
    assert saved.setup_wizard_token_error == "token_missing"


def test_model_download_persists_asr_and_diarization_as_distinct_stages(tmp_path: Path) -> None:
    _create_profile(tmp_path, "cpu")
    diar_process = FakeWizardProcess()
    asr_process = FakeWizardProcess()
    processes = [diar_process, asr_process]
    service = _service(tmp_path, popen=lambda _command, **_kwargs: processes.pop(0))

    diarization = service.start_model_download(
        "cpu", "pyannote:pyannote/speaker-diarization-community-1"
    )
    _wait_until(lambda: service.snapshot(str(diarization["id"]))["status"] == "running")
    diar_process.returncode = 0
    diar_process.finished.set()
    _wait_until(lambda: service.snapshot(str(diarization["id"]))["status"] == "completed")

    asr = service.start_model_download("cpu", "faster-whisper:large-v3-turbo")
    _wait_until(lambda: service.snapshot(str(asr["id"]))["status"] == "running")
    asr_process.returncode = 0
    asr_process.finished.set()
    _wait_until(lambda: service.snapshot(str(asr["id"]))["status"] == "completed")

    assert service.status()["completed_stages"] == ["diarization_model", "asr_model"]


def test_wizard_steps_are_queued_and_run_one_at_a_time(tmp_path: Path) -> None:
    first_process = FakeWizardProcess()
    second_process = FakeWizardProcess()
    launched: list[FakeWizardProcess] = []

    def popen(_command: list[str], **_kwargs: Any) -> FakeWizardProcess:
        process = [first_process, second_process][len(launched)]
        launched.append(process)
        return process

    service = _service(tmp_path, popen=popen)

    started = service.start_venv_build("cpu")
    _wait_until(lambda: service.snapshot(str(started["id"]))["status"] == "running")
    queued = service.start_venv_build("cuda")
    assert service.snapshot(str(queued["id"]))["status"] == "starting"
    assert launched == [first_process]

    first_process.returncode = 0
    first_process.finished.set()
    _wait_until(lambda: service.snapshot(str(queued["id"]))["status"] == "running")
    assert launched == [first_process, second_process]
    second_process.returncode = 0
    second_process.finished.set()


def test_cancel_uses_injected_tree_killer(tmp_path: Path) -> None:
    process = FakeWizardProcess()
    killed: list[int] = []

    def kill(fake: Any) -> None:
        killed.append(fake.pid)
        fake.returncode = 1
        fake.finished.set()

    service = _service(tmp_path, popen=lambda _command, **_kwargs: process, tree_killer=kill)
    started = service.start_venv_build("cpu")
    job_id = str(started["id"])
    _wait_until(lambda: service.snapshot(job_id)["status"] == "running")

    service.cancel(job_id)
    _wait_until(lambda: service.snapshot(job_id)["status"] in {"cancelled", "failed"})

    snapshot = service.snapshot(job_id)
    assert killed == [54321]
    assert snapshot["status"] == "cancelled"
    assert snapshot["exit_code"] == 130


def test_model_download_requires_an_existing_profile_venv(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(WizardProfileMissingError):
        service.start_model_download("cpu", "faster-whisper:large-v3-turbo")


def test_model_management_action_uses_explicit_cli_command_without_wizard_stage(
    tmp_path: Path,
) -> None:
    executable = _create_profile(tmp_path, "intel")
    process = FakeWizardProcess(["取得完了\n"])
    captured: list[str] = []

    def popen(command: list[str], **_kwargs: Any) -> FakeWizardProcess:
        captured.extend(command)
        return process

    service = _service(tmp_path, popen=popen)
    started = service.start_model_action("intel", "download", "whisper-cpp:large-v3-turbo-q5_0")
    process.returncode = 0
    process.finished.set()
    _wait_until(lambda: service.snapshot(str(started["id"]))["status"] == "completed")

    assert captured == [
        str(executable),
        "models",
        "download",
        "whisper-cpp:large-v3-turbo-q5_0",
        "--progress-json",
    ]
    assert service.status()["completed_stages"] == []


def test_whisper_cpp_native_build_runs_before_smoke_as_a_distinct_stage(tmp_path: Path) -> None:
    executable = _create_profile(tmp_path, "intel")
    process = FakeWizardProcess(["ネイティブビルドを開始します。数分から数十分かかります。\n"])
    captured: list[str] = []

    def popen(command: list[str], **_kwargs: Any) -> FakeWizardProcess:
        captured.extend(command)
        return process

    service = _service(tmp_path, popen=popen)
    started = service.start_native_build("intel", "whisper-cpp:large-v3-turbo-q5_0")
    job_id = str(started["id"])
    _wait_until(lambda: service.snapshot(job_id)["status"] == "running")
    assert service.snapshot(job_id)["status"] == "running"
    process.returncode = 0
    process.finished.set()
    _wait_until(lambda: service.snapshot(job_id)["status"] == "completed")

    assert captured == [
        str(executable),
        "native",
        "build",
        "--variant",
        "openvino_vulkan,vulkan,openvino",
    ]
    assert service.status()["completed_stages"] == ["native"]


def test_non_whisper_model_does_not_request_a_native_build(tmp_path: Path) -> None:
    _create_profile(tmp_path, "cpu")
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="only required for whisper-cpp"):
        service.start_native_build("cpu", "faster-whisper:large-v3-turbo")


def test_failed_native_build_has_specific_guidance(tmp_path: Path) -> None:
    _create_profile(tmp_path, "vulkan")
    process = FakeWizardProcess(["glslc (Vulkan SDK シェーダーコンパイラ) が見つかりません。\n"])
    process.returncode = 3
    service = _service(tmp_path, popen=lambda _command, **_kwargs: process)

    started = service.start_native_build("vulkan", "whisper-cpp:base")
    process.finished.set()
    _wait_until(lambda: service.snapshot(str(started["id"]))["status"] == "failed")

    assert service.snapshot(str(started["id"]))["guidance"] == {
        "key": "native",
        "settings_anchor": "",
    }


def test_smoke_test_requires_an_existing_profile_venv(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(WizardProfileMissingError):
        service.start_smoke_test("cpu")


def test_smoke_test_uses_synthetic_audio_and_cleans_up_its_temp_dir(tmp_path: Path) -> None:
    _create_profile(tmp_path, "cpu")
    process = FakeWizardProcess()
    captured_audio_paths: list[str] = []

    def popen(command: list[str], **_kwargs: Any) -> Any:
        audio_path = Path(command[2])
        assert audio_path.suffix == ".wav"
        assert audio_path.stat().st_size > 44  # real WAV header + silent frames
        captured_audio_paths.append(str(audio_path))
        return process

    service = _service(tmp_path, popen=popen)
    started = service.start_smoke_test("cpu")
    job_id = str(started["id"])
    process.returncode = 0
    process.finished.set()
    _wait_until(lambda: service.snapshot(job_id)["status"] in {"completed", "failed"})

    assert service.snapshot(job_id)["status"] == "completed"
    assert captured_audio_paths
    assert not Path(captured_audio_paths[0]).exists(), "temp smoke-test audio must be cleaned up"


def test_smoke_test_passes_through_the_downloaded_model_reference(tmp_path: Path) -> None:
    """Confirmed on real hardware: the `intel` profile auto-selects

    whisper-cpp/vulkan, but a machine that only downloaded a faster-whisper
    model would fail the smoke test without an explicit override - so the
    wizard must pass the exact model it just downloaded, not rely on auto.
    """
    _create_profile(tmp_path, "cpu")
    process = FakeWizardProcess()
    captured_commands: list[list[str]] = []

    def popen(command: list[str], **_kwargs: Any) -> Any:
        captured_commands.append(command)
        return process

    service = _service(tmp_path, popen=popen)
    started = service.start_smoke_test("cpu", asr_model_ref="faster-whisper:large-v3-turbo")
    job_id = str(started["id"])
    process.returncode = 0
    process.finished.set()
    _wait_until(lambda: service.snapshot(job_id)["status"] in {"completed", "failed"})

    command = captured_commands[0]
    assert "--asr-backend" in command
    assert command[command.index("--asr-backend") + 1] == "faster-whisper"
    assert command[command.index("--asr-model") + 1] == "large-v3-turbo"


def test_complete_requires_a_successful_smoke_test_first(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(WizardNotReadyError):
        service.complete()


def test_complete_persists_after_a_successful_smoke_test(tmp_path: Path) -> None:
    _create_profile(tmp_path, "cpu")
    process = FakeWizardProcess()
    settings_store = SettingsStore(tmp_path / "settings.json")
    service = SetupWizardService(
        CliAdapter(tmp_path),
        settings_store=settings_store,
        popen_factory=lambda _command, **_kwargs: process,
    )
    started = service.start_smoke_test("cpu")
    job_id = str(started["id"])
    process.returncode = 0
    process.finished.set()
    _wait_until(lambda: service.snapshot(job_id)["status"] == "completed")

    persisted = settings_store.load().setup_wizard_completed_at
    assert persisted, "smoke success must persist completion before the Done button is clicked"
    restarted = SetupWizardService(CliAdapter(tmp_path), settings_store=settings_store)
    assert restarted.status()["completed_at"] == persisted
    assert restarted.complete()["setup_wizard_completed_at"] == persisted

    result = service.complete()
    assert result["setup_wizard_completed_at"]
    assert settings_store.load().setup_wizard_completed_at == result["setup_wizard_completed_at"]


def test_started_wizard_is_offered_for_resume_after_restart(tmp_path: Path) -> None:
    _create_profile(tmp_path, "cpu")
    store = SettingsStore(tmp_path / "settings.json")
    service = SetupWizardService(CliAdapter(tmp_path), settings_store=store)

    started = service.start()
    assert started["setup_wizard_started_at"]

    restarted = SetupWizardService(CliAdapter(tmp_path), settings_store=store)
    status = restarted.status()
    assert status["first_run"] is True
    assert status["resume_available"] is True


def test_invalid_execution_state_without_profile_recovers_to_recommendation(
    tmp_path: Path,
) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.update(
        {
            "setup_wizard_started_at": "2026-08-24T00:00:00+00:00",
            "setup_wizard_step": "execution",
            "setup_wizard_profile": None,
            "setup_wizard_completed_stages": ["preflight"],
        }
    )

    status = SetupWizardService(CliAdapter(tmp_path), settings_store=store).status()

    assert status["step"] == "profile"
    assert status["profile"] is None
    assert status["completed_stages"] == []
    assert status["first_run"] is True
    assert status["resume_available"] is True


def test_service_refuses_to_persist_execution_without_a_profile(tmp_path: Path) -> None:
    service = _service(tmp_path)

    saved = service.save_state("execution", diarization_enabled=True)

    assert saved["setup_wizard_step"] == "profile"
    assert saved["setup_wizard_profile"] is None
    assert saved["setup_wizard_completed_stages"] == ()


def test_selecting_a_different_profile_discards_stale_execution_progress(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.update(
        {
            "setup_wizard_profile": "cpu",
            "setup_wizard_completed_stages": ["venv", "preflight", "asr_model"],
        }
    )
    service = SetupWizardService(CliAdapter(tmp_path), settings_store=store)

    service.save_state("diarization", profile="intel", diarization_enabled=True)

    assert service.status()["profile"] == "intel"
    assert service.status()["completed_stages"] == []


def test_status_is_first_run_with_no_settings_file_and_no_profile(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.status()["first_run"] is True


def test_status_is_not_first_run_for_an_existing_install_missing_only_the_new_field(
    tmp_path: Path,
) -> None:
    """An install that predates Phase 5c has a settings.json without

    `setup_wizard_completed_at` and already has a profile venv - it must not
    be treated as first-run just because the new field is absent (Phase 5c
    指示書's explicit "don't annoy existing users" requirement).
    """
    _create_profile(tmp_path, "cpu")
    store = SettingsStore(tmp_path / "settings.json")
    store.save(store.load())
    service = SetupWizardService(CliAdapter(tmp_path), settings_store=store)
    assert service.status()["first_run"] is False
