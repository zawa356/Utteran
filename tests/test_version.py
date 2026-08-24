from __future__ import annotations

import tomllib
from pathlib import Path

import utteran
import utteran_gui


def test_package_version_matches_project_metadata() -> None:
    project = Path(__file__).parents[1]
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))

    assert utteran.__version__ == metadata["project"]["version"]
    assert utteran_gui.__version__ == metadata["project"]["version"]


def test_windows_build_embeds_and_verifies_project_version() -> None:
    project = Path(__file__).parents[1]
    spec = (project / "packaging" / "gui.spec").read_text(encoding="utf-8")
    build = (project / "build.ps1").read_text(encoding="utf-8")

    assert 'os.environ.get("UTTERAN_BUILD_VERSION"' in spec
    assert 'StringStruct("ProductVersion", BUILD_VERSION)' in spec
    assert "version=version_info" in spec
    assert "$env:UTTERAN_BUILD_VERSION = $Version" in build
    assert ".VersionInfo.ProductVersion" in build
    assert '"utteran-setup-$Version.exe"' in build


def test_installer_does_not_launch_gui_before_setup_exits() -> None:
    """Postinstall children inherit Inno Setup's RedirectionGuard policy."""
    project = Path(__file__).parents[1]
    installer = (project / "packaging" / "installer.iss").read_text(encoding="utf-8")
    directives = "\n".join(
        line for line in installer.splitlines() if not line.lstrip().startswith(";")
    )

    assert "[Run]" not in directives
    assert "postinstall" not in directives
