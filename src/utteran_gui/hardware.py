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
import os
import platform
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

from utteran_gui.processes import build_creation_kwargs, build_popen_kwargs, kill_process_tree
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

# Match the environment endpoint's measured first-run allowance. The Core i7
# reference machine needed 105.7s, while seven 20s isolated probes plus tree
# termination overhead have a 140s+ theoretical ceiling.
_RUNTIME_PROBE_TIMEOUT_SECONDS = 200.0


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
class RuntimeCapabilities:
    """Actual accelerator results from an already-created inference profile."""

    source_profile: str | None
    ctranslate2_cuda: bool | None
    torch_cuda: bool | None
    openvino_gpu: bool | None
    torch_xpu: bool | None
    vulkan: bool | None
    error: str | None = None


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
    runtime: RuntimeCapabilities
    recommendation: ProfileRecommendation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HardwareProbeSet:
    """Injectable environment probes, mirroring utteran.devices.DeviceProbeSet."""

    gpu: Callable[[], GpuReport]
    memory: Callable[[], MemoryReport]
    disk: Callable[[Path], DiskReport]
    runtime: Callable[[Path], RuntimeCapabilities] | None = None


def system_probes() -> HardwareProbeSet:
    """Create the real, OS-backed probe collection."""
    return HardwareProbeSet(
        gpu=detect_gpu,
        memory=detect_memory,
        disk=detect_disk,
        runtime=detect_runtime_capabilities,
    )


def detect_hardware(repo_root: Path, *, probes: HardwareProbeSet | None = None) -> HardwareSnapshot:
    """Run independent probes and derive a profile recommendation from them."""
    selected = probes or system_probes()
    gpu = selected.gpu()
    memory = selected.memory()
    disk = selected.disk(repo_root)
    runtime = (
        selected.runtime(repo_root)
        if selected.runtime is not None
        else RuntimeCapabilities(None, None, None, None, None, None, "not_probed")
    )
    return HardwareSnapshot(
        os_supported=platform.system() == "Windows",
        gpu=gpu,
        memory=memory,
        disk=disk,
        runtime=runtime,
        recommendation=recommend_profile(gpu, runtime),
    )


