from __future__ import annotations

import tomllib
from pathlib import Path

import utteran


def test_package_version_matches_project_metadata() -> None:
    project = Path(__file__).parents[1]
    metadata = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))

    assert utteran.__version__ == metadata["project"]["version"]
