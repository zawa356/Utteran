"""Pre-venv hardware detection and setup-wizard profile recommendation.

This module must never import `utteran`, `torch`, `openvino`, `ctranslate2`,
`faster_whisper`, or `pyannote` - the GUI environment (`.venvs/win-gui`) only
has FastAPI/Uvicorn/pywebview installed, and Phase 5c explicitly forbids
adding inference dependencies to it just to detect hardware before any
profile venv exists (see docs/utteran_Phase5c_指示書.md Step 2). Detection
therefore only uses the Python standard library, `ctypes` (Windows memory
API), and shelling out to OS-provided executables (`powershell.exe`) - the
same style `utteran/devices.py` already uses for `nvidia-smi`/`vulkaninfo`
probes, just without any Python package that would pull inference wheels in.
"""

from __future__ import annotations

import ctypes
import json
import platform
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from utteran_gui.processes import build_creation_kwargs
from utteran_gui.settings import PROFILE_NAMES

GpuVendor = Literal["nvidia", "intel", "amd", "other", "none"]

# cpu/intel/vulkan are measured from this machine's actual `.venvs` via
# `setup.ps1 -List` (2026-08-17: cpu 1.0 GiB, intel 5.3 GiB, vulkan 1.1 GiB -
# the intel figure matches docs/utteran_Phase5c_指示書.md's own "Intel プロファ
# イルは5.3GB"). No NVIDIA GPU is available on this machine, so `cuda` keeps
# the 指示書's stated "torch だけで約2.4GB" figure unmeasured here.
_APPROX_DISK_BYTES: dict[str, int] = {
    "cuda": int(2.4 * 1024**3),
    "intel": int(5.3 * 1024**3),
    "cpu": int(1.0 * 1024**3),
    "vulkan": int(1.1 * 1024**3),
}


@dataclass(frozen=True)
class GpuAdapter:
    """One display adapter as reported by the OS, with a coarse vendor guess."""

    name: str
    vendor: GpuVendor


@dataclass(frozen=True)
class GpuReport:
    """Detected display adapters and the vendor that drives the recommendation."""

    adapters: tuple[GpuAdapter, ...]
    dominant_vendor: GpuVendor
    error: str | None = None


@dataclass(frozen=True)
class MemoryReport:
    """Physical RAM, when the OS API to read it is available."""

    total_bytes: int | None
    available_bytes: int | None


@dataclass(frozen=True)
class DiskReport:
    """Free space on the volume that will host `.venvs` and downloaded models."""

    free_bytes: int | None


@dataclass(frozen=True)
class AlternativeProfile:
    """One selectable profile, described by what it does for *this* user."""

    profile: str
    asr_accelerated: bool
    diarization_accelerated: bool
    approx_disk_bytes: int
    extra_setup: tuple[str, ...] = ()
    caveat: str | None = None


@dataclass(frozen=True)
class ProfileRecommendation:
    """The wizard's suggested profile plus every profile safe to offer."""

    recommended: str
    reasons: tuple[str, ...]
    alternatives: tuple[AlternativeProfile, ...]
    detection_confident: bool


@dataclass(frozen=True)
class HardwareSnapshot:
    """Complete pre-venv detection result returned by `GET /api/wizard/hardware`."""

    os_supported: bool
    gpu: GpuReport
    memory: MemoryReport
    disk: DiskReport
    recommendation: ProfileRecommendation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HardwareProbeSet:
    """Injectable environment probes, mirroring utteran.devices.DeviceProbeSet."""

    gpu: Callable[[], GpuReport]
    memory: Callable[[], MemoryReport]
    disk: Callable[[Path], DiskReport]


def system_probes() -> HardwareProbeSet:
    """Create the real, OS-backed probe collection."""
    return HardwareProbeSet(gpu=detect_gpu, memory=detect_memory, disk=detect_disk)