def recommend_profile(
    gpu: GpuReport, runtime: RuntimeCapabilities | None = None
) -> ProfileRecommendation:
    """Recommend from actual runtime usability, retaining an honest fresh-install fallback.

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
    if runtime is not None and runtime.source_profile is not None:
        return _recommend_from_runtime(gpu, runtime)
    if gpu.dominant_vendor == "nvidia":
        return ProfileRecommendation(
            recommended="cuda",
            reasons=(
                "NVIDIA GPUを検出しました。",
                "文字起こしと話者分離の両方をGPUで実行できます。",
            ),
            alternatives=(_alternative("cuda"), _alternative("cpu")),
            detection_confident=False,
        )
    if gpu.dominant_vendor == "intel":
        return ProfileRecommendation(
            recommended="intel",
            reasons=(
                "Intel GPU(Arcまたは内蔵GPU)を検出しました。",
                "ランタイム未構築のためOpenVINOとXPUの利用可否はまだ判定できません。",
            ),
            alternatives=(_alternative("intel"), _alternative("cpu")),
            detection_confident=False,
        )
    if gpu.dominant_vendor == "amd" or gpu.dominant_vendor == "other":
        return ProfileRecommendation(
            recommended="vulkan",
            reasons=(
                "NVIDIA/Intel以外のGPUを検出しました。",
                "文字起こしはVulkanでGPU実行できますが、話者分離はCPUで実行されます。",
            ),
            alternatives=(_alternative("vulkan"), _alternative("cpu")),
            detection_confident=False,
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


def _recommend_from_runtime(gpu: GpuReport, runtime: RuntimeCapabilities) -> ProfileRecommendation:
    """Describe ASR and diarization independently; ``None`` always means unknown."""
    if runtime.ctranslate2_cuda is True or runtime.torch_cuda is True:
        both = runtime.ctranslate2_cuda is True and runtime.torch_cuda is True
        reasons = [
            "実プローブでNVIDIA CUDAランタイムを確認しました。",
            (
                "文字起こしと話者分離の両方をGPUで高速化できます。"
                if both
                else "文字起こしと話者分離のうち、利用可能と確認できた処理だけをGPUで実行します。"
            ),
        ]
        reasons.extend(_unknown_runtime_reasons(runtime))
        return ProfileRecommendation(
            "cuda",
            tuple(reasons),
            (_alternative("cuda", runtime), _alternative("cpu", runtime)),
            not _has_unknown(runtime),
        )
    if runtime.openvino_gpu is True or runtime.torch_xpu is True:
        reasons = ["Intel向けランタイムを実プローブしました。"]
        if runtime.openvino_gpu is True and runtime.torch_xpu is True:
            reasons.append("ASRと話者分離の両方をIntel GPUで高速化できます。")
        elif runtime.openvino_gpu is True:
            reasons.append("ASRはGPUで高速化できます。話者分離はCPU実行になります。")
        else:
            reasons.append("話者分離はGPUで高速化できます。ASRはCPU実行になります。")
        reasons.extend(_unknown_runtime_reasons(runtime))
        return ProfileRecommendation(
            "intel",
            tuple(reasons),
            (_alternative("intel", runtime), _alternative("cpu", runtime)),
            not _has_unknown(runtime),
        )
    if runtime.vulkan is True:
        reasons = [
            "実プローブでVulkanを確認しました。",
            "ASRはGPUで高速化できます。話者分離はCPU実行になります。",
        ]
        reasons.extend(_unknown_runtime_reasons(runtime))
        return ProfileRecommendation(
            "vulkan",
            tuple(reasons),
            (_alternative("vulkan", runtime), _alternative("cpu", runtime)),
            not _has_unknown(runtime),
        )
    reasons = [
        "実プローブで利用可能なGPUランタイムを確認できませんでした。",
        "安全なCPUプロファイルを推奨します。",
    ]
    reasons.extend(_unknown_runtime_reasons(runtime))
    return ProfileRecommendation(
        "cpu",
        tuple(reasons),
        (_alternative("cpu", runtime),),
        not _has_unknown(runtime),
    )


def _unknown_runtime_reasons(runtime: RuntimeCapabilities) -> list[str]:
    labels = []
    for name, value in (
        ("CTranslate2 CUDA", runtime.ctranslate2_cuda),
        ("PyTorch CUDA", runtime.torch_cuda),
        ("OpenVINO GPU", runtime.openvino_gpu),
        ("PyTorch XPU", runtime.torch_xpu),
        ("Vulkan", runtime.vulkan),
    ):
        if value is None:
            labels.append(name)
    return [f"{', '.join(labels)}の判定ができませんでした。"] if labels else []


def _has_unknown(runtime: RuntimeCapabilities) -> bool:
    return any(
        value is None
        for value in (
            runtime.ctranslate2_cuda,
            runtime.torch_cuda,
            runtime.openvino_gpu,
            runtime.torch_xpu,
            runtime.vulkan,
        )
    )


def _alternative(profile: str, runtime: RuntimeCapabilities | None = None) -> AlternativeProfile:
    if profile not in PROFILE_NAMES:
        raise ValueError(f"Unknown profile: {profile}")
    if profile == "cuda":
        return AlternativeProfile(
            profile="cuda",
            asr_accelerated=(True if runtime is None else runtime.ctranslate2_cuda is True),
            diarization_accelerated=(True if runtime is None else runtime.torch_cuda is True),
            approx_disk_bytes=_APPROX_DISK_BYTES["cuda"],
        )
    if profile == "intel":
        return AlternativeProfile(
            profile="intel",
            asr_accelerated=True if runtime is None else runtime.openvino_gpu is True,
            diarization_accelerated=True if runtime is None else runtime.torch_xpu is True,
            approx_disk_bytes=_APPROX_DISK_BYTES["intel"],
            extra_setup=(
                "whisper.cppのOpenVINO/Vulkanバックエンドを使う場合、"
                "`utteran native build`と`utteran models prepare-openvino`が"
                "追加で必要です(任意)。",
            ),
            caveat=(
                "話者分離はCPUで実行されます。"
                if runtime is not None
                and runtime.openvino_gpu is True
                and runtime.torch_xpu is not True
                else None
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


def detect_runtime_capabilities(repo_root: Path) -> RuntimeCapabilities:
    """Ask an existing inference profile for its isolated, cached runtime report."""
    suffix = "Scripts/utteran.exe" if os.name == "nt" else "bin/utteran"
    candidates = (
        ("cuda", repo_root / ".venvs" / "win-cuda" / suffix),
        ("intel", repo_root / ".venvs" / "win-intel" / suffix),
        ("vulkan", repo_root / ".venvs" / "win-vulkan" / suffix),
        ("cpu", repo_root / ".venvs" / "win-cpu" / suffix),
    )
    selected = next(((name, path) for name, path in candidates if path.is_file()), None)
    if selected is None:
        return RuntimeCapabilities(None, None, None, None, None, None, "profile_missing")
    profile, executable = selected
    environment = dict(os.environ)
    environment["UTTERAN_PROFILE"] = profile
    try:
        popen = cast(Callable[..., subprocess.Popen[str]], subprocess.Popen)
        process = popen(
            [str(executable), "devices", "--json"],
            **build_popen_kwargs(cwd=repo_root, env=environment),
        )
        try:
            stdout, stderr = process.communicate(timeout=_RUNTIME_PROBE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            kill_process_tree(process)
            stdout, stderr = process.communicate()
            return RuntimeCapabilities(
                profile, None, None, None, None, None, "runtime_probe_timeout"
            )
        if process.returncode != 0:
            return RuntimeCapabilities(
                profile,
                None,
                None,
                None,
                None,
                None,
                _bounded_error(stderr or f"exit={process.returncode}"),
            )
        payload = json.loads(stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return RuntimeCapabilities(profile, None, None, None, None, None, _bounded_error(str(exc)))
    if not isinstance(payload, dict):
        return RuntimeCapabilities(profile, None, None, None, None, None, "invalid_payload")
    ct2 = payload.get("ctranslate2", {})
    torch = payload.get("pytorch", {})
    openvino = payload.get("openvino", {})
    vulkan = payload.get("vulkan", {})
    if not all(isinstance(item, dict) for item in (ct2, torch, openvino, vulkan)):
        return RuntimeCapabilities(profile, None, None, None, None, None, "invalid_payload")
    return RuntimeCapabilities(
        source_profile=profile,
        ctranslate2_cuda=_known_capability(
            ct2.get("cuda_status"),
            any(
                isinstance(item, dict) and item.get("usable") is True
                for item in ct2.get("cuda_devices", [])
            ),
        ),
        torch_cuda=_known_capability(torch.get("cuda_status"), bool(torch.get("cuda_available"))),
        openvino_gpu=_known_capability(
            openvino.get("status"),
            bool(openvino.get("available"))
            and any(str(item).upper().startswith("GPU") for item in openvino.get("values", [])),
        ),
        torch_xpu=_known_capability(torch.get("xpu_status"), bool(torch.get("xpu_available"))),
        vulkan=_known_capability(vulkan.get("status"), bool(vulkan.get("runtime_available"))),
    )


def _known_capability(status: object, available: bool) -> bool | None:
    return available if status == "completed" else None


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
