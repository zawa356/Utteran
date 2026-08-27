"""Hardware, runtime dependency, and backend auto-selection diagnostics."""

from __future__ import annotations

import ctypes.util
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import os
import platform
import shutil
import signal
import subprocess
import sys
import sysconfig
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

from platformdirs import user_cache_dir

from utteran.audio import find_ffmpeg
from utteran.errors import BackendUnavailableError, FfmpegNotFoundError
from utteran.logging import structured_event

_DLL_DIRECTORY_HANDLES: list[Any] = []
DEFAULT_PROBE_TIMEOUT_SECONDS = 20.0
_PROBE_CACHE_SCHEMA = 1
_LOGGER = logging.getLogger(__name__)
ProbeState = Literal["completed", "timeout", "error"]


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
    cpu_status: ProbeState = "completed"
    cuda_status: ProbeState = "completed"


@dataclass(frozen=True)
class TorchReport:
    """PyTorch availability and actually initializable CUDA/XPU devices."""

    available: bool
    version: str | None
    cuda_available: bool
    cuda_devices: tuple[AcceleratorDevice, ...]
    error: str | None = None
    xpu_available: bool = False
    xpu_devices: tuple[AcceleratorDevice, ...] = ()
    cuda_status: ProbeState = "completed"
    xpu_status: ProbeState = "completed"


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
    status: ProbeState = "completed"


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
    status: ProbeState = "completed"


@dataclass(frozen=True)
class ProbeOutcome:
    """Observable result of one isolated native probe."""

    name: str
    label: str
    status: ProbeState
    duration_seconds: float
    detail: str | None = None
    cached: bool = False


@dataclass(frozen=True)
class ProbeProgress:
    """Progress notification suitable for both terminal and GUI streams."""

    name: str
    label: str
    position: int
    total: int
    state: Literal["started", "completed", "timeout", "error", "cached"]


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
    probes: tuple[ProbeOutcome, ...] = ()
    probe_cache_hit: bool = False

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
class _IsolatedReports:
    ctranslate2: CTranslate2Report
    torch: TorchReport
    openvino: OptionalRuntimeReport
    onnxruntime: OptionalRuntimeReport
    vulkan: VulkanReport
    outcomes: tuple[ProbeOutcome, ...]


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
    refresh: bool = False,
    probe_timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    cache_path: Path | None = None,
    progress: Callable[[ProbeProgress], None] | None = None,
    hardware_fingerprint: str | None = None,
) -> DeviceReport:
    """Run isolated probes, cache them, and derive the current auto-mode decision."""
    if probe_timeout_seconds <= 0:
        raise ValueError("probe_timeout_seconds must be greater than zero")
    selected_probes = probes or system_probes(venv_dir=venv_dir, native_dir=native_dir)
    cpu = selected_probes.cpu()
    probe_outcomes: tuple[ProbeOutcome, ...] = ()
    cache_hit = False
    if probes is None:
        selected_cache = cache_path or default_probe_cache_path()
        fingerprint = hardware_fingerprint or device_probe_fingerprint()
        isolated = None if refresh else _load_probe_cache(selected_cache, fingerprint)
        if isolated is None:
            isolated = _detect_isolated_runtimes(probe_timeout_seconds, progress)
            _save_probe_cache(selected_cache, fingerprint, isolated)
        else:
            cache_hit = True
            isolated = replace(
                isolated,
                outcomes=tuple(replace(item, cached=True) for item in isolated.outcomes),
            )
            if progress is not None:
                total = len(isolated.outcomes)
                for position, outcome in enumerate(isolated.outcomes, start=1):
                    progress(
                        ProbeProgress(
                            outcome.name,
                            outcome.label,
                            position,
                            total,
                            "cached",
                        )
                    )
        raw_ctranslate2 = isolated.ctranslate2
        torch = isolated.torch
        openvino = isolated.openvino
        onnxruntime = isolated.onnxruntime
        vulkan = isolated.vulkan
        probe_outcomes = isolated.outcomes
    else:
        raw_ctranslate2 = selected_probes.ctranslate2()
        torch = selected_probes.torch()
        openvino = selected_probes.openvino()
        onnxruntime = selected_probes.onnxruntime()
        vulkan = selected_probes.vulkan()
    libraries = selected_probes.libraries()
    ctranslate2 = _apply_cuda_library_status(raw_ctranslate2, libraries)
    ffmpeg = selected_probes.ffmpeg(ffmpeg_path)
    backends = selected_probes.backends()
    profile = selected_probes.profile()
    native = selected_probes.native()
    auto_selection, warnings = _auto_selection(ctranslate2, torch, openvino, vulkan, native)
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
        probes=probe_outcomes,
        probe_cache_hit=cache_hit,
    )


