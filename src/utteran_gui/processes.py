"""Shared subprocess-tree launch/kill primitives for GUI job runners.

Extracted from jobs.py so setup_wizard.py's SetupWizardService can launch and
cancel its own subprocess trees (setup.ps1, profile utteran.exe) with the same
platform-correct isolation and termination semantics as JobManager, instead of
duplicating them.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

PopenFactory = Callable[..., subprocess.Popen[str]]
TreeKiller = Callable[[subprocess.Popen[str]], None]


def build_popen_kwargs(*, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    """Build Popen kwargs that isolate a new process group/session for tree cancellation."""
    kwargs: dict[str, object] = {
        "cwd": cwd,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return kwargs


def kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate an entire CLI process tree using the platform's reliable primitive."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
