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
from typing import TypedDict, cast

PopenFactory = Callable[..., subprocess.Popen[str]]
TreeKiller = Callable[[subprocess.Popen[str]], None]


class CreationKwargs(TypedDict, total=False):
    creationflags: int


def build_creation_kwargs(*, new_process_group: bool = False) -> CreationKwargs:
    """Return Windows flags that keep GUI child processes invisible.

    Long-running job trees additionally retain ``CREATE_NEW_PROCESS_GROUP``
    for cancellation. Every Windows child gets ``CREATE_NO_WINDOW`` so a
    console-less GUI never flashes a console when starting a CLI program.
    """
    if os.name != "nt":
        return {}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if new_process_group:
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return {"creationflags": flags}


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
        kwargs.update(build_creation_kwargs(new_process_group=True))
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
            **build_creation_kwargs(),
        )
        return
    # os.killpg/signal.SIGKILL are POSIX-only; typeshed omits them from the
    # win32 stubs, so mypy fails here when run on a Windows machine even
    # though this branch never executes there (os.name == "nt" returns
    # above). CI's type check runs on Linux and would not catch this, so
    # resolve both through getattr to stay mypy-clean on every platform,
    # matching the precedent in utteran.native for the same class of issue.
    killpg = cast(Callable[[int, int], None], getattr(os, "killpg"))  # noqa: B009
    sigkill = cast(int, getattr(signal, "SIGKILL", signal.SIGTERM))
    try:
        killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        killpg(process.pid, sigkill)
    except ProcessLookupError:
        return