@dataclass(frozen=True)
class _ProbeRun:
    outcome: ProbeOutcome
    result: dict[str, Any] | None


def default_probe_cache_path() -> Path:
    """Return the profile-independent platform cache used by ``devices``."""
    return Path(user_cache_dir("utteran")) / "device-probes-v1.json"


def device_probe_fingerprint() -> str:
    """Hash hardware, driver, interpreter, and runtime-package identities.

    Only the SHA-256 digest is stored.  Device serials, user names, and the raw
    environment are deliberately excluded from the cache.
    """
    packages: dict[str, str | None] = {}
    for distribution in ("ctranslate2", "torch", "openvino", "onnxruntime"):
        try:
            packages[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            packages[distribution] = None
    identity: dict[str, object] = {
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "executable": str(Path(sys.executable).resolve()),
        "packages": packages,
        "drivers": _driver_identity(),
    }
    encoded = json.dumps(identity, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_isolated_probe(
    name: str,
    label: str,
    timeout_seconds: float,
    *,
    argument: str | None = None,
    command: list[str] | None = None,
) -> _ProbeRun:
    """Run one JSON probe in a killable process group.

    ``command`` exists for deterministic timeout/process-tree acceptance tests;
    production calls always use the private one-shot worker module.
    """
    if command is None and getattr(sys, "frozen", False):
        # `sys.executable` inside a PyInstaller-frozen process is the frozen
        # executable itself, not a plain interpreter - `-m module` would
        # relaunch the whole packaged app instead of the tiny probe worker.
        # This module is never bundled into the frozen GUI today (excluded
        # by packaging/gui.spec's inference-core guard - see AISTATE.md
        # Phase 5l), so this should be unreachable, but fail loudly instead
        # of silently spawning copies of the host application if that ever
        # changes.
        raise RuntimeError(
            "run_isolated_probe cannot use sys.executable inside a frozen build; "
            "pass an explicit command= for a real interpreter."
        )
    selected_command = command or [sys.executable, "-m", "utteran._device_probe", name]
    if argument is not None and command is None:
        selected_command.append(argument)
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    started = time.monotonic()
    try:
        popen = cast(Callable[..., subprocess.Popen[str]], subprocess.Popen)
        process = popen(selected_command, **kwargs)
    except OSError as exc:
        outcome = ProbeOutcome(name, label, "error", 0.0, _bounded_error(exc))
        _log_probe_outcome(outcome)
        return _ProbeRun(outcome, None)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_probe_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        outcome = ProbeOutcome(
            name,
            label,
            "timeout",
            time.monotonic() - started,
            f"{timeout_seconds:g}秒でタイムアウト (判定不能)",
        )
        _log_probe_outcome(outcome)
        return _ProbeRun(outcome, None)
    duration = time.monotonic() - started
    if process.returncode != 0:
        detail = _bounded_error(stderr or f"probe exited with {process.returncode}")
        outcome = ProbeOutcome(name, label, "error", duration, detail)
        _log_probe_outcome(outcome)
        return _ProbeRun(outcome, None)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        outcome = ProbeOutcome(name, label, "error", duration, _bounded_error(exc))
        _log_probe_outcome(outcome)
        return _ProbeRun(outcome, None)
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        detail = str(payload.get("error", "invalid probe response"))[:500]
        outcome = ProbeOutcome(name, label, "error", duration, detail)
        _log_probe_outcome(outcome)
        return _ProbeRun(outcome, None)
    result = payload.get("result")
    if not isinstance(result, dict):
        outcome = ProbeOutcome(name, label, "error", duration, "invalid probe result")
        _log_probe_outcome(outcome)
        return _ProbeRun(outcome, None)
    outcome = ProbeOutcome(name, label, "completed", duration)
    _log_probe_outcome(outcome)
    return _ProbeRun(outcome, cast(dict[str, Any], result))


def _kill_probe_process_tree(process: subprocess.Popen[str]) -> None:
    """Force-stop a timed-out probe and all descendants using Phase 2 semantics."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if process.poll() is None:
            process.kill()
        return
    killpg = cast(Callable[[int, int], None], getattr(os, "killpg"))  # noqa: B009
    try:
        killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        killpg(process.pid, cast(int, getattr(signal, "SIGKILL", signal.SIGTERM)))
    except ProcessLookupError:
        return


def _log_probe_outcome(outcome: ProbeOutcome) -> None:
    level = logging.WARNING if outcome.status in {"timeout", "error"} else logging.DEBUG
    _LOGGER.log(level, "Device probe %s: %s", outcome.label, outcome.status)
    structured_event(
        "device_probe",
        level=level,
        probe=outcome.name,
        probe_status=outcome.status,
        duration_seconds=round(outcome.duration_seconds, 3),
    )


def _detect_isolated_runtimes(
    timeout_seconds: float,
    progress: Callable[[ProbeProgress], None] | None,
) -> _IsolatedReports:
    outcomes: list[ProbeOutcome] = []
    position = 0
    total = 7

    def execute(name: str, label: str, *, argument: str | None = None) -> _ProbeRun:
        nonlocal position
        position += 1
        if progress is not None:
            progress(ProbeProgress(name, label, position, total, "started"))
        run = run_isolated_probe(name, label, timeout_seconds, argument=argument)
        outcomes.append(run.outcome)
        if progress is not None:
            progress(ProbeProgress(name, label, position, total, cast(Any, run.outcome.status)))
        return run

    def unavailable(name: str, label: str) -> _ProbeRun:
        nonlocal position
        position += 1
        outcome = ProbeOutcome(name, label, "completed", 0.0, "未導入")
        outcomes.append(outcome)
        if progress is not None:
            progress(ProbeProgress(name, label, position, total, "completed"))
        return _ProbeRun(outcome, None)

    ct2_installed = importlib.util.find_spec("ctranslate2") is not None
    ct2_cpu = (
        execute("ctranslate2_cpu", "CTranslate2 CPU")
        if ct2_installed
        else unavailable("ctranslate2_cpu", "CTranslate2 CPU")
    )
    ct2_count = (
        execute("ctranslate2_cuda_count", "CTranslate2 CUDA device count")
        if ct2_installed
        else unavailable("ctranslate2_cuda_count", "CTranslate2 CUDA device count")
    )
    count = int(ct2_count.result.get("count", 0)) if ct2_count.result is not None else 0
    metadata: dict[int, tuple[str, int | None]] = {}
    cuda_runs: list[_ProbeRun] = []
    if count:
        total += count + 1
        metadata_run = execute("nvidia_metadata", "NVIDIA driver metadata")
        if metadata_run.result is not None:
            metadata = _parse_nvidia_metadata(str(metadata_run.result.get("stdout", "")))
        for index in range(count):
            cuda_runs.append(
                execute(
                    "ctranslate2_cuda",
                    f"CTranslate2 cuda:{index} compute types",
                    argument=str(index),
                )
            )
    ctranslate2 = _combine_ctranslate2(ct2_installed, ct2_cpu, ct2_count, cuda_runs, metadata)

    torch_installed = importlib.util.find_spec("torch") is not None
    torch_cuda = (
        execute("torch_cuda", "PyTorch CUDA")
        if torch_installed
        else unavailable("torch_cuda", "PyTorch CUDA")
    )
    torch_xpu = (
        execute("torch_xpu", "PyTorch XPU")
        if torch_installed
        else unavailable("torch_xpu", "PyTorch XPU")
    )
    torch = _combine_torch(torch_installed, torch_cuda, torch_xpu)

    openvino_installed = importlib.util.find_spec("openvino") is not None
    openvino_run = (
        execute("openvino", "OpenVINO devices")
        if openvino_installed
        else unavailable("openvino", "OpenVINO devices")
    )
    openvino = _optional_report(openvino_installed, openvino_run)

    onnx_installed = importlib.util.find_spec("onnxruntime") is not None
    onnx_run = (
        execute("onnxruntime", "ONNX Runtime providers")
        if onnx_installed
        else unavailable("onnxruntime", "ONNX Runtime providers")
    )
    onnxruntime = _optional_report(onnx_installed, onnx_run)

    vulkan_run = execute("vulkan", "Vulkan build/runtime")
    vulkan = _vulkan_report(vulkan_run)
    return _IsolatedReports(ctranslate2, torch, openvino, onnxruntime, vulkan, tuple(outcomes))


def _combine_ctranslate2(
    installed: bool,
    cpu: _ProbeRun,
    count_run: _ProbeRun,
    cuda_runs: list[_ProbeRun],
    metadata: dict[int, tuple[str, int | None]],
) -> CTranslate2Report:
    if not installed:
        return CTranslate2Report(False, None, (), 0, (), "未導入")
    version = None
    if cpu.result is not None:
        version = str(cpu.result.get("version", "unknown"))
    elif count_run.result is not None:
        version = str(count_run.result.get("version", "unknown"))
    cpu_types = (
        tuple(str(item) for item in cpu.result.get("compute_types", ()))
        if cpu.result is not None
        else ()
    )
    count = int(count_run.result.get("count", 0)) if count_run.result is not None else 0
    devices: list[AcceleratorDevice] = []
    for index, run in enumerate(cuda_runs):
        name, memory = metadata.get(index, (f"NVIDIA CUDA {index}", None))
        compute_types = (
            tuple(str(item) for item in run.result.get("compute_types", ()))
            if run.result is not None
            else ()
        )
        devices.append(
            AcceleratorDevice(
                index,
                name,
                memory,
                compute_types,
                bool(compute_types) and run.outcome.status == "completed",
                run.outcome.detail,
            )
        )
    cuda_states = [count_run.outcome.status, *(item.outcome.status for item in cuda_runs)]
    cuda_status: ProbeState = (
        "timeout"
        if "timeout" in cuda_states
        else "error"
        if "error" in cuda_states
        else "completed"
    )
    details = [
        item.outcome.detail
        for item in (cpu, count_run, *cuda_runs)
        if item.outcome.status != "completed" and item.outcome.detail
    ]
    return CTranslate2Report(
        cpu.outcome.status == "completed" or count_run.outcome.status == "completed",
        version,
        cpu_types,
        count,
        tuple(devices),
        "; ".join(details) or None,
        cpu.outcome.status,
        cuda_status,
    )


def _accelerators(result: dict[str, Any] | None) -> tuple[AcceleratorDevice, ...]:
    if result is None:
        return ()
    devices: list[AcceleratorDevice] = []
    raw_devices = result.get("devices", ())
    if not isinstance(raw_devices, list):
        return ()
    for raw in raw_devices:
        if not isinstance(raw, dict):
            continue
        devices.append(
            AcceleratorDevice(
                int(raw.get("index", len(devices))),
                str(raw.get("name", "unknown")),
                None if raw.get("memory_bytes") is None else int(raw["memory_bytes"]),
                tuple(str(item) for item in raw.get("compute_types", ())),
                bool(raw.get("usable", False)),
                None if raw.get("error") is None else str(raw["error"]),
            )
        )
    return tuple(devices)


def _combine_torch(installed: bool, cuda: _ProbeRun, xpu: _ProbeRun) -> TorchReport:
    if not installed:
        return TorchReport(False, None, False, (), "未導入")
    cuda_devices = _accelerators(cuda.result)
    xpu_devices = _accelerators(xpu.result)
    source = cuda.result or xpu.result or {}
    details = [
        run.outcome.detail
        for run in (cuda, xpu)
        if run.outcome.status != "completed" and run.outcome.detail
    ]
    return TorchReport(
        True,
        str(source.get("version", "unknown")),
        any(item.usable for item in cuda_devices),
        cuda_devices,
        "; ".join(details) or None,
        any(item.usable for item in xpu_devices),
        xpu_devices,
        cuda.outcome.status,
        xpu.outcome.status,
    )


def _optional_report(installed: bool, run: _ProbeRun) -> OptionalRuntimeReport:
    if not installed:
        return OptionalRuntimeReport(False, (), "未導入")
    values = (
        tuple(str(item) for item in run.result.get("values", ())) if run.result is not None else ()
    )
    return OptionalRuntimeReport(
        run.outcome.status == "completed",
        values,
        run.outcome.detail,
        run.outcome.status,
    )


def _vulkan_report(run: _ProbeRun) -> VulkanReport:
    if run.result is None:
        detail = run.outcome.detail or "判定不能"
        return VulkanReport(False, detail, False, None, detail, run.outcome.status)
    return VulkanReport(
        bool(run.result.get("build_available")),
        None if run.result.get("build_error") is None else str(run.result["build_error"]),
        bool(run.result.get("runtime_available")),
        None if run.result.get("runtime_device") is None else str(run.result["runtime_device"]),
        None if run.result.get("runtime_error") is None else str(run.result["runtime_error"]),
        run.outcome.status,
    )


def _parse_nvidia_metadata(stdout: str) -> dict[int, tuple[str, int | None]]:
    metadata: dict[int, tuple[str, int | None]] = {}
    for line in stdout.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) < 3:
            continue
        try:
            index = int(fields[0])
            memory = int(fields[2]) * 1024 * 1024
        except ValueError:
            continue
        metadata[index] = (fields[1], memory)
    return metadata


def _save_probe_cache(path: Path, fingerprint: str, reports: _IsolatedReports) -> None:
    payload = {
        "schema_version": _PROBE_CACHE_SCHEMA,
        "fingerprint": fingerprint,
        "reports": {
            "ctranslate2": asdict(reports.ctranslate2),
            "torch": asdict(reports.torch),
            "openvino": asdict(reports.openvino),
            "onnxruntime": asdict(reports.onnxruntime),
            "vulkan": asdict(reports.vulkan),
        },
        "outcomes": [asdict(item) for item in reports.outcomes],
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        temporary.replace(path)
    except OSError as exc:
        _LOGGER.warning("Could not save device probe cache: %s", _bounded_error(exc))
        temporary.unlink(missing_ok=True)


def _load_probe_cache(path: Path, fingerprint: str) -> _IsolatedReports | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != _PROBE_CACHE_SCHEMA
            or payload.get("fingerprint") != fingerprint
        ):
            return None
        reports = payload["reports"]
        outcomes = payload["outcomes"]
        if not isinstance(reports, dict) or not isinstance(outcomes, list):
            return None
        return _IsolatedReports(
            _cached_ctranslate2(cast(dict[str, Any], reports["ctranslate2"])),
            _cached_torch(cast(dict[str, Any], reports["torch"])),
            _cached_optional(cast(dict[str, Any], reports["openvino"])),
            _cached_optional(cast(dict[str, Any], reports["onnxruntime"])),
            _cached_vulkan(cast(dict[str, Any], reports["vulkan"])),
            tuple(_cached_outcome(cast(dict[str, Any], item)) for item in outcomes),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _probe_state(value: object) -> ProbeState:
    if value not in {"completed", "timeout", "error"}:
        raise ValueError(f"invalid probe state: {value}")
    return cast(ProbeState, value)


def _cached_accelerator(raw: dict[str, Any]) -> AcceleratorDevice:
    return AcceleratorDevice(
        int(raw["index"]),
        str(raw["name"]),
        None if raw.get("memory_bytes") is None else int(raw["memory_bytes"]),
        tuple(str(item) for item in raw.get("compute_types", ())),
        bool(raw.get("usable", False)),
        None if raw.get("error") is None else str(raw["error"]),
    )


def _cached_ctranslate2(raw: dict[str, Any]) -> CTranslate2Report:
    return CTranslate2Report(
        bool(raw["available"]),
        None if raw.get("version") is None else str(raw["version"]),
        tuple(str(item) for item in raw.get("cpu_compute_types", ())),
        int(raw.get("cuda_device_count", 0)),
        tuple(_cached_accelerator(item) for item in raw.get("cuda_devices", ())),
        None if raw.get("error") is None else str(raw["error"]),
        _probe_state(raw.get("cpu_status", "completed")),
        _probe_state(raw.get("cuda_status", "completed")),
    )


def _cached_torch(raw: dict[str, Any]) -> TorchReport:
    return TorchReport(
        bool(raw["available"]),
        None if raw.get("version") is None else str(raw["version"]),
        bool(raw.get("cuda_available", False)),
        tuple(_cached_accelerator(item) for item in raw.get("cuda_devices", ())),
        None if raw.get("error") is None else str(raw["error"]),
        bool(raw.get("xpu_available", False)),
        tuple(_cached_accelerator(item) for item in raw.get("xpu_devices", ())),
        _probe_state(raw.get("cuda_status", "completed")),
        _probe_state(raw.get("xpu_status", "completed")),
    )


def _cached_optional(raw: dict[str, Any]) -> OptionalRuntimeReport:
    return OptionalRuntimeReport(
        bool(raw["available"]),
        tuple(str(item) for item in raw.get("values", ())),
        None if raw.get("error") is None else str(raw["error"]),
        _probe_state(raw.get("status", "completed")),
    )


def _cached_vulkan(raw: dict[str, Any]) -> VulkanReport:
    return VulkanReport(
        bool(raw["build_available"]),
        None if raw.get("build_error") is None else str(raw["build_error"]),
        bool(raw["runtime_available"]),
        None if raw.get("runtime_device") is None else str(raw["runtime_device"]),
        None if raw.get("runtime_error") is None else str(raw["runtime_error"]),
        _probe_state(raw.get("status", "completed")),
    )


def _cached_outcome(raw: dict[str, Any]) -> ProbeOutcome:
    return ProbeOutcome(
        str(raw["name"]),
        str(raw["label"]),
        _probe_state(raw["status"]),
        float(raw.get("duration_seconds", 0.0)),
        None if raw.get("detail") is None else str(raw["detail"]),
        bool(raw.get("cached", False)),
    )


def _driver_identity() -> list[str]:
    """Return non-personal driver identity fields for cache invalidation."""
    if sys.platform == "win32":
        return _windows_driver_identity()
    identities: list[str] = []
    for device in sorted(Path("/sys/class/drm").glob("card*/device")):
        fields: list[str] = []
        for name in ("vendor", "device", "subsystem_vendor", "subsystem_device", "revision"):
            try:
                fields.append(f"{name}={device.joinpath(name).read_text().strip()}")
            except OSError:
                continue
        with suppress(OSError):
            fields.append(f"driver={device.joinpath('driver').resolve().name}")
        if fields:
            identities.append("|".join(fields))
    return identities


def _windows_driver_identity() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    identities: list[str] = []
    root_path = r"SYSTEM\CurrentControlSet\Control\Video"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root_path) as root:
            for adapter_index in range(winreg.QueryInfoKey(root)[0]):
                adapter_name = winreg.EnumKey(root, adapter_index)
                try:
                    with winreg.OpenKey(root, adapter_name) as adapter:
                        for child_index in range(winreg.QueryInfoKey(adapter)[0]):
                            child_name = winreg.EnumKey(adapter, child_index)
                            try:
                                with winreg.OpenKey(adapter, child_name) as child:
                                    values: list[str] = []
                                    for field in (
                                        "DriverVersion",
                                        "DriverDate",
                                        "MatchingDeviceId",
                                        "ProviderName",
                                    ):
                                        try:
                                            value, _kind = winreg.QueryValueEx(child, field)
                                            values.append(f"{field}={value}")
                                        except OSError:
                                            continue
                                    if values:
                                        identities.append("|".join(values))
                            except OSError:
                                continue
                except OSError:
                    continue
    except OSError:
        return []
    return sorted(set(identities))


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


def detect_ctranslate2(
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> CTranslate2Report:
    """Probe CTranslate2 only through killable child processes."""
    installed = importlib.util.find_spec("ctranslate2") is not None
    if not installed:
        return CTranslate2Report(False, None, (), 0, (), "未導入")
    cpu = run_isolated_probe("ctranslate2_cpu", "CTranslate2 CPU", timeout_seconds)
    count_run = run_isolated_probe(
        "ctranslate2_cuda_count", "CTranslate2 CUDA device count", timeout_seconds
    )
    count = int(count_run.result.get("count", 0)) if count_run.result is not None else 0
    metadata: dict[int, tuple[str, int | None]] = {}
    cuda_runs: list[_ProbeRun] = []
    if count:
        metadata_run = run_isolated_probe(
            "nvidia_metadata", "NVIDIA driver metadata", timeout_seconds
        )
        if metadata_run.result is not None:
            metadata = _parse_nvidia_metadata(str(metadata_run.result.get("stdout", "")))
        cuda_runs = [
            run_isolated_probe(
                "ctranslate2_cuda",
                f"CTranslate2 cuda:{index} compute types",
                timeout_seconds,
                argument=str(index),
            )
            for index in range(count)
        ]
    return _combine_ctranslate2(installed, cpu, count_run, cuda_runs, metadata)


def detect_torch(timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS) -> TorchReport:
    """Probe PyTorch CUDA and XPU in separate killable child processes."""
    installed = importlib.util.find_spec("torch") is not None
    if not installed:
        return TorchReport(False, None, False, (), "未導入")
    cuda = run_isolated_probe("torch_cuda", "PyTorch CUDA", timeout_seconds)
    xpu = run_isolated_probe("torch_xpu", "PyTorch XPU", timeout_seconds)
    return _combine_torch(installed, cuda, xpu)


def detect_cuda_libraries() -> LibraryReport:
    """Resolve cuDNN and cuBLAS by loader name or a PATH directory."""
    return LibraryReport(
        cudnn=_find_shared_library(("cudnn", "libcudnn"), ("cudnn*.dll", "libcudnn.so*")),
        cublas=_find_shared_library(
            ("cublas", "libcublas"),
            ("cublas*.dll", "libcublas.so*"),
        ),
    )


def detect_openvino(
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> OptionalRuntimeReport:
    """Report OpenVINO devices through a killable child process."""
    installed = importlib.util.find_spec("openvino") is not None
    if not installed:
        return OptionalRuntimeReport(False, (), "未導入")
    return _optional_report(
        installed, run_isolated_probe("openvino", "OpenVINO devices", timeout_seconds)
    )


def detect_onnxruntime(
    timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
) -> OptionalRuntimeReport:
    """Report ONNX Runtime providers through a killable child process."""
    installed = importlib.util.find_spec("onnxruntime") is not None
    if not installed:
        return OptionalRuntimeReport(False, (), "未導入")
    return _optional_report(
        installed,
        run_isolated_probe("onnxruntime", "ONNX Runtime providers", timeout_seconds),
    )


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
    from utteran.asr.whisper_cpp import WhisperCppBackend

    return {
        "faster-whisper": _module_available("faster_whisper"),
        "whisper-cpp": WhisperCppBackend.is_available(),
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


def detect_vulkan(timeout_seconds: float = DEFAULT_PROBE_TIMEOUT_SECONDS) -> VulkanReport:
    """Report Vulkan prerequisites through a killable child process."""
    return _vulkan_report(run_isolated_probe("vulkan", "Vulkan build/runtime", timeout_seconds))


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
    vulkan: VulkanReport,
    native: NativeReport,
) -> tuple[AutoSelection, list[str]]:
    """Apply the documented CUDA, Intel/Vulkan, then CPU ASR priority."""
    notes: list[str] = []
    if ctranslate2.cuda_status != "completed":
        notes.append("CTranslate2 CUDAの判定ができませんでした。CUDAを利用可能とは扱いません。")
    if torch.cuda_status != "completed":
        notes.append("PyTorch CUDAの判定ができませんでした。")
    if torch.xpu_status != "completed":
        notes.append("PyTorch XPUの判定ができませんでした。話者分離はCPU実行になります。")
    if openvino.status != "completed":
        notes.append("OpenVINOの判定ができませんでした。")
    if vulkan.status != "completed":
        notes.append("Vulkanの判定ができませんでした。")
    cuda_usable = any(device.usable for device in ctranslate2.cuda_devices)
    openvino_gpu = openvino.available and any(
        item.upper().startswith("GPU") for item in openvino.values
    )
    if cuda_usable:
        try:
            asr = select_faster_whisper_device("auto", "auto", report=ctranslate2)
            if asr.note:
                notes.append(asr.note)
            asr_device = f"cuda:{asr.device_index}"
            asr_compute = asr.compute_type
            asr_backend = "faster-whisper"
        except BackendUnavailableError as exc:
            notes.append(str(exc))
            asr_backend, asr_device, asr_compute = "faster-whisper", "cpu", "int8"
    elif vulkan.runtime_available and native.variants.get("vulkan", False):
        asr_backend, asr_device, asr_compute = "whisper-cpp", "vulkan", "ggml"
        notes.append("実機中央値と追加IR不要という運用コストからvulkanを選択しました。")
    elif openvino_gpu and native.variants.get("openvino_vulkan", False):
        asr_backend, asr_device, asr_compute = "whisper-cpp", "openvino_vulkan", "ggml"
        notes.append("Vulkan単独が利用できないためopenvino_vulkanを選択しました。")
    elif openvino_gpu and native.variants.get("openvino", False):
        asr_backend, asr_device, asr_compute = "whisper-cpp", "openvino", "ggml"
        notes.append("OpenVINO GPUが利用可能なためopenvinoを選択しました。")
    else:
        asr_backend, asr_device, asr_compute = "faster-whisper", "cpu", "int8"
        notes.append("GPU向け構成を利用できないためfaster-whisper CPUを選択しました。")
    torch_cuda = next((device for device in torch.cuda_devices if device.usable), None)
    torch_xpu = next((device for device in torch.xpu_devices if device.usable), None)
    if torch_cuda is not None:
        diarization_device = f"cuda:{torch_cuda.index}"
        notes.append("PyTorch CUDAが利用可能なため話者分離にcudaを選択しました。")
    elif torch_xpu is not None:
        diarization_device = f"xpu:{torch_xpu.index}"
        notes.append("PyTorch XPUが利用可能なため話者分離にxpuを選択しました。")
    else:
        diarization_device = "cpu"
        notes.append("CUDA/XPUを利用できないため話者分離にCPUを選択しました。")
    if openvino_gpu and torch_xpu is None:
        notes.append("ASRはIntel GPUで高速化できますが、話者分離はCPUで実行されます。")
    intel_accelerators = tuple(
        item for item in openvino.values if item.upper().startswith(("GPU", "NPU"))
    )
    warnings: list[str] = []
    if asr_device == "cpu" and intel_accelerators:
        warnings.append(
            "Intel GPU / NPU は検出されましたが実行可能なwhisper.cpp構成がありません。"
            "`utteran native build`を確認してください。"
        )
    return (
        AutoSelection(
            asr_backend=asr_backend,
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


@contextmanager
def suppress_torch_import() -> Iterator[bool]:
    """Shadow ``torch`` in ``sys.modules`` while CTranslate2 is imported.

    ``ctranslate2.specs.model_spec`` does an unconditional, module-level
    ``try: import torch`` purely to support its (unused by utteran) PyTorch
    checkpoint conversion helpers. On this project's Intel profile, the real
    ``torch`` package ships an 800+ MiB ``torch_xpu.dll`` whose native
    initialization (``torch/__init__.py::_load_dll_libraries``) can spend
    many minutes of real CPU time on machines with this Intel iGPU/driver
    combination (measured: 1000+ CPU-seconds without finishing) -- this is
    the actual cause of the "faster-whisper CPU inference never finishes"
    symptom, not an explicit CUDA/XPU device query. Runtime inference never
    touches ``model_spec``'s torch-only conversion paths, so a lightweight
    stand-in is safe here and is removed immediately afterward so a later,
    genuine ``import torch`` (e.g. for diarization) is unaffected.
    """
    already_imported = "torch" in sys.modules
    if not already_imported:
        sys.modules["torch"] = ModuleType("torch")
    try:
        yield not already_imported
    finally:
        if not already_imported:
            sys.modules.pop("torch", None)


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


def _bounded_error(error: Exception | str) -> str:
    """Return a short diagnostic without backend traceback data."""
    return str(error).replace("\r", " ").replace("\n", " ")[:500]


def _module_available(name: str) -> bool:
    """Check optional dotted modules without propagating a missing parent import."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False
