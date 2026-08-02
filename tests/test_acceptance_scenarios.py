from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def test_security_token_scan_ignores_config_key_but_finds_token_values() -> None:
    namespace = runpy.run_path(Path(__file__).parents[1] / "tools" / "acceptance" / "scenarios.py")
    token_bytes_pattern = namespace["TOKEN_BYTES_PATTERN"]
    assert token_bytes_pattern.search(b'hf_token = "dummy-token-value"') is None
    assert token_bytes_pattern.search(b"value=hf_acceptanceDummyToken123456") is not None


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
