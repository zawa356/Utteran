from __future__ import annotations

import _thread
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from utteran.batch import BatchItemResult, BatchSummary
from utteran.cli import (
    _cli_overrides,
    _parse_model_selection,
    _parse_variant_selection,
    _run_interruptibly,
    app,
)
from utteran.devices import (
    AutoSelection,
    CPUReport,
    CTranslate2Report,
    DeviceReport,
    FfmpegReport,
    LibraryReport,
    NativeReport,
    OptionalRuntimeReport,
    ProfileReport,
    TorchReport,
    VulkanReport,
)
from utteran.errors import CancelledError, ConfigurationError
from utteran.jobs import JobStore
from utteran.models.catalog import ModelEntry, get_model, list_models
from utteran.models.manager import ModelManager, ModelStatus
from utteran.types import CancelToken

runner = CliRunner()


def test_interruptible_worker_returns_a_normal_result() -> None:
    assert (
        _run_interruptibly(lambda cancel: "ok" if not cancel.is_cancelled else "cancelled") == "ok"
    )


def test_interruptible_worker_turns_main_thread_interrupt_into_cancellation() -> None:
    worker_started = threading.Event()

    def operation(cancel: CancelToken) -> None:
        worker_started.set()
        while not cancel.is_cancelled:
            time.sleep(0.01)
        cancel.raise_if_cancelled()

    def interrupt_when_ready() -> None:
        assert worker_started.wait(1)
        _thread.interrupt_main()

    interrupter = threading.Thread(target=interrupt_when_ready, daemon=True)
    interrupter.start()

    with pytest.raises(CancelledError):
        _run_interruptibly(operation)

    interrupter.join(1)


