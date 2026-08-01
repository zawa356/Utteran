from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from utteran.batch import BatchItemResult, BatchSummary
from utteran.cli import app
from utteran.devices import (
    AutoSelection,
    CPUReport,
    CTranslate2Report,
    DeviceReport,
    FfmpegReport,
    LibraryReport,
    OptionalRuntimeReport,
    TorchReport,
)
from utteran.jobs import JobStore
from utteran.models.catalog import ModelEntry, get_model
from utteran.models.manager import ModelManager, ModelStatus

runner = CliRunner()


def test_missing_input_returns_input_exit_code(tmp_path: Path) -> None:
    result = runner.invoke(app, ["transcribe", str(tmp_path / "missing.wav"), "--no-diarization"])

    assert result.exit_code == 4
    assert "入力ファイルが見つかりません" in result.output
    assert "Traceback" not in result.output


def test_missing_hf_token_is_actionable_before_expensive_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    monkeypatch.setenv("HF_TOKEN", "")
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "")
    monkeypatch.setattr("utteran.config.KeyringTokenProvider.get_token", lambda _self: None)

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
    )
    monkeypatch.setattr("utteran.cli.detect_devices", lambda _path: report)

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
    verified = runner.invoke(app, ["models", "verify", entry.key])
    shown_path = runner.invoke(app, ["models", "path"])
    removed = runner.invoke(app, ["models", "remove", entry.key, "--yes"])

    assert listed.exit_code == 0 and "yes" in listed.output
    assert verified.exit_code == 0 and "正常" in verified.output
    assert shown_path.exit_code == 0 and str(model_root) in shown_path.output
    assert removed.exit_code == 0 and "削除しました" in removed.output
    assert not path.exists()


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
