"""Tests never inherit a developer's or an acceptance run's data/model/log location.

PR #48 Windows re-acceptance ran the whole suite with the acceptance UTTERAN_DATA_ROOT
still set: tests wrote settings/job records into that root and failed on its contents.
The autouse fixture in conftest.py removes location/mode variables for every test.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from utteran_paths import resolve_data_paths

LOCATION_KEYS = (
    "UTTERAN_DATA_ROOT",
    "UTTERAN_DISTRIBUTION",
    "UTTERAN_TOKEN_MODE",
    "UTTERAN_MODEL_DIR",
    "UTTERAN_PROJECT_ROOT",
    "UTTERAN_GUI_CHILD_LOG_DIR",
)
MARK = "UTTERAN_TEST_AMBIENT_ROOT_UNDER_TEST"


def test_location_and_core_setting_variables_are_absent_inside_a_test() -> None:
    assert not [k for k in LOCATION_KEYS if k in os.environ]
    assert not [k for k in os.environ if k.upper().startswith("UTTERAN_ASR__")]
    ambient = os.environ.get(MARK)
    if ambient:  # only when started by the outer test below
        # No explicit root: the product falls back to its default, never the ambient one.
        assert resolve_data_paths().root is None


def test_a_suite_started_with_an_acceptance_root_does_not_touch_it(tmp_path: Path) -> None:
    root = tmp_path / "acceptance-root"
    root.mkdir()
    (root / "owner-file").write_text("synthetic", encoding="utf-8")
    env = {
        **os.environ,
        "UTTERAN_DATA_ROOT": str(root),
        "UTTERAN_MODEL_DIR": str(root / "models"),
        "UTTERAN_ASR__MODEL": "must-not-apply",
        MARK: str(root),
    }
    inner = test_location_and_core_setting_variables_are_absent_inside_a_test.__name__
    node = f"{Path(__file__).as_posix()}::{inner}"
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
        env=env,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout.decode(errors="replace")[-2000:]
    assert sorted(p.name for p in root.iterdir()) == ["owner-file"]