def test_interruptible_worker_hard_exit_returns_130_in_cli_process() -> None:
    probe = """
import _thread
import threading
import time
from utteran.cli import _run_interruptibly

started = threading.Event()

def operation(cancel):
    started.set()
    while not cancel.is_cancelled:
        time.sleep(0.01)

def interrupt_when_ready():
    started.wait(1)
    _thread.interrupt_main()

threading.Thread(target=interrupt_when_ready, daemon=True).start()
_run_interruptibly(operation, hard_exit_on_interrupt=True)
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 130
    assert "中断" in completed.stderr


def test_missing_input_returns_input_exit_code(tmp_path: Path) -> None:
    result = runner.invoke(app, ["transcribe", str(tmp_path / "missing.wav"), "--no-diarization"])

    assert result.exit_code == 4
    assert "入力ファイルが見つかりません" in result.output
    assert "Traceback" not in result.output


def test_cli_overrides_support_language_auto_and_diarization_model() -> None:
    overrides = _cli_overrides(
        format_names=None,
        output_dir=None,
        asr_backend="auto",
        asr_model="large-v3-turbo",
        diarization_backend="pyannote",
        diarization_model="local-pyannote",
        device="auto",
        language="auto",
        num_speakers=None,
        min_speakers=None,
        max_speakers=None,
        no_diarization=False,
        verbose=False,
        quiet=False,
    )

    assert overrides["asr"] == {
        "backend": "auto",
        "model": "large-v3-turbo",
        "device": "auto",
        "language": None,
    }
    assert overrides["diarization"] == {
        "backend": "pyannote",
        "model": "local-pyannote",
        "device": "auto",
    }


def test_missing_hf_token_is_actionable_before_expensive_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HF_TOKEN", "")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "")
    monkeypatch.setattr("utteran.config.KeyringTokenProvider.get_token", lambda _self: None)
    monkeypatch.setattr(
        "utteran.diarization.registry.find_runtime_model", lambda *_args, **_kwargs: None
    )

    result = runner.invoke(app, ["transcribe", str(input_path), "--quiet"])

    assert result.exit_code == 2
    assert "トークンが未設定" in result.output
    assert "settings/tokens" in result.output
    assert "Traceback" not in result.output


def test_missing_ffmpeg_returns_dependency_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    monkeypatch.setattr("utteran.audio.shutil.which", lambda _name: None)
    monkeypatch.setattr("utteran.audio.user_data_dir", lambda _name: str(tmp_path))

    result = runner.invoke(
        app,
        ["transcribe", str(input_path), "--no-diarization", "--quiet"],
    )

    assert result.exit_code == 3
    assert "ffmpeg が見つかりません" in result.output
    assert "Traceback" not in result.output


def test_devices_json_is_machine_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    report = DeviceReport(
        cpu=CPUReport(8, 4, True, False),
        ctranslate2=CTranslate2Report(True, "test", ("int8",), 0, ()),
        cuda_libraries=LibraryReport(None, None),
        pytorch=TorchReport(False, None, False, ()),
        openvino=OptionalRuntimeReport(False, ()),
        onnxruntime=OptionalRuntimeReport(True, ("CPUExecutionProvider",)),
        ffmpeg=FfmpegReport(True, "/bin/ffmpeg", "ffmpeg test"),
        backends={"faster-whisper": True},
        auto_selection=AutoSelection(
            "faster-whisper",
            "cpu",
            "int8",
            "pyannote",
            "cpu",
        ),
        warnings=(),
        profile=ProfileReport(current="cpu", profiles=()),
        vulkan=VulkanReport(False, "no glslc", False, None, "no vulkaninfo"),
        native=NativeReport(built=False, whisper_cpp_tag=None, variants={}),
    )
    monkeypatch.setattr("utteran.cli.detect_devices", lambda _path, **_kwargs: report)

    result = runner.invoke(app, ["devices", "--json"])

    assert result.exit_code == 0
    assert '"logical_cores": 8' in result.output
    assert '"asr_device": "cpu"' in result.output


def test_jobs_commands_list_show_and_clean_failed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    job_dir = tmp_path / "jobs"
    config_path.write_text(
        f"[general]\njob_dir = '{job_dir.as_posix()}'\n",
        encoding="utf-8",
    )
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    job = JobStore(job_dir).open(input_path)
    job.fail_stage("audio", "test-hash", "decode failed")

    listed = runner.invoke(app, ["jobs", "list", "--config", str(config_path)])
    shown = runner.invoke(
        app,
        ["jobs", "show", job.manifest.job_id, "--config", str(config_path)],
    )
    cleaned = runner.invoke(
        app,
        ["jobs", "clean", "--failed", "--yes", "--config", str(config_path)],
    )

    assert listed.exit_code == 0 and job.manifest.job_id in listed.output
    assert shown.exit_code == 0 and "decode failed" in shown.output
    assert cleaned.exit_code == 0 and "1件" in cleaned.output
    assert not job.root.exists()


def test_config_commands_create_and_show_effective_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"

    initialized = runner.invoke(app, ["config", "init", "--path", str(config_path)])
    shown = runner.invoke(app, ["config", "show", "--path", str(config_path)])
    duplicate = runner.invoke(app, ["config", "init", "--path", str(config_path)])

    assert initialized.exit_code == 0
    assert shown.exit_code == 0 and '"large-v3-turbo"' in shown.output
    assert "HF_TOKEN" not in shown.output
    assert duplicate.exit_code == 2 and "既に存在" in duplicate.output


def test_batch_partial_failure_returns_exit_code_five(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.wav").write_bytes(b"a")
    (tmp_path / "b.wav").write_bytes(b"b")
    summary = BatchSummary(
        (
            BatchItemResult(tmp_path / "a.wav", "success", "完了"),
            BatchItemResult(tmp_path / "b.wav", "failed", "test failure"),
        )
    )
    monkeypatch.setattr("utteran.cli.find_ffmpeg", lambda _path: Path("ffmpeg"))
    monkeypatch.setattr("utteran.cli._ensure_configured_models", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("utteran.cli._run_batch_with_progress", lambda *_args, **_kwargs: summary)

    result = runner.invoke(
        app,
        ["transcribe", str(tmp_path), "--no-diarization", "--quiet"],
    )

    assert result.exit_code == 5
    assert "成功 1" in result.output
    assert "失敗 1" in result.output


def test_noninteractive_transcribe_does_not_download_missing_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    monkeypatch.setattr("utteran.cli.find_ffmpeg", lambda _path: Path("ffmpeg"))

    def missing_status(_self: ModelManager, entry: ModelEntry) -> ModelStatus:
        return ModelStatus(
            entry=entry,
            installed=False,
            path=None,
            size_bytes=0,
            managed=False,
        )

    monkeypatch.setattr("utteran.cli.ModelManager.status", missing_status)

    result = runner.invoke(
        app,
        ["transcribe", str(input_path), "--no-diarization", "--quiet"],
    )

    assert result.exit_code == 3
    assert "非対話環境では自動取得しません" in result.output
    assert "models download" in result.output


def test_models_cli_list_verify_path_and_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_root = tmp_path / "models"
    monkeypatch.setenv("UTTERAN_MODEL_DIR", str(model_root))
    manager = ModelManager(model_root)
    entry = get_model("faster-whisper:large-v3")
    path = manager.managed_path(entry)
    path.mkdir(parents=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    (path / "model.bin").write_bytes(b"weights")

    listed = runner.invoke(app, ["models", "list"])
    available = runner.invoke(app, ["models", "list", "--available"])
    verified = runner.invoke(app, ["models", "verify", entry.key])
    shown_path = runner.invoke(app, ["models", "path"])
    removed = runner.invoke(app, ["models", "remove", entry.key, "--yes"])

    assert listed.exit_code == 0 and "導入済み" in listed.output
    assert available.exit_code == 0 and "Kotoba-Whisper" in available.output
    assert "日本語音声認識向け" in available.output
    assert verified.exit_code == 0 and "正常" in verified.output
    assert shown_path.exit_code == 0 and str(model_root) in shown_path.output
    assert removed.exit_code == 0 and "削除しました" in removed.output
    assert not path.exists()


def test_models_list_json_is_machine_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("utteran.cli._model_manager", lambda: ModelManager(Path("missing")))

    result = runner.invoke(app, ["models", "list", "--available", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload and {"key", "backend", "installed"} <= payload[0].keys()


def test_models_download_command_uses_explicit_manager_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = get_model("faster-whisper:large-v3")
    destination = tmp_path / "downloaded"
    monkeypatch.setattr(
        "utteran.cli.ModelManager.status",
        lambda _self, selected: ModelStatus(selected, False, None, 0, False),
    )
    monkeypatch.setattr(
        "utteran.cli.ModelManager.download",
        lambda _self, selected, **_kwargs: destination if selected == entry else Path(),
    )

    result = runner.invoke(app, ["models", "download", entry.key])

    assert result.exit_code == 0
    assert str(destination) in result.output.replace("\n", "")


def test_models_download_without_id_rejects_noninteractive_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("utteran.cli._stdin_is_interactive", lambda: False)

    result = runner.invoke(app, ["models", "download"])

    assert result.exit_code == 2
    assert "非対話環境ではモデルIDを省略できません" in result.output
    assert "utteran models list" in result.output
    assert "--available" in result.output


def test_models_download_interactively_selects_multiple_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[ModelEntry] = []
    monkeypatch.setenv("UTTERAN_MODEL_DIR", str(tmp_path / "models"))
    monkeypatch.setattr("utteran.cli._stdin_is_interactive", lambda: True)
    monkeypatch.setattr(
        "utteran.cli.ModelManager.status",
        lambda _self, entry: ModelStatus(entry, False, None, 0, False),
    )

    def fake_download(
        _self: ModelManager,
        entry: ModelEntry,
        **_kwargs: object,
    ) -> Path:
        selected.append(entry)
        return tmp_path / entry.backend / entry.model_id.replace("/", "--")

    monkeypatch.setattr("utteran.cli.ModelManager.download", fake_download)

    result = runner.invoke(app, ["models", "download"], input="4,5\n")

    assert result.exit_code == 0
    assert selected == [
        get_model("faster-whisper:kotoba-whisper-v2.0"),
        get_model("pyannote:pyannote/speaker-diarization-community-1"),
    ]
    assert "Kotoba-Whisper" in result.output
    assert "pyannote community-1" in result.output


def test_models_download_interactive_blank_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("utteran.cli._stdin_is_interactive", lambda: True)
    monkeypatch.setattr(
        "utteran.cli.ModelManager.status",
        lambda _self, entry: ModelStatus(entry, False, None, 0, False),
    )

    result = runner.invoke(app, ["models", "download"], input="\n")

    assert result.exit_code == 0
    assert "モデルは選択されませんでした" in result.output


def test_model_selection_accepts_numbers_ids_and_rejects_invalid_number() -> None:
    entries = list_models()

    selected = _parse_model_selection(
        "1、faster-whisper:kotoba-whisper-v2.0,1",
        entries,
    )

    assert selected == [entries[0], get_model("faster-whisper:kotoba-whisper-v2.0")]
    with pytest.raises(ConfigurationError, match="範囲外"):
        _parse_model_selection("99", entries)


def test_profiles_list_reports_one_created_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UTTERAN_VENV_DIR", raising=False)
    (tmp_path / "win-cpu").mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"[general]\nvenv_dir = '{tmp_path.as_posix()}'\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["profiles", "list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "cpu" in result.output
    assert "作成済み" in result.output
    assert "未作成" in result.output  # cuda/intel/vulkan were not created


def test_profiles_current_reflects_the_run_ps1_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UTTERAN_PROFILE", raising=False)
    unset = runner.invoke(app, ["profiles", "current"])

    monkeypatch.setenv("UTTERAN_PROFILE", "cuda")
    set_result = runner.invoke(app, ["profiles", "current"])

    assert unset.exit_code == 0 and "不明" in unset.output
    assert set_result.exit_code == 0 and "cuda" in set_result.output


def test_profiles_path_prints_the_resolved_venv_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UTTERAN_VENV_DIR", raising=False)
    config_path = tmp_path / "config.toml"
    venv_root = tmp_path / "custom-venvs"
    config_path.write_text(
        f"[general]\nvenv_dir = '{venv_root.as_posix()}'\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["profiles", "path", "--config", str(config_path)])

    assert result.exit_code == 0
    assert str(venv_root) in result.output


def test_native_build_reports_success_and_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = {
        "backends": {"cpu": {"executable": "/fake/whisper-cli"}},
        "errors": {"vulkan": "no glslc"},
    }

    class FakeBuilder:
        def __init__(self, _native_dir: object) -> None:
            pass

        def build_all(self, *, variants: object, force: bool) -> dict[str, object]:
            return manifest

    monkeypatch.setattr("utteran.cli.NativeBuilder", FakeBuilder)

    result = runner.invoke(app, ["native", "build", "--variant", "cpu,vulkan"])

    assert result.exit_code == 0
    assert "構築成功" in result.output
    assert "スキップ" in result.output


def test_native_build_exits_nonzero_when_nothing_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBuilder:
        def __init__(self, _native_dir: object) -> None:
            pass

        def build_all(self, *, variants: object, force: bool) -> dict[str, object]:
            return {"backends": {}, "errors": {"cpu": "no cmake"}}

    monkeypatch.setattr("utteran.cli.NativeBuilder", FakeBuilder)

    result = runner.invoke(app, ["native", "build", "--variant", "cpu"])

    assert result.exit_code == 3


def test_native_build_rejects_unknown_variant_name() -> None:
    result = runner.invoke(app, ["native", "build", "--variant", "cpu,rocm"])

    assert result.exit_code == 2
    assert "rocm" in result.output


def test_native_status_reports_no_manifest_when_never_built(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBuilder:
        def __init__(self, _native_dir: object) -> None:
            pass

        def status(self) -> dict[str, object]:
            return {"manifest": {}, "runnable": {}}

    monkeypatch.setattr("utteran.cli.NativeBuilder", FakeBuilder)

    result = runner.invoke(app, ["native", "status"])

    assert result.exit_code == 0
    assert "未実行" in result.output


def test_native_clean_requires_exactly_one_selector() -> None:
    neither = runner.invoke(app, ["native", "clean"])
    both = runner.invoke(app, ["native", "clean", "--all", "--variant", "cpu"])

    assert neither.exit_code == 2
    assert both.exit_code == 2


def test_native_clean_removes_one_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str | None] = []

    class FakeBuilder:
        def __init__(self, _native_dir: object) -> None:
            pass

        def clean(self, *, variant: str | None) -> None:
            calls.append(variant)

    monkeypatch.setattr("utteran.cli.NativeBuilder", FakeBuilder)

    result = runner.invoke(app, ["native", "clean", "--variant", "cpu"])

    assert result.exit_code == 0
    assert calls == ["cpu"]


def test_parse_variant_selection_defaults_and_validates() -> None:
    from utteran.native import VARIANT_NAMES

    assert _parse_variant_selection(None) == VARIANT_NAMES
    assert _parse_variant_selection("cpu, vulkan") == ("cpu", "vulkan")
    with pytest.raises(ConfigurationError, match="rocm"):
        _parse_variant_selection("cpu,rocm")
