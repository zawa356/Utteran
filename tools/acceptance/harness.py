"""Resumable acceptance-test command harness with privacy-safe result capture."""

from __future__ import annotations

import argparse
import json
import locale
import os
import re
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = Path(__file__).with_name("cases.json")
DEFAULT_RESULTS = PROJECT_ROOT / "output" / "_acceptance" / "results.jsonl"
_TOKEN_PATTERN = re.compile(r"\bhf_[A-Za-z0-9_-]{4,}\b")
_ERROR_WORDS = ("error", "failed", "warning", "エラー", "失敗", "警告", "traceback")


@dataclass(frozen=True)
class Case:
    """One declarative command-based acceptance case."""

    case_id: str
    group: str
    description: str
    command: tuple[str, ...]
    expected_exit_codes: tuple[int, ...]
    timeout_seconds: float
    environment: dict[str, str]
    minimum_peak_memory_bytes: int | None


def _windows_utteran() -> Path:
    return PROJECT_ROOT / ".venv-windows" / "Scripts" / "utteran.exe"


def _placeholders() -> dict[str, str]:
    acceptance = PROJECT_ROOT / "output" / "_acceptance"
    return {
        "project": str(PROJECT_ROOT),
        "python": sys.executable,
        "utteran": str(
            _windows_utteran() if os.name == "nt" else PROJECT_ROOT / ".venv/bin/utteran"
        ),
        "testdata": str(PROJECT_ROOT / "output" / "_testdata"),
        "acceptance": str(acceptance),
        "jobs": str(acceptance / "jobs"),
    }


def _expand(value: str, placeholders: dict[str, str]) -> str:
    for name, replacement in placeholders.items():
        value = value.replace("{" + name + "}", replacement)
    return value


def load_cases(path: Path) -> list[Case]:
    """Load ordered case declarations from JSON."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("cases.json must contain a list")
    placeholders = _placeholders()
    cases: list[Case] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each case must be an object")
        case_id = str(item["id"])
        if case_id in seen:
            raise ValueError(f"duplicate case ID: {case_id}")
        seen.add(case_id)
        command = tuple(_expand(str(part), placeholders) for part in item["command"])
        environment = {
            str(key): _expand(str(value), placeholders)
            for key, value in dict(item.get("environment", {})).items()
        }
        cases.append(
            Case(
                case_id=case_id,
                group=str(item["group"]),
                description=str(item["description"]),
                command=command,
                expected_exit_codes=tuple(
                    int(code) for code in item.get("expected_exit_codes", [0])
                ),
                timeout_seconds=float(item.get("timeout_seconds", 600)),
                environment=environment,
                minimum_peak_memory_bytes=(
                    None
                    if item.get("minimum_peak_memory_bytes") is None
                    else int(item["minimum_peak_memory_bytes"])
                ),
            )
        )
    return cases


def _sanitize(value: str) -> str:
    return _TOKEN_PATTERN.sub("hf_****", value).replace("\x00", "")


def _safe_output_summary(output: str, *, edge_lines: int = 4) -> dict[str, list[str]]:
    """Keep only bounded edge/error lines and never persist complete command output."""
    lines = [_sanitize(line)[:500] for line in output.splitlines() if line.strip()]
    errors = [line for line in lines if any(word in line.casefold() for word in _ERROR_WORDS)]
    return {
        "head": lines[:edge_lines],
        "tail": lines[-edge_lines:] if len(lines) > edge_lines else [],
        "errors": errors[:12],
    }


def _read_peak_memory(pid: int) -> int | None:
    """Read current process-tree memory; the caller retains the observed peak."""
    if os.name == "nt":
        return _read_windows_tree_memory(pid)
    return _read_posix_tree_memory(pid)


def _descendant_ids(root_pid: int, parent_by_pid: dict[int, int]) -> set[int]:
    """Return a root PID and all recursively linked descendants."""
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for child, parent in parent_by_pid.items():
            if parent in selected and child not in selected:
                selected.add(child)
                changed = True
    return selected


def _read_posix_tree_memory(pid: int) -> int | None:
    parent_by_pid: dict[int, int] = {}
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            parts = stat_path.read_text(encoding="utf-8").split()
            parent_by_pid[int(parts[0])] = int(parts[3])
        except (OSError, ValueError, IndexError):
            continue
    total = 0
    observed = False
    for process_id in _descendant_ids(pid, parent_by_pid):
        status = Path(f"/proc/{process_id}/status")
        try:
            for line in status.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) * 1024
                    observed = True
                    break
        except (OSError, ValueError, IndexError):
            continue
    return total if observed else None


def _read_windows_tree_memory(pid: int) -> int | None:
    """Sum current working sets for a Windows process and all descendants."""
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return None
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            parent_by_pid: dict[int, int] = {}
            success = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while success:
                parent_by_pid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                success = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        total = 0
        observed = False
        for process_id in _descendant_ids(pid, parent_by_pid):
            handle = kernel32.OpenProcess(0x1000 | 0x0010, False, process_id)
            if not handle:
                continue
            try:
                counters = ProcessMemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    total += int(counters.WorkingSetSize)
                    observed = True
            finally:
                kernel32.CloseHandle(handle)
        return total if observed else None
    except (AttributeError, OSError, ValueError):
        return None


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    """Terminate a timed-out command and its descendants."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def _completed_ids(results_path: Path) -> set[str]:
    completed: set[str] = set()
    if not results_path.is_file():
        return completed
    for line in results_path.read_text(encoding="utf-8").splitlines():
        try:
            completed.add(str(json.loads(line)["id"]))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return completed


