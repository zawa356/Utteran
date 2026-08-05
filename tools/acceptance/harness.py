"""Resumable acceptance-test command harness with privacy-safe result capture.

Importable as a Python API (``import tools.acceptance.harness as harness``) so a future
GUI can call :func:`run_selected` directly instead of shelling out to this file's CLI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES = Path(__file__).with_name("cases.json")
DEFAULT_RESULTS = PROJECT_ROOT / "output" / "_acceptance" / "results.jsonl"
LONG_GROUPS = {"G13", "P14"}
_TOKEN_PATTERN = re.compile(r"\bhf_[A-Za-z0-9_-]{4,}\b")
_ERROR_WORDS = ("error", "failed", "warning", "エラー", "失敗", "警告", "traceback")


@dataclass(frozen=True)
class Case:
    """One declarative command-based acceptance case.

    ``requires``/``destructive``/``estimated_seconds`` are the metadata a future GUI needs
    to decide, ahead of time, what this environment can run: ``requires`` is matched
    against a live ``devices --json`` / ``models list --json`` snapshot (see
    :func:`unmet_requirements`); cases whose requirements are unmet are recorded as
    ``skip`` rather than attempted or failed.
    """

    case_id: str
    group: str
    description: str
    command: tuple[str, ...]
    expected_exit_codes: tuple[int, ...]
    timeout_seconds: float
    environment: dict[str, str]
    minimum_peak_memory_bytes: int | None
    measure_vram: bool
    requires: dict[str, Any] = field(default_factory=dict)
    destructive: bool = False
    estimated_seconds: float | None = None


def _windows_utteran() -> Path:
    override = os.environ.get("UTTERAN_ACCEPTANCE_UTTERAN")
    if override:
        return Path(override)
    intel = PROJECT_ROOT / ".venvs" / "win-intel" / "Scripts" / "utteran.exe"
    if intel.is_file():
        return intel
    return PROJECT_ROOT / ".venv-windows" / "Scripts" / "utteran.exe"


def _placeholders(results_path: Path = DEFAULT_RESULTS) -> dict[str, str]:
    acceptance = Path(
        os.environ.get("UTTERAN_ACCEPTANCE_ROOT", PROJECT_ROOT / "output" / "_acceptance")
    )
    actual_files = sorted((PROJECT_ROOT / "input").glob("*.mp4"))
    return {
        "project": str(PROJECT_ROOT),
        "python": sys.executable,
        "utteran": str(
            _windows_utteran() if os.name == "nt" else PROJECT_ROOT / ".venv/bin/utteran"
        ),
        "testdata": str(PROJECT_ROOT / "output" / "_testdata"),
        "actual": str(actual_files[0] if actual_files else PROJECT_ROOT / "input" / "missing.mp4"),
        "acceptance": str(acceptance),
        "jobs": str(acceptance / "jobs"),
        "results": str(results_path),
    }


def _expand(value: str, placeholders: dict[str, str]) -> str:
    for name, replacement in placeholders.items():
        value = value.replace("{" + name + "}", replacement)
    return value


def load_cases(path: Path, *, results_path: Path = DEFAULT_RESULTS) -> list[Case]:
    """Load ordered case declarations from JSON."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("cases.json must contain a list")
    placeholders = _placeholders(results_path)
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
                measure_vram=bool(item.get("measure_vram", False)),
                requires=dict(item.get("requires") or {}),
                destructive=bool(item.get("destructive", False)),
                estimated_seconds=(
                    None
                    if item.get("estimated_seconds") is None
                    else float(item["estimated_seconds"])
                ),
            )
        )
    return cases


def select_cases(
    cases: list[Case],
    *,
    groups: set[str] | None = None,
    rerun: set[str] | None = None,
    include_long: bool = False,
    include_destructive: bool = False,
) -> list[Case]:
    """Apply the same group/long/destructive/rerun filtering the CLI and API share."""
    selected = cases
    if groups:
        selected = [case for case in selected if case.group in groups]
    else:
        if not include_long:
            selected = [case for case in selected if case.group not in LONG_GROUPS]
        if not include_destructive:
            selected = [case for case in selected if not case.destructive]
    if rerun:
        selected = [case for case in selected if case.case_id in rerun]
    return selected


