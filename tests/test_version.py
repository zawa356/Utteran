from __future__ import annotations

import re
import tomllib
from pathlib import Path

import utteran
import utteran_gui


def test_package_version_matches_project_metadata() -> None:
    project = Path(__file__).parents[1]
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))

    assert utteran.__version__ == metadata["project"]["version"]
    assert utteran_gui.__version__ == metadata["project"]["version"]


def test_readme_versions_match_project_metadata() -> None:
    project = Path(__file__).parents[1]
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    expected = metadata["project"]["version"]

    for name in ("README.md", "README.en.md"):
        readme = (project / name).read_text(encoding="utf-8")
        match = re.search(r"^> .*development version: `([^`]+)`", readme, re.MULTILINE | re.I)
        if name == "README.md":
            match = re.search(r"^> 現在の開発版: `([^`]+)`", readme, re.MULTILINE)
        assert match is not None, f"{name} must state the current development version"
        assert match.group(1) == expected


def test_packages_resolve_version_from_distribution_metadata() -> None:
    project = Path(__file__).parents[1]

    for package in ("utteran", "utteran_gui"):
        source = (project / "src" / package / "__init__.py").read_text(encoding="utf-8")
        assert '__version__ = version("utteran")' in source
        assert not re.search(r'__version__\s*=\s*["\']\d', source)

    spec = (project / "packaging" / "gui.spec").read_text(encoding="utf-8")
    assert '*copy_metadata("utteran")' in spec


def test_windows_build_embeds_and_verifies_project_version() -> None:
    project = Path(__file__).parents[1]
    spec = (project / "packaging" / "gui.spec").read_text(encoding="utf-8")
    build = (project / "build.ps1").read_text(encoding="utf-8")

    assert 'os.environ.get("UTTERAN_BUILD_VERSION"' in spec
    assert 'StringStruct("ProductVersion", BUILD_VERSION)' in spec
    assert "version=version_info" in spec
    assert "$env:UTTERAN_BUILD_VERSION = $Version" in build
    assert "VersionInfo.ProductVersion" in build
    assert "VersionInfo.FileVersion" in build
    assert '"utteran-setup-$Version.exe"' in build

    installer = (project / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "AppVersion={#MyAppVersion}" in installer
    assert "VersionInfoVersion={#MyAppVersion}" in installer
    assert "$InstallerVersionInfo.ProductVersion" in build
    assert "$InstallerVersionInfo.FileVersion" in build


def test_installer_does_not_launch_gui_before_setup_exits() -> None:
    """Postinstall children inherit Inno Setup's RedirectionGuard policy."""
    project = Path(__file__).parents[1]
    installer = (project / "packaging" / "installer.iss").read_text(encoding="utf-8")
    directives = "\n".join(
        line for line in installer.splitlines() if not line.lstrip().startswith(";")
    )

    assert "[Run]" not in directives
    assert "postinstall" not in directives


def test_uninstaller_always_removes_reproducible_runtime_but_preserves_user_data_by_default() -> (
    None
):
    project = Path(__file__).parents[1]
    installer = (project / "packaging" / "installer.iss").read_text(encoding="utf-8")
    runtime = installer.split("procedure DeleteRuntimeData();", 1)[1].split(
        "function InitializeUninstall", 1
    )[0]
    initialize = installer.split("function InitializeUninstall(): Boolean;", 1)[1].split(
        "procedure CurUninstallStepChanged", 1
    )[0]

    for target in (
        "VenvsDir()",
        "ModelsDir()",
        "GenAICompiledCacheDir()",
        "DeviceProbeCacheFile()",
        "NativeBuildDir()",
        "DeleteFfmpegFiles()",
    ):
        assert target in runtime
    assert "DeleteRuntimeData();" in installer.split("if CurUninstallStep = usUninstall", 1)[1]
    assert "if UninstallSilent() then" in initialize
    for user_choice in ("DeleteUserSettings", "DeleteJobs", "DeleteLogs", "DeleteToken"):
        assert f"{user_choice} := False" in initialize


def test_uninstaller_deletes_keyring_token_only_after_interactive_consent() -> None:
    installer = (Path(__file__).parents[1] / "packaging" / "installer.iss").read_text(
        encoding="utf-8"
    )

    assert "DeleteToken :=" in installer
    assert "and DeleteToken" not in installer
    assert "if DeleteToken then" in installer
    assert "--delete-keyring-token" in installer


def test_build_creates_installer_and_portable_artifacts() -> None:
    root = Path(__file__).parents[1]
    build = (root / "build.ps1").read_text(encoding="utf-8-sig")
    spec = (root / "packaging" / "gui.spec").read_text(encoding="utf-8")
    hook = (root / "packaging" / "portable_runtime.py").read_text(encoding="utf-8")

    assert '"utteran-setup-$Version.exe"' in build
    assert '"utteran-portable-$Version.zip"' in build
    assert "Compress-Archive" in build
    assert 'UTTERAN_BUILD_FLAVOR") == "portable"' in spec
    assert 'os.environ["UTTERAN_DATA_ROOT"]' in hook
    assert 'os.environ["UTTERAN_TOKEN_MODE"] = "session"' in hook
    assert '$ReleaseDir = Join-Path $DistDir "release"' in build
    assert '$StagingDir = Join-Path $DistDir "staging"' in build
    assert 'Join-Path $ReleaseDir "utteran-setup-$Version.exe"' in build
    assert 'Join-Path $ReleaseDir "utteran-portable-$Version.zip"' in build
    assert "--distpath $StagingDir" in build
    for variable in ("UV_CACHE_DIR", "HF_HOME", "TORCH_HOME", "WEBVIEW2_USER_DATA_FOLDER"):
        assert f'os.environ["{variable}"]' in hook

    installer = (root / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "OutputDir={#RepoRoot}\\dist\\release" in installer
    assert "..\\dist\\staging\\installer-gui" in installer


def test_python_launcher_is_scoped_diagnostic_and_non_installing() -> None:
    root = Path(__file__).parents[1]
    batch = (root / "launch-python.bat").read_text(encoding="utf-8")
    launcher = (root / "launch-python.ps1").read_text(encoding="utf-8")

    assert "-ExecutionPolicy Bypass" in batch
    assert "Set-ExecutionPolicy" not in batch + launcher
    assert "UTTERAN_PYTHON_DIRECT" in launcher
    assert "-m utteran_gui" in launcher
    assert "Start-Transcript" in launcher
    assert "Install-Uv" not in launcher
    assert "Invoke-WebRequest" not in launcher
    for prerequisite in ("Python", "uv", "ffmpeg"):
        assert prerequisite in launcher


def test_installer_explicitly_closes_running_app_and_blocks_silent_downgrade() -> None:
    installer = (Path(__file__).parents[1] / "packaging" / "installer.iss").read_text(
        encoding="utf-8"
    )

    assert "CloseApplications=force" in installer
    assert "RestartApplications=no" in installer
    initialize = installer.split("function InitializeSetup(): Boolean;", 1)[1].split(
        "procedure InitializeWizard", 1
    )[0]
    assert "CompareVersions(InstalledVersion, '{#MyAppVersion}') > 0" in initialize
    assert "if WizardSilent() then" in initialize
    assert "Result := False" in initialize
    assert "DowngradeWarning" in initialize
