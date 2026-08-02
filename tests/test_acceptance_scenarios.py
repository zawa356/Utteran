from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows console control regression")
def test_ctrl_c_is_confined_to_the_child_console() -> None:
    probe = """
import sys
import time
from tools.acceptance.scenarios import _interrupt, _start

child = _start([
    sys.executable,
    "-c",
    "import time;\\ntry: time.sleep(60)\\nexcept KeyboardInterrupt: raise SystemExit(130)",
])
time.sleep(1)
exit_code, _stdout, _stderr = _interrupt(child)
raise SystemExit(0 if exit_code == 130 else 1)
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
