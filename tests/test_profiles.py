from __future__ import annotations

from pathlib import Path

import pytest

from utteran.errors import ConfigurationError
from utteran.profiles import (
    PROFILE_EXTRAS,
    PROFILE_NAMES,
    ProfileStatus,
    UnknownProfileError,
    current_profile_name,
    default_profile_name,
    list_profile_statuses,
    os_slug,
    profile_extras,
    resolve_venv_root,
    validate_profile_name,
    venv_dir_name,
    venv_path,
)


def test_profile_names_match_the_documented_extras_table() -> None:
    assert PROFILE_NAMES == ("cpu", "cuda", "intel", "vulkan")
    assert PROFILE_EXTRAS["cpu"] == ("cpu",)
    assert PROFILE_EXTRAS["cuda"] == ("cuda",)
    assert PROFILE_EXTRAS["intel"] == ("xpu", "whisper-cpp", "openvino")
    assert PROFILE_EXTRAS["vulkan"] == ("cpu", "whisper-cpp")


def test_unknown_profile_name_is_rejected() -> None:
    with pytest.raises(UnknownProfileError):
        validate_profile_name("rocm")
    with pytest.raises(UnknownProfileError):
        profile_extras("rocm")


def test_venv_dir_name_combines_os_slug_and_profile() -> None:
    name = venv_dir_name("cpu")
    assert name == f"{os_slug()}-cpu"


def test_venv_path_is_below_the_resolved_root(tmp_path: Path) -> None:
    root = tmp_path / ".venvs"
    path = venv_path(root, "cuda")
    assert path == root / f"{os_slug()}-cuda"


def test_resolve_venv_root_prefers_explicit_config_over_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UTTERAN_VENV_DIR", str(tmp_path / "from-env"))
    explicit = tmp_path / "from-config"

    assert resolve_venv_root(tmp_path, configured=explicit) == explicit


def test_resolve_venv_root_uses_env_over_repo_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_dir = tmp_path / "from-env"
    monkeypatch.setenv("UTTERAN_VENV_DIR", str(env_dir))

    assert resolve_venv_root(tmp_path) == env_dir


def test_resolve_venv_root_defaults_to_repo_relative_dot_venvs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UTTERAN_VENV_DIR", raising=False)

    assert resolve_venv_root(tmp_path) == tmp_path / ".venvs"


def test_default_profile_name_uses_explicit_config_value(tmp_path: Path) -> None:
    assert default_profile_name("cuda", tmp_path) == "cuda"


def test_default_profile_name_uses_the_sole_existing_profile(tmp_path: Path) -> None:
    (tmp_path / venv_dir_name("cpu")).mkdir(parents=True)

    assert default_profile_name(None, tmp_path) == "cpu"


def test_default_profile_name_errors_when_none_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="作成済みのプロファイル"):
        default_profile_name(None, tmp_path)


def test_default_profile_name_errors_when_multiple_exist_and_unset(tmp_path: Path) -> None:
    (tmp_path / venv_dir_name("cpu")).mkdir(parents=True)
    (tmp_path / venv_dir_name("cuda")).mkdir(parents=True)

    with pytest.raises(ConfigurationError, match="複数のプロファイルが存在"):
        default_profile_name(None, tmp_path)


def test_current_profile_name_reads_the_run_ps1_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UTTERAN_PROFILE", "vulkan")
    assert current_profile_name() == "vulkan"

    monkeypatch.setenv("UTTERAN_PROFILE", "not-a-real-profile")
    assert current_profile_name() is None

    monkeypatch.delenv("UTTERAN_PROFILE", raising=False)
    assert current_profile_name() is None


def test_list_profile_statuses_reports_existence_size_and_timestamp(tmp_path: Path) -> None:
    created = tmp_path / venv_dir_name("cpu")
    created.mkdir(parents=True)
    (created / "marker.txt").write_bytes(b"0123456789")

    statuses = list_profile_statuses(tmp_path)

    by_name = {status.name: status for status in statuses}
    assert set(by_name) == set(PROFILE_NAMES)
    assert by_name["cpu"].exists is True
    assert by_name["cpu"].size_bytes == 10
    assert by_name["cpu"].updated_at is not None
    assert by_name["cuda"].exists is False
    assert by_name["cuda"].size_bytes is None
    assert isinstance(by_name["cpu"], ProfileStatus)


def test_setup_forces_utf8_for_devices_json() -> None:
    setup_script = (Path(__file__).parents[1] / "setup.ps1").read_text(encoding="utf-8")

    helper = setup_script.index("function Invoke-Utf8Captured")
    json_call = setup_script.index("utteran devices --json")
    encoding_assignment = setup_script.index('$env:PYTHONIOENCODING = "utf-8"', helper)
    console_encoding = setup_script.index(
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8", helper
    )
    json_parse = setup_script.index("ConvertFrom-Json", json_call)

    assert helper < encoding_assignment < console_encoding < json_call < json_parse
    assert "Invoke-Utf8Captured" in setup_script[json_call - 100 : json_call]
    assert "Remove-Item Env:PYTHONIOENCODING" in setup_script[helper:json_call]
    assert "[Console]::OutputEncoding = $PreviousConsoleEncoding" in setup_script


def test_start_forces_utf8_for_captured_json() -> None:
    start_script = (Path(__file__).parents[1] / "start.ps1").read_text(encoding="utf-8")

    helper = start_script.index("function Invoke-Utf8Captured")
    json_helper = start_script.index("function Get-UtteranJson", helper)
    json_capture = start_script.index("$Raw = Invoke-Utf8Captured", json_helper)
    json_parse = start_script.index("ConvertFrom-Json", json_capture)

    assert helper < json_helper < json_capture < json_parse
    helper_body = start_script[helper:json_helper]
    assert '$env:PYTHONIOENCODING = "utf-8"' in helper_body
    assert "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8" in helper_body
    assert "Remove-Item Env:PYTHONIOENCODING" in helper_body
    assert "[Console]::OutputEncoding = $PreviousConsoleEncoding" in helper_body


def test_setup_feeds_vulkan_probe_over_stdin_for_powershell_51() -> None:
    setup_script = (Path(__file__).parents[1] / "setup.ps1").read_text(encoding="utf-8")

    probe_function = setup_script.index("function Invoke-VulkanPrerequisiteCheck")
    profile_setup = setup_script.index("function Invoke-ProfileSetup", probe_function)
    probe_body = setup_script[probe_function:profile_setup]

    assert "$Result = $Probe | & $PythonExe - 2>&1" in probe_body
    assert "& $PythonExe -c $Probe" not in probe_body
