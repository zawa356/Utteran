from __future__ import annotations

from pathlib import Path

import pytest

from utteran.config import Config, default_config_path
from utteran.devices import default_probe_cache_path
from utteran.memory import default_calibration_path
from utteran.models.manager import resolve_model_dir
from utteran.native import default_native_dir
from utteran_gui.cli import CliAdapter
from utteran_gui.settings import SessionTokenStore, SettingsStore
from utteran_paths import resolve_data_paths


def test_portable_data_root_contains_every_managed_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "portable-data"
    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(root))
    paths = resolve_data_paths(tmp_path)

    resolved = (
        paths.venvs,
        resolve_model_dir(),
        paths.openvino_genai_cache,
        default_probe_cache_path(),
        default_native_dir(),
        paths.ffmpeg_bin,
        Config().effective_job_dir,
        default_config_path(),
        SettingsStore().path,
        paths.logs,
        default_calibration_path(),
        CliAdapter(tmp_path).venv_root,
    )
    assert all(path == root or root in path.parents for path in resolved)


def test_session_token_store_never_uses_keyring() -> None:
    store = SessionTokenStore()
    assert store.status().backend == "session"
    store.set("hf_process_only")
    assert store.status().configured is True
    assert store.session_token() == "hf_process_only"
    store.clear()
    assert store.session_token() is None
