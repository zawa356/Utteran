from __future__ import annotations

from pathlib import Path

import pytest

from utteran.audio import build_ffmpeg_command, find_ffmpeg, normalize_audio
from utteran.errors import FfmpegNotFoundError, InputFileNotFoundError


def test_configured_ffmpeg_has_highest_priority(tmp_path: Path) -> None:
    configured = tmp_path / "ffmpeg-custom"
    configured.touch()

    assert find_ffmpeg(configured) == configured


def test_ffmpeg_missing_has_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("utteran.audio.shutil.which", lambda _name: None)
    monkeypatch.setattr("utteran.audio.user_data_dir", lambda _name: str(tmp_path))

    with pytest.raises(FfmpegNotFoundError, match="PATH"):
        find_ffmpeg()


def test_command_normalizes_to_pcm16_16khz_mono(tmp_path: Path) -> None:
    command = build_ffmpeg_command(Path("ffmpeg"), tmp_path / "video.mp4", tmp_path / "audio.wav")

    assert command[command.index("-acodec") + 1] == "pcm_s16le"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-ac") + 1] == "1"
    assert "-vn" in command


def test_missing_input_is_reported_before_ffmpeg_lookup(tmp_path: Path) -> None:
    with pytest.raises(InputFileNotFoundError, match="入力ファイル"):
        normalize_audio(tmp_path / "missing.mp4", tmp_path / "audio.wav")
