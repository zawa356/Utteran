"""Observe-only OS process identity for B7 recovery. Never signals or terminates.

A PID alone is never treated as proof. ``start_identity`` combines the PID with the OS
creation time (Linux: boot id + /proc starttime; Windows: process creation FILETIME).
Anything the platform cannot prove becomes ``unknown`` or ``pid_reused``.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from utteran_contracts.control import OwnerIdentity, ProcessIdentity
from utteran_control.recovery import Liveness

_PROC = Path("/proc")


def _linux_boot_id() -> str | None:
    try:
        value = (_PROC / "sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return None
    return value.replace("-", "") if value else None


def _linux_stat(pid: int) -> tuple[str, str] | None:
    """(state, starttime) from /proc/<pid>/stat, or None when the PID is absent."""
    try:
        raw = (_PROC / str(pid) / "stat").read_text(encoding="ascii", errors="replace")
    except FileNotFoundError:
        return None
    fields = raw[raw.rindex(")") + 2 :].split()
    return fields[0], fields[19]


if sys.platform == "win32":  # pragma: no cover - exercised by the Windows CI job
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _OpenProcess = _kernel32.OpenProcess
    _OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _OpenProcess.restype = wintypes.HANDLE
    _GetProcessTimes = _kernel32.GetProcessTimes
    _GetExitCodeProcess = _kernel32.GetExitCodeProcess
    _CloseHandle = _kernel32.CloseHandle
    _QUERY_LIMITED = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_INVALID_PARAMETER = 87

    def _windows(pid: int) -> tuple[str, bool] | Liveness | None:
        """(creation filetime, running) / None when absent / UNKNOWN when unobservable."""
        handle = _OpenProcess(_QUERY_LIMITED, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            return None if error == _ERROR_INVALID_PARAMETER else Liveness.UNKNOWN
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not _GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return Liveness.UNKNOWN
            code = wintypes.DWORD()
            if not _GetExitCodeProcess(handle, ctypes.byref(code)):
                return Liveness.UNKNOWN
            stamp = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return str(stamp), code.value == _STILL_ACTIVE
        finally:
            _CloseHandle(handle)


def start_identity(pid: int) -> str | None:
    """OS creation identity of a live PID, or None when it cannot be established."""
    if pid < 1:
        return None
    try:
        if sys.platform == "win32":  # pragma: no cover - Windows CI
            observed = _windows(pid)
            if isinstance(observed, tuple) and observed[1]:
                return "win:" + observed[0]
            return None
        if sys.platform.startswith("linux"):
            boot = _linux_boot_id()
            stat = _linux_stat(pid)
            if boot is None or stat is None or stat[0] in {"Z", "X"}:
                return None
            return f"linux:{boot}:{stat[1]}"
    except (OSError, ValueError, IndexError):
        return None
    return None


class OsProcessVerifier:
    """Recovery verifier backed by the OS. Observation only."""

    def check(self, process: ProcessIdentity) -> Liveness:
        try:
            return self._check(process)
        except (OSError, ValueError, IndexError):
            return Liveness.UNKNOWN

    @staticmethod
    def _check(process: ProcessIdentity) -> Liveness:
        recorded = process.start_identity
        if sys.platform == "win32":  # pragma: no cover - Windows CI
            observed = _windows(process.pid)
            if observed is None:
                return Liveness.GONE
            if isinstance(observed, Liveness):
                return observed
            if recorded is None:
                return Liveness.UNKNOWN
            if "win:" + observed[0] != recorded:
                return Liveness.PID_REUSED
            return Liveness.ALIVE if observed[1] else Liveness.GONE
        if sys.platform.startswith("linux"):
            stat = _linux_stat(process.pid)
            if stat is None:
                return Liveness.GONE  # no such PID: the recorded process cannot be running
            if recorded is None:
                return Liveness.UNKNOWN
            boot = _linux_boot_id()
            if boot is None:
                return Liveness.UNKNOWN
            if f"linux:{boot}:{stat[1]}" != recorded:
                return Liveness.PID_REUSED
            # A zombie has exited (it can no longer write); only its PID entry remains.
            return Liveness.GONE if stat[0] in {"Z", "X"} else Liveness.ALIVE
        try:
            os.kill(process.pid, 0)
        except ProcessLookupError:
            return Liveness.GONE
        except PermissionError:
            return Liveness.UNKNOWN
        return Liveness.UNKNOWN  # no creation identity available on this platform


def current_owner() -> OwnerIdentity:
    """Fresh private owner identity for this GUI process. The nonce is never logged."""
    pid = os.getpid()
    return OwnerIdentity(
        owner_id="own_" + secrets.token_hex(16),
        nonce=secrets.token_hex(32),
        process=ProcessIdentity(pid=pid, start_identity=start_identity(pid)),
    )
