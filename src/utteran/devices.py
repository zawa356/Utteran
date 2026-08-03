"""Hardware, runtime dependency, and backend auto-selection diagnostics."""

from __future__ import annotations

import ctypes.util
import importlib.util
import os
import platform
import shutil
import subprocess
import sysconfig
import warnings
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from utteran.audio import find_ffmpeg
from utteran.errors import BackendUnavailableError, FfmpegNotFoundError

_DLL_DIRECTORY_HANDLES: list[Any] = []


@dataclass(frozen=True)
class CPUReport:
    """CPU topology and instruction-set report."""

    logical_cores: int | None
    physical_cores: int | None
    avx2: bool | None
    avx512: bool | None


@dataclass(frozen=True)
class AcceleratorDevice:
    """One accelerator as seen by a specific runtime."""

    index: int
    name: str
    memory_bytes: int | None
    compute_types: tuple[str, ...] = ()
    usable: bool = False
    error: str | None = None


@dataclass(frozen=True)
class CTranslate2Report:
    """CTranslate2 CPU and CUDA capabilities."""

    available: bool
    version: str | None
    cpu_compute_types: tuple[str, ...]
    cuda_device_count: int
    cuda_devices: tuple[AcceleratorDevice, ...]
    error: str | None = None


@dataclass(frozen=True)
class TorchReport:
    """PyTorch availability and actually initializable CUDA devices."""

    available: bool
    version: str | None
    cuda_available: bool
    cuda_devices: tuple[AcceleratorDevice, ...]
    error: str | None = None


@dataclass(frozen=True)
class LibraryReport:
    """CUDA shared-library resolution report."""

    cudnn: str | None
    cublas: str | None


@dataclass(frozen=True)
class OptionalRuntimeReport:
    """Optional Phase 3 runtime and its advertised devices/providers."""

    available: bool
    values: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class FfmpegReport:
    """Resolved ffmpeg executable and first version line."""

    available: bool
    path: str | None
    version: str | None
    error: str | None = None


@dataclass(frozen=True)
class AutoSelection:
    """Backends and devices used by the currently implemented auto mode."""

    asr_backend: str
    asr_device: str
    asr_compute_type: str
    diarization_backend: str
    diarization_device: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProfileSummary:
    """One profile's presence and freshness, without launching its Python."""

    name: str
    exists: bool
    updated_at: str | None


@dataclass(frozen=True)
class ProfileReport:
    """Phase 3a environment-separation view: active profile and its siblings."""

    current: str | None
    profiles: tuple[ProfileSummary, ...]


@dataclass(frozen=True)
class VulkanReport:
    """Vulkan build and runtime prerequisites, reported separately.

    A machine can satisfy one without the other (confirmed while
    implementing native.py - see AISTATE.md I-3), so `devices` must not
    collapse them into a single available/unavailable flag.
    """

    build_available: bool
    build_error: str | None
    runtime_available: bool
    runtime_device: str | None
    runtime_error: str | None


@dataclass(frozen=True)
class NativeReport:
    """whisper.cpp native build status from the shared native_dir manifest."""

    built: bool
    whisper_cpp_tag: str | None
    variants: dict[str, bool]


