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
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast

PopenFactory = Callable[..., subprocess.Popen[str]]
TreeKiller = Callable[[subprocess.Popen[str]], None]


class ProcessSupervisor:
    """Own GUI children and make parent death terminate them on Windows.

    Process groups are useful for explicit cancellation but do not tie a
    child's lifetime to its parent. A Windows Job Object with
    ``KILL_ON_JOB_CLOSE`` supplies that missing lifetime boundary: Windows
    closes the GUI's only job handle even after a crash or forced exit.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._processes: dict[int, subprocess.Popen[str]] = {}
        self._job_handle = _create_kill_on_close_job() if os.name == "nt" else None
        self._closed = False

    def popen(self, command: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        """Start and immediately attach one direct child to this supervisor."""
        with self._lock:
            if self._closed:
                raise RuntimeError("GUI process supervisor is shutting down")
        process = cast(subprocess.Popen[str], subprocess.Popen(command, **kwargs))
        try:
            if self._job_handle is not None:
                _assign_process_to_job(self._job_handle, process)
        except Exception:
            kill_process_tree(process)
            raise
        with self._lock:
            if self._closed:
                kill_process_tree(process)
                raise RuntimeError("GUI process supervisor shut down during process launch")
            self._processes[process.pid] = process
        return process

    def shutdown(self, *, timeout: float = 5.0) -> None:
        """Stop direct children first, wait briefly, then close the job handle."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            processes = list(self._processes.values())
        for process in processes:
            if process.poll() is None:
                kill_process_tree(process)
        deadline = time.monotonic() + timeout
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                continue
        if self._job_handle is not None:
            _close_windows_handle(self._job_handle)
            self._job_handle = None

    def active_pids(self) -> tuple[int, ...]:
        """Return currently live direct children for diagnostics and tests."""
        with self._lock:
            return tuple(pid for pid, process in self._processes.items() if process.poll() is None)


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


def _create_kill_on_close_job() -> int:
    """Create a Windows Job Object whose members die when the GUI handle closes."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(
        handle, 9, ctypes.byref(information), ctypes.sizeof(information)
    ):
        error = ctypes.get_last_error()
        _close_windows_handle(int(handle))
        raise ctypes.WinError(error)
    return int(handle)


def _assign_process_to_job(handle: int, process: subprocess.Popen[str]) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    process_handle = getattr(process, "_handle", None)
    if process_handle is None or not kernel32.AssignProcessToJobObject(handle, process_handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)
