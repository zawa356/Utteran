from __future__ import annotations

from pathlib import Path

import pytest

from utteran_gui import logging_runtime


def _settings(log_dir: Path | None = None) -> logging_runtime.GuiLoggingSettings:
    return logging_runtime.GuiLoggingSettings(log_dir, False, 30, 100, 100)


def test_packaged_gui_defaults_logs_outside_the_install_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_logs = tmp_path / "user-logs"
    monkeypatch.setattr("utteran_paths.user_log_dir", lambda _name: str(user_logs))
    monkeypatch.setattr(logging_runtime, "_writable", lambda _path: True)

    selected, fell_back = logging_runtime.resolve_gui_log_dir(
        _settings(), install_dir=tmp_path / "install", packaged=True
    )

    assert selected == user_logs.resolve()
    assert fell_back is False


def test_explicit_log_directory_still_wins_for_packaged_gui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured"
    monkeypatch.setattr(logging_runtime, "_writable", lambda _path: True)

    selected, fell_back = logging_runtime.resolve_gui_log_dir(
        _settings(configured), install_dir=tmp_path / "install", packaged=True
    )

    assert selected == configured.resolve()
    assert fell_back is False