@dataclass(frozen=True)
class DeviceReport:
    """Complete machine-readable devices command payload."""

    cpu: CPUReport
    ctranslate2: CTranslate2Report
    cuda_libraries: LibraryReport
    pytorch: TorchReport
    openvino: OptionalRuntimeReport
    onnxruntime: OptionalRuntimeReport
    ffmpeg: FfmpegReport
    backends: dict[str, bool]
    auto_selection: AutoSelection
    warnings: tuple[str, ...]
    profile: ProfileReport
    vulkan: VulkanReport
    native: NativeReport

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report for future GUI consumers."""
        return asdict(self)


@dataclass(frozen=True)
class DeviceProbeSet:
    """Injectable environment probes used by tests and platform adapters."""

    cpu: Callable[[], CPUReport]
    ctranslate2: Callable[[], CTranslate2Report]
    libraries: Callable[[], LibraryReport]
    torch: Callable[[], TorchReport]
    openvino: Callable[[], OptionalRuntimeReport]
    onnxruntime: Callable[[], OptionalRuntimeReport]
    ffmpeg: Callable[[Path | None], FfmpegReport]
    backends: Callable[[], dict[str, bool]]
    profile: Callable[[], ProfileReport]
    vulkan: Callable[[], VulkanReport]
    native: Callable[[], NativeReport]


@dataclass(frozen=True)
class FasterWhisperSelection:
    """Validated CTranslate2 runtime arguments."""

    device: str
    device_index: int
    compute_type: str
    note: str | None = None


def system_probes(
    *,
    venv_dir: Path | None = None,
    native_dir: Path | None = None,
) -> DeviceProbeSet:
    """Create the real environment probe collection."""
    return DeviceProbeSet(
        cpu=detect_cpu,
        ctranslate2=detect_ctranslate2,
        libraries=detect_cuda_libraries,
        torch=detect_torch,
        openvino=detect_openvino,
        onnxruntime=detect_onnxruntime,
        ffmpeg=detect_ffmpeg,
        backends=detect_backends,
        profile=lambda: detect_profile_report(venv_dir),
        vulkan=detect_vulkan,
        native=lambda: detect_native_report(native_dir),
    )


def detect_devices(
    ffmpeg_path: Path | None = None,
    *,
    venv_dir: Path | None = None,
    native_dir: Path | None = None,
    probes: DeviceProbeSet | None = None,
) -> DeviceReport:
    """Run independent probes and derive the current auto-mode decision."""
    if probes is None:
        register_cuda_dll_directories()
    selected_probes = probes or system_probes(venv_dir=venv_dir, native_dir=native_dir)
    cpu = selected_probes.cpu()
    raw_ctranslate2 = selected_probes.ctranslate2()
    libraries = selected_probes.libraries()
    ctranslate2 = _apply_cuda_library_status(raw_ctranslate2, libraries)
    torch = selected_probes.torch()
    openvino = selected_probes.openvino()
    onnxruntime = selected_probes.onnxruntime()
    ffmpeg = selected_probes.ffmpeg(ffmpeg_path)
    backends = selected_probes.backends()
    profile = selected_probes.profile()
    vulkan = selected_probes.vulkan()
    native = selected_probes.native()
    auto_selection, warnings = _auto_selection(ctranslate2, torch, openvino)
    if ctranslate2.cuda_device_count and not any(
        device.usable for device in ctranslate2.cuda_devices
    ):
        warnings.append(
            "CUDA デバイスは列挙されましたが CTranslate2 で初期化できません。"
            "cuDNN / cuBLAS とドライバーを確認してください。"
        )
    return DeviceReport(
        cpu=cpu,
        ctranslate2=ctranslate2,
        cuda_libraries=libraries,
        pytorch=torch,
        openvino=openvino,
        onnxruntime=onnxruntime,
        ffmpeg=ffmpeg,
        backends=backends,
        auto_selection=auto_selection,
        warnings=tuple(warnings),
        profile=profile,
        vulkan=vulkan,
        native=native,
    )


def select_faster_whisper_device(
    requested_device: str,
    requested_compute_type: str,
    *,
    report: CTranslate2Report | None = None,
) -> FasterWhisperSelection:
    """Validate explicit choices and safely resolve auto compute fallbacks."""
    if report is None:
        register_cuda_dll_directories()
    runtime = report or _apply_cuda_library_status(
        detect_ctranslate2(),
        detect_cuda_libraries(),
    )
    if not runtime.available:
        raise BackendUnavailableError(
            "CTranslate2 を読み込めません。`uv sync` を実行してください。"
        )

    requested = requested_device.casefold()
    if requested == "auto":
        cuda = next((device for device in runtime.cuda_devices if device.usable), None)
        if cuda is not None:
            compute = _choose_compute_type(requested_compute_type, cuda.compute_types, "cuda")
            note = None
            if requested_compute_type == "auto" and compute != "float16":
                note = f"CUDA float16 が利用できないため {compute} を使用します。"
            return FasterWhisperSelection("cuda", cuda.index, compute, note)
        compute = _choose_compute_type(
            requested_compute_type,
            runtime.cpu_compute_types,
            "cpu",
        )
        note = "CUDA を初期化できないため CPU を使用します。" if runtime.cuda_device_count else None
        return FasterWhisperSelection("cpu", 0, compute, note)

    if requested == "cpu":
        return FasterWhisperSelection(
            "cpu",
            0,
            _choose_compute_type(requested_compute_type, runtime.cpu_compute_types, "cpu"),
        )
    if requested == "cuda":
        index = 0
    elif requested.startswith("cuda:"):
        try:
            index = int(requested.partition(":")[2])
        except ValueError:
            raise BackendUnavailableError(
                f"不正な CUDA デバイス指定です: {requested_device}"
            ) from None
    else:
        raise BackendUnavailableError(
            f"faster-whisper が対応していないデバイスです: {requested_device}"
        )
    device = next((item for item in runtime.cuda_devices if item.index == index), None)
    if device is None or not device.usable:
        detail = f" ({device.error})" if device is not None and device.error else ""
        raise BackendUnavailableError(
            f"明示指定された cuda:{index} を CTranslate2 で初期化できません。{detail}"
            "自動フォールバックは行いません。"
        )
    return FasterWhisperSelection(
        "cuda",
        index,
        _choose_compute_type(requested_compute_type, device.compute_types, "cuda"),
    )


def detect_cpu() -> CPUReport:
    """Detect topology and flags without requiring a third-party package."""
    flags = _cpu_flags()
    physical = _physical_cpu_count()
    return CPUReport(
        logical_cores=os.cpu_count(),
        physical_cores=physical,
        avx2=None if flags is None else "avx2" in flags,
        avx512=(None if flags is None else any(flag.startswith("avx512") for flag in flags)),
    )


def detect_ctranslate2() -> CTranslate2Report:
    """Probe CTranslate2 devices by requesting their supported compute types."""
    register_cuda_dll_directories()
    if importlib.util.find_spec("ctranslate2") is None:
        return CTranslate2Report(False, None, (), 0, (), "未導入")
    try:
        import ctranslate2

        cpu_types = tuple(sorted(ctranslate2.get_supported_compute_types("cpu")))
        count = int(ctranslate2.get_cuda_device_count())
        metadata = _nvidia_metadata()
        cuda_devices: list[AcceleratorDevice] = []
        for index in range(count):
            name, memory = metadata.get(index, (f"NVIDIA CUDA {index}", None))
            try:
                compute_types = tuple(
                    sorted(ctranslate2.get_supported_compute_types("cuda", index))
                )
                cuda_devices.append(
                    AcceleratorDevice(
                        index,
                        name,
                        memory,
                        compute_types,
                        bool(compute_types),
                    )
                )
            except Exception as exc:
                cuda_devices.append(
                    AcceleratorDevice(index, name, memory, error=_bounded_error(exc))
                )
        return CTranslate2Report(
            True,
            str(getattr(ctranslate2, "__version__", "unknown")),
            cpu_types,
            count,
            tuple(cuda_devices),
        )
    except Exception as exc:
        return CTranslate2Report(False, None, (), 0, (), _bounded_error(exc))


def detect_torch() -> TorchReport:
    """Probe PyTorch CUDA with a minimal kernel, host copy, and synchronization."""
    register_cuda_dll_directories()
    if importlib.util.find_spec("torch") is None:
        return TorchReport(False, None, False, (), "未導入")
    try:
        import torch

        devices: list[AcceleratorDevice] = []
        if torch.cuda.is_available():
            for index in range(torch.cuda.device_count()):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        properties = torch.cuda.get_device_properties(index)
                        probe = torch.ones(1, device=f"cuda:{index}")
                        result = (probe + 1).cpu()
                        torch.cuda.synchronize(index)
                    if float(result.item()) != 2.0:
                        raise RuntimeError("CUDA probe returned an unexpected result")
                    del probe, result
                    devices.append(
                        AcceleratorDevice(
                            index,
                            str(properties.name),
                            int(properties.total_memory),
                            usable=True,
                        )
                    )
                except Exception as exc:
                    devices.append(
                        AcceleratorDevice(
                            index,
                            f"CUDA {index}",
                            None,
                            error=_bounded_error(exc),
                        )
                    )
        usable = any(device.usable for device in devices)
        return TorchReport(
            True,
            str(getattr(torch, "__version__", "unknown")),
            usable,
            tuple(devices),
        )
    except Exception as exc:
        return TorchReport(False, None, False, (), _bounded_error(exc))


def detect_cuda_libraries() -> LibraryReport:
    """Resolve cuDNN and cuBLAS by loader name or a PATH directory."""
    return LibraryReport(
        cudnn=_find_shared_library(("cudnn", "libcudnn"), ("cudnn*.dll", "libcudnn.so*")),
        cublas=_find_shared_library(
            ("cublas", "libcublas"),
            ("cublas*.dll", "libcublas.so*"),
        ),
    )


def detect_openvino() -> OptionalRuntimeReport:
    """Report OpenVINO devices when the optional Phase 3 runtime is installed."""
    if importlib.util.find_spec("openvino") is None:
        return OptionalRuntimeReport(False, (), "未導入")
    try:
        from openvino import Core

        return OptionalRuntimeReport(True, tuple(str(item) for item in Core().available_devices))
    except Exception as exc:
        return OptionalRuntimeReport(False, (), _bounded_error(exc))


def detect_onnxruntime() -> OptionalRuntimeReport:
    """Report ONNX Runtime execution providers without importing a backend."""
    if importlib.util.find_spec("onnxruntime") is None:
        return OptionalRuntimeReport(False, (), "未導入")
    try:
        import onnxruntime

        return OptionalRuntimeReport(
            True,
            tuple(str(item) for item in onnxruntime.get_available_providers()),
        )
    except Exception as exc:
        return OptionalRuntimeReport(False, (), _bounded_error(exc))


def detect_ffmpeg(configured_path: Path | None = None) -> FfmpegReport:
    """Report the product's resolved ffmpeg and a bounded version string."""
    try:
        path = find_ffmpeg(configured_path)
    except FfmpegNotFoundError as exc:
        return FfmpegReport(False, None, None, str(exc))
    try:
        completed = subprocess.run(
            [str(path), "-version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        first_line = completed.stdout.splitlines()[0] if completed.stdout else None
        return FfmpegReport(completed.returncode == 0, str(path), first_line)
    except (OSError, subprocess.SubprocessError) as exc:
        return FfmpegReport(False, str(path), None, _bounded_error(exc))


def detect_backends() -> dict[str, bool]:
    """Report current and future backend package availability."""
    return {
        "faster-whisper": _module_available("faster_whisper"),
        "pyannote": _module_available("pyannote.audio"),
        "openvino": _module_available("openvino"),
        "sherpa-onnx": _module_available("sherpa_onnx"),
    }


def detect_profile_report(venv_dir: Path | None = None) -> ProfileReport:
    """Report the active profile (if any) and every profile's presence.

    Only reads directory existence and mtimes for the non-current
    profiles - launching each one's Python is unnecessary for this view
    and would make `devices` slow.
    """
    from utteran.profiles import current_profile_name, list_profile_statuses, resolve_venv_root

    root = resolve_venv_root(Path.cwd(), configured=venv_dir)
    statuses = list_profile_statuses(root)
    return ProfileReport(
        current=current_profile_name(),
        profiles=tuple(
            ProfileSummary(status.name, status.exists, status.updated_at) for status in statuses
        ),
    )


def detect_vulkan() -> VulkanReport:
    """Report Vulkan build (glslc) and runtime (vulkaninfo) prerequisites separately."""
    from utteran.native import probe_glslc, probe_vulkan_runtime

    build = probe_glslc()
    runtime, device = probe_vulkan_runtime()
    return VulkanReport(
        build_available=build.available,
        build_error=None if build.available else build.detail,
        runtime_available=runtime.available,
        runtime_device=device,
        runtime_error=None if runtime.available else runtime.detail,
    )


def detect_native_report(native_dir: Path | None = None) -> NativeReport:
    """Report the shared whisper.cpp native build manifest and runnable variants."""
    from utteran.native import VARIANT_NAMES, NativeBuilder, resolve_native_dir

    builder = NativeBuilder(resolve_native_dir(native_dir))
    status = builder.status()
    manifest = status["manifest"]
    whisper_cpp = manifest.get("whisper_cpp") if isinstance(manifest, dict) else None
    tag = whisper_cpp.get("tag") if isinstance(whisper_cpp, dict) else None
    runnable = status["runnable"] if isinstance(status["runnable"], dict) else {}
    return NativeReport(
        built=bool(manifest),
        whisper_cpp_tag=tag,
        variants={name: bool(runnable.get(name, False)) for name in VARIANT_NAMES},
    )


def _auto_selection(
    ctranslate2: CTranslate2Report,
    torch: TorchReport,
    openvino: OptionalRuntimeReport,
) -> tuple[AutoSelection, list[str]]:
    """Select implemented backends and explain Phase 3 acceleration opportunities."""
    notes: list[str] = []
    try:
        asr = select_faster_whisper_device("auto", "auto", report=ctranslate2)
        if asr.note:
            notes.append(asr.note)
        asr_device = f"cuda:{asr.device_index}" if asr.device == "cuda" else "cpu"
        asr_compute = asr.compute_type
    except BackendUnavailableError as exc:
        asr_device = "unavailable"
        asr_compute = "unavailable"
        notes.append(str(exc))
    torch_cuda = next((device for device in torch.cuda_devices if device.usable), None)
    diarization_device = f"cuda:{torch_cuda.index}" if torch_cuda is not None else "cpu"
    intel_accelerators = tuple(
        item for item in openvino.values if item.upper().startswith(("GPU", "NPU"))
    )
    warnings: list[str] = []
    if asr_device == "cpu" and intel_accelerators:
        warnings.append(
            "Intel GPU / NPU が検出されました。ASR は OpenVINO で高速化できますが、"
            "Phase 2 の実装済み ASR は faster-whisper CPU、話者分離は pyannote CPU です。"
        )
    return (
        AutoSelection(
            asr_backend="faster-whisper",
            asr_device=asr_device,
            asr_compute_type=asr_compute,
            diarization_backend="pyannote",
            diarization_device=diarization_device,
            notes=tuple(notes),
        ),
        warnings,
    )


def _choose_compute_type(requested: str, supported: tuple[str, ...], device: str) -> str:
    """Choose a supported type without silently changing an explicit request."""
    if requested != "auto":
        if requested not in supported:
            raise BackendUnavailableError(
                f"{device} は compute_type={requested} に対応していません。"
                f"対応値: {', '.join(supported) or 'なし'}"
            )
        return requested
    preference = (
        ("float16", "int8_float16", "int8", "float32", "int8_float32")
        if device == "cuda"
        else ("int8", "int8_float32", "float32", "int16")
    )
    selected = next((item for item in preference if item in supported), None)
    if selected is None:
        raise BackendUnavailableError(f"{device} で利用可能な compute_type がありません。")
    return selected


def _apply_cuda_library_status(
    report: CTranslate2Report,
    libraries: LibraryReport,
) -> CTranslate2Report:
    """Reject CUDA auto selection when required inference libraries are unresolved."""
    missing = [
        name
        for name, value in (("cuDNN", libraries.cudnn), ("cuBLAS", libraries.cublas))
        if value is None
    ]
    if not missing:
        return report
    detail = f"未解決の共有ライブラリ: {', '.join(missing)}"
    devices = tuple(
        replace(device, usable=False, error=device.error or detail)
        for device in report.cuda_devices
    )
    return replace(report, cuda_devices=devices)


def _cpu_flags() -> set[str] | None:
    """Read CPU flags from Linux procfs or macOS sysctl when available."""
    if platform.system() == "Windows":
        return _windows_cpu_flags()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        try:
            for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip().casefold() in {"flags", "features"}:
                    return {item.casefold() for item in value.split()}
        except OSError:
            return None
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.features", "machdep.cpu.leaf7_features"],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            return {item.casefold() for item in result.stdout.split()}
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def _physical_cpu_count() -> int | None:
    """Count unique Linux physical/core IDs; return unknown elsewhere."""
    if platform.system() == "Windows":
        return _windows_physical_cpu_count()
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.is_file():
        return None
    try:
        pairs: set[tuple[str, str]] = set()
        physical = "0"
        core: str | None = None
        lines = cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line in (*lines, ""):
            if not line:
                if core is not None:
                    pairs.add((physical, core))
                physical, core = "0", None
                continue
            key, separator, value = line.partition(":")
            if not separator:
                continue
            if key.strip() == "physical id":
                physical = value.strip()
            elif key.strip() == "core id":
                core = value.strip()
        return len(pairs) or None
    except OSError:
        return None


def _nvidia_metadata() -> dict[int, tuple[str, int | None]]:
    """Query optional nvidia-smi for names and total VRAM."""
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return {}
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    metadata: dict[int, tuple[str, int | None]] = {}
    for index, line in enumerate(result.stdout.splitlines()):
        name, separator, memory = line.rpartition(",")
        if not separator:
            continue
        try:
            memory_bytes = int(memory.strip()) * 1024 * 1024
        except ValueError:
            memory_bytes = None
        metadata[index] = (name.strip(), memory_bytes)
    return metadata


def _find_shared_library(names: tuple[str, ...], globs: tuple[str, ...]) -> str | None:
    """Resolve a loader name first, then scan explicit PATH directories."""
    for name in names:
        if resolved := ctypes.util.find_library(name):
            return resolved
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        directory = Path(raw_directory)
        if not directory.is_dir():
            continue
        for pattern in globs:
            match = next(directory.glob(pattern), None)
            if match is not None:
                return str(match)
    for directory in _cuda_dependency_directories():
        for pattern in globs:
            match = next(directory.glob(pattern), None)
            if match is not None:
                return str(match)
    return None


def register_cuda_dll_directories() -> tuple[Path, ...]:
    """Register CUDA DLL directories shipped inside the active Windows environment."""
    directories = _cuda_dependency_directories()
    add_directory = getattr(os, "add_dll_directory", None)
    if platform.system() != "Windows" or add_directory is None:
        return directories
    registered = {str(getattr(handle, "path", "")) for handle in _DLL_DIRECTORY_HANDLES}
    for directory in directories:
        if str(directory) in registered:
            continue
        try:
            _DLL_DIRECTORY_HANDLES.append(add_directory(str(directory)))
        except OSError:
            continue
    return directories


def _cuda_dependency_directories() -> tuple[Path, ...]:
    """Find package-local CUDA DLL directories without importing heavy runtimes."""
    candidates: list[Path] = []
    try:
        purelib = Path(sysconfig.get_paths()["purelib"])
        candidates.extend(
            (
                purelib / "nvidia" / "cublas" / "bin",
                purelib / "nvidia" / "cudnn" / "bin",
                purelib / "nvidia" / "cuda_nvrtc" / "bin",
            )
        )
    except (KeyError, OSError):
        pass
    try:
        torch_spec = importlib.util.find_spec("torch")
        if torch_spec is not None and torch_spec.origin is not None:
            candidates.append(Path(torch_spec.origin).parent / "lib")
    except (ImportError, ModuleNotFoundError, ValueError):
        pass
    unique: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _windows_cpu_flags() -> set[str] | None:
    """Read AVX feature flags through the Windows processor feature API."""
    try:
        loader_name = "WinDLL"
        kernel32 = getattr(ctypes, loader_name)("kernel32", use_last_error=True)
        feature_present = kernel32.IsProcessorFeaturePresent
        feature_present.argtypes = [ctypes.c_uint]
        feature_present.restype = ctypes.c_bool
        features = {
            name
            for name, feature_id in (("avx2", 40), ("avx512f", 41))
            if feature_present(feature_id)
        }
        return features
    except (AttributeError, OSError):
        return None


def _windows_physical_cpu_count() -> int | None:
    """Count Windows processor-core relationship records via Kernel32."""
    try:
        loader_name = "WinDLL"
        kernel32 = getattr(ctypes, loader_name)("kernel32", use_last_error=True)
        get_information = kernel32.GetLogicalProcessorInformationEx
        get_information.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_information.restype = ctypes.c_bool
        length = ctypes.c_uint32(0)
        get_information(0, None, ctypes.byref(length))
        if length.value < 8:
            return None
        buffer = ctypes.create_string_buffer(length.value)
        if not get_information(0, buffer, ctypes.byref(length)):
            return None
        raw = buffer.raw[: length.value]
        count = 0
        offset = 0
        while offset + 8 <= len(raw):
            relationship = int.from_bytes(raw[offset : offset + 4], "little")
            size = int.from_bytes(raw[offset + 4 : offset + 8], "little")
            if size < 8 or offset + size > len(raw):
                return None
            if relationship == 0:
                count += 1
            offset += size
        return count or None
    except (AttributeError, OSError):
        return None


def _bounded_error(error: Exception) -> str:
    """Return a short diagnostic without backend traceback data."""
    return str(error).replace("\r", " ").replace("\n", " ")[:500]


def _module_available(name: str) -> bool:
    """Check optional dotted modules without propagating a missing parent import."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
