"""Memory budgets, peak models, calibration, and process-tree measurement.

Bundled models are deliberately conservative observations, not hardware guarantees.
The XPU/CPU/whisper.cpp values come from one Intel Core Ultra 7 255H / Arc 140T
machine and 25/50/100 minute concatenated audio measured during Phase 3d R-5.
CUDA has only one GTX 1070 Ti observation (7.31 GiB at 139 minutes), so its
bundled coefficient is zero rather than inventing a slope from one point.
"""

from __future__ import annotations

import ctypes
import json
import os
import statistics
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from utteran_paths import resolve_data_paths

GIB = 1024**3
CALIBRATION_SCHEMA_VERSION = 1
CALIBRATION_MIN_POINTS = 3
CALIBRATION_MIN_SPAN_MINUTES = 5.0
DEFAULT_SAFETY_MARGINS = {"cuda": 0.10, "xpu": 0.30, "cpu": 0.20, "other": 0.20}

MemoryStage = Literal["asr", "diarization"]
MemoryStatus = Literal["safe", "danger", "impossible", "unknown"]


@dataclass(frozen=True)
class PeakModel:
    """Linear peak working-set estimate in GiB."""

    base_gib: float
    gib_per_minute: float
    source: str
    sample_count: int

    def estimate_bytes(self, audio_minutes: float) -> int:
        return round((self.base_gib + self.gib_per_minute * max(audio_minutes, 0.0)) * GIB)


# OLS fits of the R-5 points. CUDA is a one-point conservative constant.
DEFAULT_MODELS: dict[tuple[MemoryStage, str, str], PeakModel] = {
    ("diarization", "pyannote", "xpu"): PeakModel(4.79693985, 0.00870553, "phase3d-r5", 3),
    ("diarization", "pyannote", "cpu"): PeakModel(2.42100143, 0.00571777, "phase3d-r5", 2),
    ("diarization", "pyannote", "cuda"): PeakModel(7.3100, 0.0, "phase3d-r5-one-point", 1),
    ("asr", "whisper-cpp", "vulkan"): PeakModel(1.11793709, 0.01002353, "phase3d-r5", 3),
}


@dataclass(frozen=True)
class MemoryReadings:
    """Raw available-memory readings; None means genuinely unknown."""

    system_available_bytes: int | None = None
    cuda_free_bytes: int | None = None
    xpu_limit_bytes: int | None = None


@dataclass(frozen=True)
class MemoryBudget:
    """Usable bytes after the device-specific safety margin."""

    device_kind: str
    raw_bytes: int | None
    usable_bytes: int | None
    safety_margin: float
    source: str


@dataclass(frozen=True)
class MemoryAssessment:
    """Three-state guard result, plus unknown when APIs/models are unavailable."""

    status: MemoryStatus
    estimate_bytes: int | None
    base_bytes: int | None
    budget: MemoryBudget
    model: PeakModel | None
    reason: str


@dataclass(frozen=True)
class MemoryDecision:
    """Preflight result including an optional auto-only CPU retreat."""

    requested_device: str
    selected_device: str
    effective_device: str
    assessment: MemoryAssessment | None
    fallback_assessment: MemoryAssessment | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class CalibrationPoint:
    """Content-free peak observation shared across profiles."""

    stage: MemoryStage
    backend: str
    device_kind: str
    audio_minutes: float
    peak_bytes: int
    measured_at: str


class ReadingProvider(Protocol):
    """Injectable OS/accelerator probe used by budget tests."""

    def __call__(self, device: str) -> MemoryReadings: ...


def device_kind(device: str) -> str:
    """Normalize concrete devices and whisper.cpp variants into model keys."""
    value = device.casefold()
    if value.startswith("cuda"):
        return "cuda"
    if value.startswith("xpu"):
        return "xpu"
    if value in {"vulkan", "openvino", "openvino_vulkan"}:
        return value
    if value == "cpu":
        return "cpu"
    return "other"