def _capture_json(command: list[str], *, environment: dict[str, str] | None = None) -> Any:
    """Run a read-only CLI command and parse its JSON stdout, tolerating decode failure."""
    merged_env = None
    if environment:
        merged_env = os.environ.copy()
        merged_env.update(environment)
    try:
        completed = subprocess.run(
            command, cwd=PROJECT_ROOT, capture_output=True, timeout=60, check=False, env=merged_env
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    try:
        return json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _profile_from_executable(utteran: Path) -> str | None:
    """Infer a profile name from a resolved ``.venvs/<os>-<profile>/...`` executable path.

    ``devices --json``'s own ``profile.current`` depends on the ``UTTERAN_PROFILE``
    environment variable that only ``run.ps1``/``start.ps1`` set (AISTATE.md); invoking
    ``{utteran}`` directly, as this harness and a future GUI both do, leaves it unset. The
    executable path itself already encodes which profile it belongs to, so this recovers
    the same information without requiring the wrapper scripts.
    """
    for parent in utteran.parents:
        if "-" in parent.name and parent.name.split("-", 1)[0] in {"win", "linux"}:
            return parent.name.split("-", 1)[1]
    return None


def fetch_environment(utteran: Path) -> dict[str, Any]:
    """Snapshot ``devices --json`` and ``models list --json`` once per harness run.

    This is the "environment" future GUIs can reuse to answer "what can I run here" -
    the same question :func:`unmet_requirements` answers per-case.
    """
    profile = _profile_from_executable(utteran)
    environment = {"UTTERAN_PROFILE": profile} if profile else None
    devices = _capture_json([str(utteran), "devices", "--json"], environment=environment)
    raw_models = _capture_json([str(utteran), "models", "list", "--json"], environment=environment)
    models = (
        {str(item["key"]): item for item in raw_models if isinstance(item, dict) and "key" in item}
        if isinstance(raw_models, list)
        else {}
    )
    return {"devices": devices if isinstance(devices, dict) else {}, "models": models}


def unmet_requirements(requires: dict[str, Any], environment: dict[str, Any] | None) -> list[str]:
    """Return human-readable reasons a case cannot run here; empty means runnable.

    ``requires`` keys mirror the four condition categories a future GUI needs
    (profile / backend / native build / model / hardware device):

    - ``profile``: exact profile name, or list of acceptable names
    - ``backends``: list of ``devices.backends`` keys that must be available
    - ``native_variants``: list of whisper.cpp native build variants that must be runnable
    - ``models``: list of catalog keys (``<backend>:<model-id>``) that must be installed
    - ``cuda`` / ``xpu``: bool, require the matching accelerator to be usable
    """
    if not requires:
        return []
    if environment is None:
        return ["environment probe unavailable"]
    devices = environment.get("devices") or {}
    models = environment.get("models") or {}
    reasons: list[str] = []
    profile = requires.get("profile")
    if profile is not None:
        allowed = {profile} if isinstance(profile, str) else set(profile)
        current = (devices.get("profile") or {}).get("current")
        if current not in allowed:
            reasons.append(f"profile must be one of {sorted(allowed)}, current is {current!r}")
    for backend in requires.get("backends", ()):
        if not (devices.get("backends") or {}).get(backend):
            reasons.append(f"backend not available: {backend}")
    for variant in requires.get("native_variants", ()):
        if not ((devices.get("native") or {}).get("variants") or {}).get(variant):
            reasons.append(f"native build not runnable: {variant}")
    for key in requires.get("models", ()):
        if not (models.get(key) or {}).get("installed"):
            reasons.append(f"model not installed: {key}")
    if requires.get("cuda") and not (devices.get("ctranslate2") or {}).get("cuda_device_count"):
        reasons.append("CUDA hardware not present")
    if requires.get("xpu") and not (devices.get("pytorch") or {}).get("xpu_available"):
        reasons.append("XPU not available")
    return reasons


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


def _parse_gpu_memory(output: str) -> tuple[int, int] | None:
    """Parse nvidia-smi's first used,total MiB row as bytes."""
    first = next((line.strip() for line in output.splitlines() if line.strip()), "")
    if not first:
        return None
    try:
        used, total = (int(value.strip()) * 1024**2 for value in first.split(",", 1))
    except (TypeError, ValueError):
        return None
    return used, total


def _read_gpu_memory() -> tuple[int, int] | None:
    """Read total GPU memory use where per-process WDDM accounting is unavailable."""
    candidates = (
        [Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "nvidia-smi.exe"]
        if os.name == "nt"
        else [Path("/usr/bin/nvidia-smi")]
    )
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [
                str(executable),
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _parse_gpu_memory(completed.stdout) if completed.returncode == 0 else None


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
        # utteran and Python children emit UTF-8; locale decoding caused the
        # mojibake that invalidated 47 legacy expectations on Japanese Windows.
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_options["start_new_session"] = True

    started = time.monotonic()
    baseline_vram = _read_gpu_memory() if case.measure_vram else None
    process = subprocess.Popen(list(case.command), **popen_options)
    captured: dict[str, str] = {"stdout": "", "stderr": ""}

    def communicate() -> None:
        stdout, stderr = process.communicate()
        captured["stdout"] = stdout
        captured["stderr"] = stderr

    thread = threading.Thread(target=communicate, daemon=True)
    thread.start()
    peak_memory: int | None = None
    peak_vram_bytes = baseline_vram[0] if baseline_vram is not None else None
    total_vram_bytes = baseline_vram[1] if baseline_vram is not None else None
    timed_out = False
    deadline = started + case.timeout_seconds
    while thread.is_alive():
        observed = _read_peak_memory(process.pid)
        if observed is not None:
            peak_memory = max(peak_memory or 0, observed)
        if case.measure_vram:
            observed_vram = _read_gpu_memory()
            if observed_vram is not None:
                peak_vram_bytes = max(peak_vram_bytes or 0, observed_vram[0])
                total_vram_bytes = observed_vram[1]
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
        "vram_baseline_bytes": None if baseline_vram is None else baseline_vram[0],
        "peak_vram_bytes": peak_vram_bytes,
        "peak_vram_delta_bytes": (
            None
            if baseline_vram is None or peak_vram_bytes is None
            else max(0, peak_vram_bytes - baseline_vram[0])
        ),
        "total_vram_bytes": total_vram_bytes,
        "result": "pass" if passed else "fail",
        "reason": reason,
        "timed_out": timed_out,
        "stdout": _safe_output_summary(captured["stdout"]),
        "stderr": _safe_output_summary(captured["stderr"]),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _append_result(results_path, result)
    return result


def _skip_result(case: Case, reasons: list[str]) -> dict[str, Any]:
    """Build a skip record shaped like :func:`run_case`'s result, for a merged results.jsonl."""
    empty_summary = {"head": [], "tail": [], "errors": []}
    return {
        "id": case.case_id,
        "group": case.group,
        "description": case.description,
        "command": [_sanitize(part) for part in case.command],
        "environment_keys": sorted(case.environment),
        "exit_code": None,
        "duration_seconds": 0.0,
        "peak_memory_bytes": None,
        "vram_baseline_bytes": None,
        "peak_vram_bytes": None,
        "peak_vram_delta_bytes": None,
        "total_vram_bytes": None,
        "result": "skip",
        "reason": "environment unmet: " + "; ".join(reasons),
        "timed_out": False,
        "stdout": empty_summary,
        "stderr": empty_summary,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


@dataclass(frozen=True)
class RunSummary:
    """Machine-readable outcome of one :func:`run_selected` call."""

    total: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    results: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_seconds": round(self.duration_seconds, 3),
            "skipped_reasons": {
                result["id"]: result["reason"]
                for result in self.results
                if result["result"] == "skip"
            },
            "failed_ids": [result["id"] for result in self.results if result["result"] == "fail"],
        }


def run_selected(
    cases_path: Path = DEFAULT_CASES,
    results_path: Path = DEFAULT_RESULTS,
    *,
    groups: set[str] | None = None,
    rerun: set[str] | None = None,
    resume: bool = False,
    include_long: bool = False,
    include_destructive: bool = False,
    summary_path: Path | None = None,
    on_event: Callable[[str], None] | None = None,
    environment: dict[str, Any] | None = None,
) -> RunSummary:
    """Run the selected cases and return a typed summary.

    This is the Python entry point future callers (a GUI, another script) should use
    instead of invoking this file as a subprocess: it does the same group/long/destructive
    filtering, environment-requirement skipping, and results.jsonl bookkeeping the CLI does,
    and returns a JSON-serializable :class:`RunSummary` rather than only an exit code.

    ``environment``, if given, is used verbatim instead of calling :func:`fetch_environment`
    - this is the injection seam tests (and any caller that already has a snapshot) use to
    avoid a real ``devices``/``models`` subprocess call.
    """
    emit = on_event or (lambda _message: None)
    cases = select_cases(
        load_cases(cases_path, results_path=results_path),
        groups=groups,
        rerun=rerun,
        include_long=include_long,
        include_destructive=include_destructive,
    )
    completed = _completed_ids(results_path) if resume and not rerun else set()
    if environment is None and any(case.requires for case in cases):
        environment = fetch_environment(Path(_placeholders()["utteran"]))

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    for case in cases:
        if case.case_id in completed:
            emit(f"SKIP {case.case_id}: already recorded")
            continue
        unmet = unmet_requirements(case.requires, environment)
        if unmet:
            result = _skip_result(case, unmet)
            _append_result(results_path, result)
            emit(f"SKIP {case.case_id}: {result['reason']}")
        else:
            emit(f"RUN  {case.case_id}: {case.description}")
            result = run_case(case, results_path)
            emit(
                f"{result['result'].upper():4} {case.case_id}: {result['reason']} "
                f"({result['duration_seconds']}s)"
            )
        results.append(result)
    duration = time.monotonic() - started

    summary = RunSummary(
        total=len(results),
        passed=sum(1 for result in results if result["result"] == "pass"),
        failed=sum(1 for result in results if result["result"] == "fail"),
        skipped=sum(1 for result in results if result["result"] == "skip"),
        duration_seconds=duration,
        results=tuple(results),
    )
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument(
        "--summary", type=Path, default=None, help="write a machine-readable run summary JSON"
    )
    parser.add_argument("--group", action="append", help="run only this group (repeatable)")
    parser.add_argument("--resume", action="store_true", help="skip IDs already present")
    parser.add_argument(
        "--rerun", action="append", default=[], help="run only this ID, even if present"
    )
    parser.add_argument("--list", action="store_true", help="list selected cases without running")
    parser.add_argument(
        "--include-long", action="store_true", help="include endurance groups G13/P14"
    )
    parser.add_argument(
        "--include-destructive",
        action="store_true",
        help="also run environment-mutating cases (profile/native/model changes)",
    )
    args = parser.parse_args()

    all_cases = load_cases(args.cases)
    selected_groups = set(args.group or [])
    reruns = set(args.rerun)
    cases = select_cases(
        all_cases,
        groups=selected_groups,
        rerun=reruns,
        include_long=args.include_long,
        include_destructive=args.include_destructive,
    )
    if reruns:
        known = {case.case_id for case in all_cases}
        missing = reruns - known
        if missing:
            parser.error(f"unknown rerun IDs: {', '.join(sorted(missing))}")
    if args.list:
        for case in cases:
            marker = "!" if case.destructive else " "
            print(f"{marker}{case.case_id}\t{case.group}\t{case.description}")
        return 0

    summary = run_selected(
        args.cases,
        args.results,
        groups=selected_groups or None,
        rerun=reruns or None,
        resume=args.resume,
        include_long=args.include_long,
        include_destructive=args.include_destructive,
        summary_path=args.summary,
        on_event=print,
    )
    print(
        f"TOTAL {summary.total} pass={summary.passed} fail={summary.failed} "
        f"skip={summary.skipped} ({summary.duration_seconds:.1f}s)"
    )
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