def detect_hardware(repo_root: Path, *, probes: HardwareProbeSet | None = None) -> HardwareSnapshot:
    """Run independent probes and derive a profile recommendation from them."""
    selected = probes or system_probes()
    gpu = selected.gpu()
    memory = selected.memory()
    disk = selected.disk(repo_root)
    return HardwareSnapshot(
        os_supported=platform.system() == "Windows",
        gpu=gpu,
        memory=memory,
        disk=disk,
        recommendation=recommend_profile(gpu),
    )


def recommend_profile(gpu: GpuReport) -> ProfileRecommendation:
    """Map a detected GPU vendor to the profile table in the Phase 5c 指示書.

    Only profiles that can actually run on the detected hardware are
    returned in `alternatives` - the wizard must never offer a selection
    that is known to fail (e.g. `cuda` without an NVIDIA GPU). When
    detection itself is not confident (non-Windows, or the probe failed),
    only the universally-safe `cpu` profile is offered; hardware-specific
    profiles must be chosen manually outside the wizard in that case.
    """
    confident = gpu.error is None
    if not confident:
        return ProfileRecommendation(
            recommended="cpu",
            reasons=(
                "この環境のGPUを自動検出できませんでした。",
                "CPUプロファイルはどの環境でも動作します。",
            ),
            alternatives=(_alternative("cpu"),),
            detection_confident=False,
        )
    if gpu.dominant_vendor == "nvidia":
        return ProfileRecommendation(
            recommended="cuda",
            reasons=(
                "NVIDIA GPUを検出しました。",
                "文字起こしと話者分離の両方をGPUで実行できます。",
            ),
            alternatives=(_alternative("cuda"), _alternative("cpu")),
            detection_confident=True,
        )
    if gpu.dominant_vendor == "intel":
        return ProfileRecommendation(
            recommended="intel",
            reasons=(
                "Intel GPU(Arcまたは内蔵GPU)を検出しました。",
                "文字起こしはOpenVINO/Vulkanで、話者分離はIntel GPU(XPU)で実行できます。",
            ),
            alternatives=(_alternative("intel"), _alternative("cpu")),
            detection_confident=True,
        )
    if gpu.dominant_vendor == "amd" or gpu.dominant_vendor == "other":
        return ProfileRecommendation(
            recommended="vulkan",
            reasons=(
                "NVIDIA/Intel以外のGPUを検出しました。",
                "文字起こしはVulkanでGPU実行できますが、話者分離はCPUで実行されます。",
            ),
            alternatives=(_alternative("vulkan"), _alternative("cpu")),
            detection_confident=True,
        )
    return ProfileRecommendation(
        recommended="cpu",
        reasons=(
            "GPUを検出できませんでした。",
            "CPUで動作します。GPUがある環境より処理時間が長くなります。",
        ),
        alternatives=(_alternative("cpu"),),
        detection_confident=True,
    )


def _alternative(profile: str) -> AlternativeProfile:
    if profile not in PROFILE_NAMES:
        raise ValueError(f"Unknown profile: {profile}")
    if profile == "cuda":
        return AlternativeProfile(
            profile="cuda",
            asr_accelerated=True,
            diarization_accelerated=True,
            approx_disk_bytes=_APPROX_DISK_BYTES["cuda"],
        )
    if profile == "intel":
        return AlternativeProfile(
            profile="intel",
            asr_accelerated=True,
            diarization_accelerated=True,
            approx_disk_bytes=_APPROX_DISK_BYTES["intel"],
            extra_setup=(
                "whisper.cppのOpenVINO/Vulkanバックエンドを使う場合、"
                "`utteran native build`と`utteran models prepare-openvino`が"
                "追加で必要です(任意)。",
            ),
        )
    if profile == "vulkan":
        return AlternativeProfile(
            profile="vulkan",
            asr_accelerated=True,
            diarization_accelerated=False,
            approx_disk_bytes=_APPROX_DISK_BYTES["vulkan"],
            extra_setup=("Vulkan SDK (https://vulkan.lunarg.com/) の導入が必要な場合があります。",),
            caveat="話者分離はCPUで実行されます。",
        )
    return AlternativeProfile(
        profile="cpu",
        asr_accelerated=False,
        diarization_accelerated=False,
        approx_disk_bytes=_APPROX_DISK_BYTES["cpu"],
        caveat="GPUを使わないため、処理時間が長くなります。",
    )