def calculate_budget(
    device: str,
    *,
    safety_margin: float = 0.0,
    readings: MemoryReadings | None = None,
    provider: ReadingProvider | None = None,
) -> MemoryBudget:
    """Calculate dedicated VRAM, shared-memory, or system-RAM budget."""
    kind = device_kind(device)
    margin = safety_margin or DEFAULT_SAFETY_MARGINS.get(kind, 0.20)
    observed = readings if readings is not None else (provider or read_memory)(device)
    raw: int | None
    source: str
    if kind == "cuda":
        raw, source = observed.cuda_free_bytes, "cuda-free-vram"
    elif kind == "xpu":
        values = [
            value
            for value in (observed.xpu_limit_bytes, observed.system_available_bytes)
            if value is not None
        ]
        raw = min(values) if len(values) == 2 else None
        source = "min(xpu-limit,system-available)"
    elif kind in {"cpu", "vulkan", "openvino", "openvino_vulkan", "other"}:
        raw, source = observed.system_available_bytes, "system-available"
    else:  # pragma: no cover - device_kind currently makes this unreachable
        raw, source = None, "unknown"
    usable = None if raw is None else max(0, round(raw * (1.0 - margin)))
    return MemoryBudget(kind, raw, usable, margin, source)


def assess_memory(
    model: PeakModel | None,
    audio_minutes: float,
    budget: MemoryBudget,
    *,
    danger_ratio: float = 0.90,
) -> MemoryAssessment:
    """Classify safe, danger, impossible, or unknown without optimistic defaults."""
    if model is None:
        return MemoryAssessment("unknown", None, None, budget, None, "推定式がありません")
    estimate = model.estimate_bytes(audio_minutes)
    base = round(model.base_gib * GIB)
    if budget.usable_bytes is None:
        return MemoryAssessment(
            "unknown", estimate, base, budget, model, "利用可能メモリを取得できません"
        )
    if base > budget.usable_bytes:
        return MemoryAssessment(
            "impossible", estimate, base, budget, model, "基礎量がメモリ予算を超えます"
        )
    if estimate >= round(budget.usable_bytes * danger_ratio):
        return MemoryAssessment(
            "danger", estimate, base, budget, model, "推定ピークがメモリ予算に近いか超えます"
        )
    return MemoryAssessment("safe", estimate, base, budget, model, "推定ピークは予算内です")


def plan_diarization_memory(
    *,
    guard: str,
    requested_device: str,
    selected_device: str,
    backend: str,
    audio_minutes: float,
    safety_margin: float,
    store: CalibrationStore,
    provider: ReadingProvider | None = None,
) -> MemoryDecision:
    """Assess the chosen device and retreat to a demonstrably safe CPU only for auto."""
    if guard == "off":
        return MemoryDecision(requested_device, selected_device, selected_device, None)
    kind = device_kind(selected_device)
    model = store.model("diarization", backend, kind)
    budget = calculate_budget(selected_device, safety_margin=safety_margin, provider=provider)
    assessment = assess_memory(model, audio_minutes, budget)
    may_retreat = (
        guard == "auto"
        and requested_device == "auto"
        and kind in {"cuda", "xpu"}
        and assessment.status in {"danger", "impossible"}
    )
    if may_retreat:
        cpu_model = store.model("diarization", backend, "cpu")
        cpu_budget = calculate_budget("cpu", safety_margin=safety_margin, provider=provider)
        cpu_assessment = assess_memory(cpu_model, audio_minutes, cpu_budget)
        if cpu_assessment.status == "safe":
            return MemoryDecision(
                requested_device,
                selected_device,
                "cpu",
                assessment,
                cpu_assessment,
                f"{selected_device} は {assessment.status}、CPU は safe と推定",
            )
        return MemoryDecision(
            requested_device,
            selected_device,
            selected_device,
            assessment,
            cpu_assessment,
        )
    return MemoryDecision(requested_device, selected_device, selected_device, assessment)


def default_calibration_path() -> Path:
    """Return profile-independent, platformdirs-managed calibration storage."""
    return resolve_data_paths().memory_calibration