def _append_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def run_case(case: Case, results_path: Path) -> dict[str, Any]:
    """Run one case with timeout, process-tree cleanup, and bounded output capture."""
    environment = os.environ.copy()
    environment.update(case.environment)
    popen_options: dict[str, Any] = {
        "cwd": PROJECT_ROOT,
        "env": environment,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": locale.getpreferredencoding(False),
        "errors": "replace",
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True

    started = time.monotonic()
    process = subprocess.Popen(list(case.command), **popen_options)
    captured: dict[str, str] = {"stdout": "", "stderr": ""}

    def communicate() -> None:
        stdout, stderr = process.communicate()
        captured["stdout"] = stdout
        captured["stderr"] = stderr

    thread = threading.Thread(target=communicate, daemon=True)
    thread.start()
    peak_memory: int | None = None
    timed_out = False
    deadline = started + case.timeout_seconds
    while thread.is_alive():
        observed = _read_peak_memory(process.pid)
        if observed is not None:
            peak_memory = max(peak_memory or 0, observed)
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate_tree(process)
            break
        thread.join(0.2)
    thread.join(10)
    if thread.is_alive():
        _terminate_tree(process)
        thread.join(5)

    duration = time.monotonic() - started
    exit_code = process.poll()
    memory_passed = case.minimum_peak_memory_bytes is None or (
        peak_memory is not None and peak_memory >= case.minimum_peak_memory_bytes
    )
    passed = not timed_out and exit_code in case.expected_exit_codes and memory_passed
    if timed_out:
        reason = f"timeout after {case.timeout_seconds:.1f}s; process tree terminated"
    elif not memory_passed:
        reason = f"peak memory {peak_memory} below required {case.minimum_peak_memory_bytes} bytes"
    elif passed:
        reason = f"exit code {exit_code} matched {list(case.expected_exit_codes)}"
    else:
        reason = f"exit code {exit_code} did not match {list(case.expected_exit_codes)}"
    result: dict[str, Any] = {
        "id": case.case_id,
        "group": case.group,
        "description": case.description,
        "command": [_sanitize(part) for part in case.command],
        "environment_keys": sorted(case.environment),
        "exit_code": exit_code,
        "duration_seconds": round(duration, 3),
        "peak_memory_bytes": peak_memory,
        "result": "pass" if passed else "fail",
        "reason": reason,
        "timed_out": timed_out,
        "stdout": _safe_output_summary(captured["stdout"]),
        "stderr": _safe_output_summary(captured["stderr"]),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _append_result(results_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--group", action="append", help="run only this group (repeatable)")
    parser.add_argument("--resume", action="store_true", help="skip IDs already present")
    parser.add_argument(
        "--rerun", action="append", default=[], help="run only this ID, even if present"
    )
    parser.add_argument("--list", action="store_true", help="list selected cases without running")
    args = parser.parse_args()

    cases = load_cases(args.cases)
    selected_groups = set(args.group or [])
    reruns = set(args.rerun)
    if selected_groups:
        cases = [case for case in cases if case.group in selected_groups]
    if reruns:
        known = {case.case_id for case in cases}
        missing = reruns - known
        if missing:
            parser.error(f"unknown rerun IDs: {', '.join(sorted(missing))}")
        cases = [case for case in cases if case.case_id in reruns]
    if args.list:
        for case in cases:
            print(f"{case.case_id}\t{case.group}\t{case.description}")
        return 0

    completed = _completed_ids(args.results) if args.resume and not reruns else set()
    failed = False
    for case in cases:
        if case.case_id in completed:
            print(f"SKIP {case.case_id}: already recorded")
            continue
        print(f"RUN  {case.case_id}: {case.description}", flush=True)
        result = run_case(case, args.results)
        print(
            f"{result['result'].upper():4} {case.case_id}: {result['reason']} "
            f"({result['duration_seconds']}s)",
            flush=True,
        )
        failed = failed or result["result"] != "pass"
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