def detect_gpu() -> GpuReport:
    """Detect display adapters via the OS's own CIM/WMI query (Windows only).

    Deliberately shells out to `powershell.exe` rather than importing a WMI
    Python package: this keeps the GUI environment free of any new
    dependency, matching how `utteran/devices.py` shells out to
    `nvidia-smi`/`vulkaninfo` instead of importing vendor SDKs.
    """
    if platform.system() != "Windows":
        return GpuReport((), "none", error="unsupported_os")
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,AdapterCompatibility | ConvertTo-Json -Compress",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            **build_creation_kwargs(),
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return GpuReport((), "none", error=_bounded_error(completed.stderr or "empty output"))
        payload = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return GpuReport((), "none", error=_bounded_error(str(exc)))
    rows = payload if isinstance(payload, list) else [payload]
    fields = [_adapter_fields(row) for row in rows]
    adapters = tuple(
        GpuAdapter(name=name, vendor=_classify_vendor(name, compatibility))
        for name, compatibility in fields
        if name
    )
    return GpuReport(adapters, _dominant_vendor(adapters))


def detect_memory() -> MemoryReport:
    """Report total/available physical RAM via GlobalMemoryStatusEx (Windows only)."""
    if platform.system() != "Windows":
        return MemoryReport(None, None)
    try:

        class _MemoryStatusEx(ctypes.Structure):
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

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        # `ctypes.WinDLL` only exists in typeshed under a sys.platform=="win32"
        # guard, which would fail mypy on the Linux CI job; go through getattr,
        # matching the same workaround already used in utteran/devices.py.
        loader_name = "WinDLL"
        kernel32 = getattr(ctypes, loader_name)("kernel32", use_last_error=True)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return MemoryReport(None, None)
        return MemoryReport(int(status.ullTotalPhys), int(status.ullAvailPhys))
    except (AttributeError, OSError):
        return MemoryReport(None, None)


def detect_disk(path: Path) -> DiskReport:
    """Report free space on the volume that will host `.venvs` and models."""
    try:
        return DiskReport(shutil.disk_usage(path).free)
    except OSError:
        return DiskReport(None)


def _adapter_fields(row: object) -> tuple[str, str]:
    if not isinstance(row, dict):
        return "", ""
    name = str(row.get("Name") or "")
    compatibility = str(row.get("AdapterCompatibility") or "")
    return name, compatibility


def _classify_vendor(name: str, compatibility: str) -> GpuVendor:
    text = f"{name} {compatibility}".casefold()
    if "nvidia" in text:
        return "nvidia"
    if "intel" in text:
        return "intel"
    if "amd" in text or "advanced micro devices" in text or "ati technologies" in text:
        return "amd"
    return "other"


def _dominant_vendor(adapters: tuple[GpuAdapter, ...]) -> GpuVendor:
    """Prefer NVIDIA, then Intel, then AMD/other - mirrors devices.py's own
    CUDA-then-Vulkan/OpenVINO-then-CPU priority for the in-venv auto selection,
    so a laptop with both an Intel iGPU and an NVIDIA dGPU recommends `cuda`."""
    vendors = {adapter.vendor for adapter in adapters}
    for vendor in ("nvidia", "intel", "amd"):
        if vendor in vendors:
            return vendor
    return "other" if vendors else "none"


def _bounded_error(error: str) -> str:
    return error.replace("\r", " ").replace("\n", " ").strip()[:500]