class CalibrationStore:
    """Persist content-free observations and produce robust local fits."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_calibration_path()

    def load(self) -> list[CalibrationPoint]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != CALIBRATION_SCHEMA_VERSION:
                return []
            return [CalibrationPoint(**item) for item in payload.get("points", [])]
        except (OSError, ValueError, TypeError):
            return []

    def add(self, point: CalibrationPoint) -> None:
        points = self.load()
        points.append(point)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "points": [asdict(p) for p in points],
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)

    def record(
        self,
        stage: MemoryStage,
        backend: str,
        device: str,
        audio_minutes: float,
        peak_bytes: int,
        *,
        measured_at: str | None = None,
    ) -> CalibrationPoint:
        point = CalibrationPoint(
            stage=stage,
            backend=backend,
            device_kind=device_kind(device),
            audio_minutes=audio_minutes,
            peak_bytes=peak_bytes,
            measured_at=measured_at or datetime.now().astimezone().isoformat(),
        )
        self.add(point)
        return point

    def reset(self) -> bool:
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    def model(self, stage: MemoryStage, backend: str, kind: str) -> PeakModel | None:
        matching = [
            point
            for point in self.load()
            if (point.stage, point.backend, point.device_kind) == (stage, backend, kind)
        ]
        calibrated = fit_calibration(matching)
        return calibrated or DEFAULT_MODELS.get((stage, backend, kind))


def fit_calibration(points: list[CalibrationPoint]) -> PeakModel | None:
    """Fit after MAD residual rejection; three points permit one residual check."""
    valid = [p for p in points if p.audio_minutes > 0 and p.peak_bytes > 0]
    if (
        len(valid) < CALIBRATION_MIN_POINTS
        or max((p.audio_minutes for p in valid), default=0.0)
        - min((p.audio_minutes for p in valid), default=0.0)
        < CALIBRATION_MIN_SPAN_MINUTES
    ):
        return None
    slopes = [
        ((b.peak_bytes - a.peak_bytes) / GIB) / (b.audio_minutes - a.audio_minutes)
        for index, a in enumerate(valid)
        for b in valid[index + 1 :]
        if abs(a.audio_minutes - b.audio_minutes) >= CALIBRATION_MIN_SPAN_MINUTES
    ]
    if not slopes:
        return None
    robust_slope = statistics.median(slopes)
    robust_base = statistics.median(
        p.peak_bytes / GIB - robust_slope * p.audio_minutes for p in valid
    )
    residuals = [
        abs(p.peak_bytes / GIB - (robust_base + robust_slope * p.audio_minutes)) for p in valid
    ]
    median_residual = statistics.median(residuals)
    mad = statistics.median(abs(value - median_residual) for value in residuals)
    median_peak = statistics.median(p.peak_bytes / GIB for p in valid)
    threshold = max(3.0 * mad, 0.10 * median_peak, 0.05)
    inliers = [p for p, residual in zip(valid, residuals, strict=True) if residual <= threshold]
    if (
        len(inliers) < CALIBRATION_MIN_POINTS
        or max(p.audio_minutes for p in inliers) - min(p.audio_minutes for p in inliers)
        < CALIBRATION_MIN_SPAN_MINUTES
    ):
        return None
    xs = [p.audio_minutes for p in inliers]
    ys = [p.peak_bytes / GIB for p in inliers]
    mean_x, mean_y = statistics.mean(xs), statistics.mean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator
    base = mean_y - slope * mean_x
    return PeakModel(max(0.0, base), max(0.0, slope), "local-calibration", len(inliers))


def read_memory(device: str) -> MemoryReadings:
    """Best-effort readings. Unsupported APIs remain None, never 'enough'."""
    kind = device_kind(device)
    system_available = _system_available_memory()
    cuda_free: int | None = None
    xpu_limit: int | None = None
    if kind in {"cuda", "xpu"}:
        try:
            import torch

            index = _device_index(device)
            if kind == "cuda":
                free, _total = torch.cuda.mem_get_info(index)
                cuda_free = int(free)
            else:
                properties = torch.xpu.get_device_properties(index)
                value = getattr(properties, "total_memory", None)
                xpu_limit = int(value) if value is not None else None
        except Exception:
            pass
    override = os.environ.get("UTTERAN_DEBUG_MEMORY_BUDGET_GIB")
    if override:
        try:
            forced = max(0, round(float(override) * GIB))
        except ValueError:
            pass
        else:
            if kind == "cuda":
                cuda_free = forced
            elif kind == "xpu":
                xpu_limit = forced
                system_available = forced
            else:
                system_available = forced
    return MemoryReadings(system_available, cuda_free, xpu_limit)


def _device_index(device: str) -> int:
    return int(device.partition(":")[2] or "0")


def _system_available_memory() -> int | None:
    if os.name == "nt":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        try:
            loader = vars(ctypes)["windll"]
            return (
                int(status.ullAvailPhys)
                if loader.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
                else None
            )
        except (AttributeError, OSError):
            return None
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


class PeakMonitor:
    """Sample current process-tree working set in a lightweight thread."""

    def __init__(self, reader: Callable[[int], int | None] | None = None) -> None:
        self._reader = reader or read_process_tree_memory
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_bytes: int | None = None

    def start(self) -> None:
        self._sample()
        self._thread = threading.Thread(target=self._run, name="utteran-memory", daemon=True)
        self._thread.start()

    def stop(self) -> int | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._sample()
        return self.peak_bytes

    def _run(self) -> None:
        while not self._stop.wait(0.1):
            self._sample()

    def _sample(self) -> None:
        value = self._reader(os.getpid())
        if value is not None:
            self.peak_bytes = value if self.peak_bytes is None else max(self.peak_bytes, value)


@contextmanager
def measure_peak(reader: Callable[[int], int | None] | None = None) -> Iterator[PeakMonitor]:
    monitor = PeakMonitor(reader)
    monitor.start()
    try:
        yield monitor
    finally:
        monitor.stop()


def read_process_tree_memory(pid: int) -> int | None:
    """Reuse the Phase 3d harness definition: summed current working sets."""
    return _read_windows_tree_memory(pid) if os.name == "nt" else _read_posix_tree_memory(pid)


def _descendant_ids(root_pid: int, parent_by_pid: dict[int, int]) -> set[int]:
    selected = {root_pid}
    while True:
        added = {child for child, parent in parent_by_pid.items() if parent in selected} - selected
        if not added:
            return selected
        selected.update(added)


def _read_posix_tree_memory(pid: int) -> int | None:
    parent_by_pid: dict[int, int] = {}
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            parts = stat_path.read_text(encoding="utf-8").split()
            parent_by_pid[int(parts[0])] = int(parts[3])
        except (OSError, ValueError, IndexError):
            continue
    total, observed = 0, False
    for process_id in _descendant_ids(pid, parent_by_pid):
        try:
            for line in Path(f"/proc/{process_id}/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    total += int(line.split()[1]) * 1024
                    observed = True
                    break
        except (OSError, ValueError, IndexError):
            continue
    return total if observed else None


def _read_windows_tree_memory(pid: int) -> int | None:
    try:
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        class Entry(ctypes.Structure):
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

        loader = vars(ctypes)["WinDLL"]
        kernel32, psapi = loader("kernel32"), loader("psapi")
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return None
        parents: dict[int, int] = {}
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(entry)
            success = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while success:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                success = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        total, observed = 0, False
        for process_id in _descendant_ids(pid, parents):
            handle = kernel32.OpenProcess(0x1000 | 0x0010, False, process_id)
            if not handle:
                continue
            try:
                counters = Counters()
                counters.cb = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    total += int(counters.WorkingSetSize)
                    observed = True
            finally:
                kernel32.CloseHandle(handle)
        return total if observed else None
    except (AttributeError, OSError, ValueError):
        return None
