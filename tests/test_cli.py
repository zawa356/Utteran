from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from utteran.cli import app

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
