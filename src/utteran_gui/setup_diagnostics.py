"""F-B: read-only setup diagnostics for the next UI (``--next-ui-v1`` only).

Stages, each with its own state (none of them is readiness):

1. **OS** (no profile needed): display adapters and driver version from the OS
   (``hardware.detect_gpu``), memory, free disk, and the Visual C++ 2015-2022 x64
   runtime (registry *and* DLL presence; neither proves the DLLs load).
2. **runtime** (only when a profile exists): ``utteran devices --json`` in that profile,
   which separates CTranslate2 CUDA (ASR) from PyTorch CUDA (diarization).

Detection runs in one background thread (single-flight): concurrent refresh requests
join the running pass instead of starting duplicate probes, a result from an older
pass never replaces a newer one, a timeout or failure is reported as ``unknown`` (never
"no GPU" or "CPU") and nothing incomplete is cached. One bounded retry of a timed-out
runtime probe is allowed (warm OS cache; the total time stays bounded) - see #89.
No driver, runtime or package is installed and nothing is written.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from utteran_gui import hardware
from utteran_gui.cli import CliAdapter, CliError, CliTimeout, as_json_dict

VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
NVIDIA_DRIVER_URL = "https://www.nvidia.com/Download/index.aspx"
VCREDIST_KEY = r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
VCREDIST_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
# One `devices --json` run: a 1-GPU pass is <= ~200 s at worst (see utteran.devices); 300 s
# leaves room for a few more GPUs on this new path (the legacy /api/environment keeps 200 s).
RUNTIME_TIMEOUT = 300.0
# Division of labour (PR #91 P3): utteran.devices retries a single slow probe inside the run;
# this stage reruns the whole CLI only when the *process* timed out (cold interpreter/DLLs),
# never because the payload reports a probe timeout (that was already retried inside).
RUNTIME_RETRIES = 1
RUNTIME_BUDGET = 600.0  # total wall-clock bound, retries included

Phase = Literal["idle", "running", "completed", "failed"]
Tri = bool | None


@dataclass(frozen=True)
class VcRedist:
    state: Literal["present", "missing", "partial", "unknown", "not_applicable"]
    version: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {"state": self.state, "version": self.version, "download_url": VCREDIST_URL}


def detect_vcredist(
    *,
    system: str | None = None,
    read_registry: Callable[[], tuple[int | None, str | None]] | None = None,
    system32: Path | None = None,
) -> VcRedist:
    """Visual C++ v14 x64 runtime: registry ``Installed``/``Version`` plus the three DLLs.

    ``present`` needs both; ``missing`` needs neither; anything else is ``partial`` or
    ``unknown``. The registry alone does not prove the DLLs are intact or loadable.
    """
    if (system or platform.system()) != "Windows":
        return VcRedist("not_applicable")
    try:
        installed, version = (read_registry or _read_vcredist_registry)()
    except OSError:
        installed, version = None, None
    folder = system32 or Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    try:
        dlls = [(folder / name).is_file() for name in VCREDIST_DLLS]
    except OSError:
        return VcRedist("unknown", version)
    registry = installed == 1
    if registry and all(dlls):
        return VcRedist("present", version)
    if not registry and not any(dlls):
        return VcRedist("missing")
    return VcRedist("partial", version)


def _read_vcredist_registry() -> tuple[int | None, str | None]:  # pragma: no cover - Windows
    if sys.platform != "win32":
        return None, None
    import winreg

    access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, VCREDIST_KEY, 0, access) as key:
            installed, _ = winreg.QueryValueEx(key, "Installed")
            try:
                version, _ = winreg.QueryValueEx(key, "Version")
            except OSError:
                version = None
    except FileNotFoundError:
        return None, None
    text = str(version)[:40] if isinstance(version, str) else None
    return (int(installed) if isinstance(installed, int) else None), text


@dataclass(frozen=True)
class RuntimeReport:
    profile: str | None
    reason_code: str | None
    ctranslate2_cuda: Tri = None
    torch_cuda: Tri = None
    cuda_device_count: int | None = None
    attempts: int = 0
    probes: tuple[tuple[str, str], ...] = ()  # (fixed probe name, completed/timeout/error)
    # The first attempt of this pass (kept when a retry follows): fixed status + seconds.
    first_attempt: tuple[str, float] | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "first_attempt": None
            if self.first_attempt is None
            else {"status": self.first_attempt[0], "seconds": round(self.first_attempt[1], 1)},
            "profile": self.profile,
            "reason_code": self.reason_code,
            "ctranslate2_cuda": self.ctranslate2_cuda,
            "torch_cuda": self.torch_cuda,
            "cuda_device_count": self.cuda_device_count,
            "attempts": self.attempts,
            "probes": [{"name": n, "status": s} for n, s in self.probes],
        }


_PROBE_NAMES = frozenset(
    {
        "ctranslate2_cpu",
        "ctranslate2_cuda_count",
        "ctranslate2_cuda",
        "nvidia_metadata",
        "torch_cuda",
        "torch_xpu",
        "openvino",
        "onnxruntime",
        "vulkan",
    }
)


_CUDA_PROBES = frozenset({"ctranslate2_cuda_count", "ctranslate2_cuda", "torch_cuda"})


def parse_devices(profile: str, payload: dict[str, Any], attempts: int) -> RuntimeReport:
    """Allowlisted projection of ``utteran devices --json`` (no paths, no free text)."""
    ct2 = payload.get("ctranslate2") if isinstance(payload.get("ctranslate2"), dict) else {}
    torch = payload.get("pytorch") if isinstance(payload.get("pytorch"), dict) else {}
    assert isinstance(ct2, dict) and isinstance(torch, dict)
    devices = [d for d in ct2.get("cuda_devices", []) if isinstance(d, dict)]
    ct2_cuda: Tri = (
        any(d.get("usable") is True for d in devices)
        if ct2.get("cuda_status") == "completed"
        else None
    )
    torch_cuda: Tri = (
        bool(torch.get("cuda_available")) if torch.get("cuda_status") == "completed" else None
    )
    count = ct2.get("cuda_device_count")
    probes = tuple(
        (str(item["name"]), str(item["status"]))
        for item in payload.get("probes", [])
        if isinstance(item, dict)
        and item.get("name") in _PROBE_NAMES
        and item.get("status") in {"completed", "timeout", "error"}
    )
    # Only the CUDA probes decide a retry: a slow Vulkan/OpenVINO probe is not a CUDA answer.
    timed_out = any(status == "timeout" for name, status in probes if name in _CUDA_PROBES)
    return RuntimeReport(
        profile,
        "probe_timeout" if timed_out else None,
        ct2_cuda,
        torch_cuda,
        count if isinstance(count, int) and ct2.get("cuda_status") == "completed" else None,
        attempts,
        probes,
    )


def recommend(gpu: hardware.GpuReport, runtime: RuntimeReport | None) -> dict[str, Any]:
    """Fixed reason codes for the standard setup. Only cpu/cuda can be verified (B4)."""
    reasons: list[str] = []
    if runtime is not None and runtime.ctranslate2_cuda is True:
        reasons.append("runtime_cuda_confirmed")
        if runtime.torch_cuda is not True:
            reasons.append("diarization_cuda_unconfirmed")
        return {"profile": "cuda", "reason_codes": reasons, "confident": True}
    nvidia = [a for a in gpu.adapters if a.vendor == "nvidia"]
    # (runtime comes from the cuda profile only; None/unknown keeps the NVIDIA recommendation)
    if gpu.error is not None:
        return {"profile": "cpu", "reason_codes": ["gpu_detection_unknown"], "confident": False}
    if nvidia:
        reasons.append("nvidia_adapter_detected")
        if not any(a.driver_version for a in nvidia):
            reasons.append("nvidia_driver_unknown")
        if runtime is not None and runtime.ctranslate2_cuda is False:
            reasons.append("runtime_cuda_unavailable")
            return {"profile": "cpu", "reason_codes": reasons, "confident": True}
        reasons.append("runtime_not_confirmed")
        return {"profile": "cuda", "reason_codes": reasons, "confident": False}
    if any(a.vendor in {"intel", "amd", "other"} for a in gpu.adapters):
        return {"profile": "cpu", "reason_codes": ["gpu_not_supported_by_setup"], "confident": True}
    return {"profile": "cpu", "reason_codes": ["no_gpu_detected"], "confident": True}


@dataclass
class _State:
    generation: int = 0
    phase: Phase = "idle"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    gpu: hardware.GpuReport | None = None
    memory: hardware.MemoryReport | None = None
    disk: hardware.DiskReport | None = None
    vcredist: VcRedist | None = None
    runtime: RuntimeReport | None = None
    runtime_phase: Phase = "idle"
    # A forced re-detection asked for while a pass was running: run right after it.
    pending_force: bool = False
    thread: threading.Thread | None = field(default=None, repr=False)


@dataclass(frozen=True)
class DiagnosticProbes:
    """Injectable probes (tests use synthetic ones; production uses the real OS/CLI)."""

    gpu: Callable[[], hardware.GpuReport]
    memory: Callable[[], hardware.MemoryReport]
    disk: Callable[[Path], hardware.DiskReport]
    vcredist: Callable[[], VcRedist]
    runtime: Callable[[str, bool], dict[str, Any]]  # (profile, refresh) -> devices JSON


class SetupDiagnostics:
    def __init__(
        self,
        cli: CliAdapter | Any,
        *,
        probes: DiagnosticProbes | None = None,
        busy_profile: Callable[[], str | None] = lambda: None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        runtime_budget: float = RUNTIME_BUDGET,
        venv_build_supported: bool | None = None,
        gate: Any = None,
    ) -> None:
        self.cli = cli
        # Shared with the dispatcher (execution_gate.ExecutionGate): set by the setup routes.
        self.gate = gate
        # Same rule as the planner (setup.ps1 is Windows-only); injectable for synthetic worlds.
        self.venv_build_supported = (
            os.name == "nt" if venv_build_supported is None else venv_build_supported
        )
        self.probes = probes or DiagnosticProbes(
            gpu=hardware.detect_gpu,
            memory=hardware.detect_memory,
            disk=hardware.detect_disk,
            vcredist=detect_vcredist,
            runtime=self._devices,
        )
        self.busy_profile = busy_profile
        self.clock = clock
        self.runtime_budget = runtime_budget
        self._lock = threading.Lock()
        self._state = _State()

    def _devices(self, profile: str, refresh: bool) -> dict[str, Any]:
        arguments = ["devices", "--json", *(["--refresh"] if refresh else [])]
        return as_json_dict(self.cli.run_json(profile, arguments, timeout=RUNTIME_TIMEOUT))

    # ------------------------------------------------------------------ read

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = replace(self._state)
        gpu = state.gpu
        return {
            "schema_version": 1,
            "generation": state.generation,
            "phase": state.phase,
            "started_at": _iso(state.started_at),
            "finished_at": _iso(state.finished_at),
            "os": {
                "platform": {"Windows": "windows", "Linux": "linux"}.get(
                    platform.system(), "other"
                ),
                "venv_build_supported": self.venv_build_supported,
            },
            "gpu": None
            if gpu is None
            else {
                "state": "unknown" if gpu.error else "detected" if gpu.adapters else "none",
                "reason_code": None if gpu.error is None else _gpu_reason(gpu.error),
                "adapters": [
                    {"name": a.name[:120], "vendor": a.vendor, "driver_version": a.driver_version}
                    for a in gpu.adapters[:8]
                ],
                "driver_download_url": NVIDIA_DRIVER_URL,
            },
            "memory": None if state.memory is None else {"total_bytes": state.memory.total_bytes},
            "disk": None if state.disk is None else {"free_bytes": state.disk.free_bytes},
            "vcredist": None if state.vcredist is None else state.vcredist.to_wire(),
            "runtime_phase": state.runtime_phase,
            "rerun_pending": state.pending_force,
            "runtime": None if state.runtime is None else state.runtime.to_wire(),
            "recommendation": None if gpu is None else recommend(gpu, state.runtime),
        }

    # ------------------------------------------------------------------ refresh

    def refresh(self, *, runtime: bool, force_runtime: bool = False) -> dict[str, Any]:
        """Start one detection pass unless one is running (single-flight). Never blocks.

        A forced runtime request that arrives during a pass is not dropped: it is recorded and
        runs once after the current pass (several such requests coalesce into one).
        """
        with self._lock:
            if self._state.phase == "running":
                if runtime and force_runtime:
                    self._state.pending_force = True
            else:
                self._start_locked(runtime, force_runtime)
        return self.snapshot()

    def _start_locked(self, runtime: bool, force_runtime: bool) -> None:
        """Caller holds ``self._lock``."""
        self._state.generation += 1
        self._state.phase = "running"
        self._state.started_at = self.clock()
        self._state.finished_at = None
        if runtime:
            # A new runtime pass never shows (or recommends from) the previous pass's answer.
            self._state.runtime = None
            self._state.runtime_phase = "idle"
        thread = threading.Thread(
            target=self._run,
            args=(self._state.generation, runtime, force_runtime),
            daemon=True,
            name="utteran-setup-diagnostics",
        )
        self._state.thread = thread
        thread.start()

    def wait(self, timeout: float) -> bool:
        """Test helper: join the running pass and a queued forced pass after it."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                thread = self._state.thread
            if thread is None:
                return True
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                return False
            with self._lock:
                if self._state.thread is thread and self._state.phase != "running":
                    return True

    def _apply(self, generation: int, **values: Any) -> bool:
        with self._lock:
            if generation != self._state.generation:
                return False  # an older pass never overwrites a newer one
            for name, value in values.items():
                setattr(self._state, name, value)
            return True

    def _run(self, generation: int, runtime: bool, force: bool) -> None:
        try:
            gpu = _safe(self.probes.gpu, hardware.GpuReport((), "none", error="probe_failed"))
            memory = _safe(self.probes.memory, hardware.MemoryReport(None, None))
            disk = _safe(
                lambda: self.probes.disk(Path(self.cli.repo_root)), hardware.DiskReport(None)
            )
            vc = _safe(self.probes.vcredist, VcRedist("unknown"))
            self._apply(generation, gpu=gpu, memory=memory, disk=disk, vcredist=vc)
            report = self._runtime_stage(generation, force) if runtime else None
            self._apply(
                generation,
                phase="completed",
                finished_at=self.clock(),
                **({"runtime": report, "runtime_phase": "completed"} if runtime else {}),
            )
        except Exception:
            self._apply(generation, phase="failed", finished_at=self.clock())
        with self._lock:
            if generation == self._state.generation and self._state.pending_force:
                self._state.pending_force = False
                self._start_locked(True, True)

    def runtime_busy(self) -> bool:
        """A runtime probe (profile ``devices`` children) is running or about to run."""
        with self._lock:
            return self._state.phase == "running" or self._state.pending_force

    def _runtime_profile(self) -> tuple[str | None, str | None]:
        """Only the ``cuda`` profile answers "can CUDA run": a CPU venv has no CUDA runtime,
        so its CTranslate2 saying "no CUDA" is not evidence about the GPU (PR #91 P2-4)."""
        try:
            info = self.cli.profile_info("cuda")
        except CliError:
            return None, "cuda_profile_missing"
        if not info.exists:
            return None, "cuda_profile_missing"
        if getattr(info, "compatible", True) is False:
            return None, "cuda_profile_incompatible"
        return "cuda", None

    def _runtime_stage(self, generation: int, force: bool) -> RuntimeReport:
        profile, missing = self._runtime_profile()
        if profile is None:
            return RuntimeReport(None, missing)
        if self.busy_profile() is not None:
            return RuntimeReport(profile, "setup_running")
        gate = self.gate
        if gate is not None and not gate.try_acquire("probe"):
            # A job or setup holds the heavy slot (and maybe the GPU / this venv): not now.
            return RuntimeReport(profile, "execution_running")
        try:
            return self._probe(generation, profile, force)
        finally:
            if gate is not None:
                gate.release("probe")

    def _probe(self, generation: int, profile: str, force: bool) -> RuntimeReport:
        self._apply(generation, runtime_phase="running")
        deadline = time.monotonic() + self.runtime_budget
        report = RuntimeReport(profile, "probe_failed")
        first: tuple[str, float] | None = None
        for attempt in range(1, RUNTIME_RETRIES + 2):
            started = time.monotonic()
            try:
                payload = self.probes.runtime(profile, force and attempt == 1)
                report = parse_devices(profile, payload, attempt)
            except (subprocess.TimeoutExpired, CliTimeout):
                # The adapter tree-kills the CLI and raises CliTimeout (a CliError): catch it
                # before the generic failure so the cold-start retry really happens (#89).
                report = RuntimeReport(profile, "probe_timeout", attempts=attempt)
            except (CliError, OSError, subprocess.SubprocessError, ValueError):
                report = RuntimeReport(profile, "probe_failed", attempts=attempt)
            if first is None:
                first = (report.reason_code or "completed", time.monotonic() - started)
            report = replace(report, first_attempt=first)
            process_timeout = report.reason_code == "probe_timeout" and report.probes == ()
            if not process_timeout or time.monotonic() + RUNTIME_TIMEOUT > deadline:
                return report
        return report


def _safe(call: Callable[[], Any], fallback: Any) -> Any:
    try:
        return call()
    except Exception:
        return fallback


def _gpu_reason(error: str) -> str:
    return "unsupported_os" if error == "unsupported_os" else "gpu_probe_failed"


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
