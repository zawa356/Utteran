"""Metadata-only runtime/hardware identities for B4 smoke evidence (0.1.50).

These values bind a smoke result to the runtime and host it ran on. They are computed
identically by the explicit smoke child and by every later metadata-only refresh, in the
same profile interpreter and with the same bridge environment, so an upgrade or a
different host yields a different evidence scope.

Deliberately limited (see docs/architecture/b4/smoke.md §1.2):

- ``cpu``: package metadata + interpreter/OS, and the CPU model.
- ``cuda`` (F-B, 0.1.71): CTranslate2 exposes neither the driver version nor a GPU
  identity, so both come from the driver's NVML library (read-only ctypes, no CUDA
  context): driver + CUDA driver API version, the CUDA wheel versions, and the *set* of
  GPUs by UUID/name/total memory, plus ``CUDA_VISIBLE_DEVICES``/``CUDA_DEVICE_ORDER``.
  The ordinal -> UUID link is attested by the smoke child itself (``nvml.cuda_ordinal_uuid``).
  Without NVML (or with no GPU) both stay ``None`` (unknown).
- No inference import, no subprocess (``platform.processor()`` runs ``uname -p`` on
  POSIX), no network, no writes.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
from pathlib import Path

from utteran.capabilities import nvml
from utteran.capabilities.evaluator import digest

# Packages whose code runs during a faster-whisper CPU transcription.
RUNTIME_PACKAGES = (
    "ctranslate2",
    "faster-whisper",
    "tokenizers",
    "av",
    "onnxruntime",
    "numpy",
    "huggingface-hub",
)
_REQUIRED = ("ctranslate2", "faster-whisper")
# CUDA user-space libraries CTranslate2 loads from the profile (pip wheels), when present.
CUDA_PACKAGES = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-runtime-cu12")
CUDA_ENVIRONMENT = ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER")


def _cuda_environment() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in CUDA_ENVIRONMENT}


def _cuda_runtime(versions: dict[str, str | None]) -> str | None:
    info = nvml.snapshot()
    if info is None or not info.gpus:
        return None
    extra: dict[str, str | None] = {}
    for name in CUDA_PACKAGES:
        try:
            extra[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            extra[name] = None
    return digest(
        {
            "kind": "faster-whisper-cuda-runtime",
            "version": 1,
            "python": platform.python_version(),
            "abi": sys.implementation.cache_tag,
            "os": (platform.system(), platform.release(), platform.machine()),
            "packages": versions,
            "cuda_packages": extra,
            "driver": info.driver_version,
            "cuda_driver_api": info.cuda_driver_version,
            "environment": _cuda_environment(),
        }
    )


_CPUINFO_LIMIT = 4 * 1024 * 1024


def package_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in RUNTIME_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def runtime_fingerprint(device: str) -> str | None:
    if device not in {"cpu", "cuda"}:
        return None
    versions = package_versions()
    if any(versions[name] is None for name in _REQUIRED):
        return None
    if device == "cuda":
        return _cuda_runtime(versions)
    return digest(
        {
            "kind": "faster-whisper-cpu-runtime",
            "version": 1,
            "python": platform.python_version(),
            "abi": sys.implementation.cache_tag,
            "os": (platform.system(), platform.release(), platform.machine()),
            "packages": versions,
        }
    )


def _linux_cpu_model() -> str | None:
    try:
        with Path("/proc/cpuinfo").open("rb") as source:
            text = source.read(_CPUINFO_LIMIT).decode("utf-8", "replace")
    except OSError:
        return None
    for key in ("model name", "Hardware", "Processor", "cpu model"):
        for line in text.splitlines():
            name, _, value = line.partition(":")
            if name.strip() == key and value.strip():
                return value.strip()
    return None


def _windows_cpu_model() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "ProcessorNameString")
    except OSError:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def cpu_model() -> str | None:
    if sys.platform == "win32":
        return _windows_cpu_model()
    if sys.platform.startswith("linux"):
        return _linux_cpu_model()
    return None


def single_cuda_gpu() -> nvml.NvmlGpu | None:
    """The one GPU CUDA ordinal 0 must be, or ``None`` when that is not provable.

    PR #91 P2-2: with several NVIDIA GPUs, or with ``CUDA_VISIBLE_DEVICES`` /
    ``CUDA_DEVICE_ORDER`` set, the CUDA ordinal of a later run cannot be tied to the GPU a
    past smoke ran on from metadata alone, so the evidence scope stays unknown (no ready).
    Only one NVIDIA GPU with neither variable set is accepted: ordinal 0 is that GPU.
    """
    # Present at all (even empty: CUDA_VISIBLE_DEVICES="" hides every GPU) -> not provable.
    if any(name in os.environ for name in CUDA_ENVIRONMENT):
        return None
    info = nvml.snapshot()
    if info is None or len(info.gpus) != 1:
        return None
    return info.gpus[0]


def cuda_hardware_fingerprint() -> str | None:
    gpu = single_cuda_gpu()
    if gpu is None:
        return None
    return digest(
        {
            "kind": "cuda-host",
            "version": 2,
            "machine": platform.machine(),
            # Bound to the one GPU by UUID: a replaced or added GPU is a different scope.
            "gpu": [gpu.uuid, gpu.name, gpu.memory_total],
            "ordinal": 0,
        }
    )


def hardware_fingerprint(device: str) -> str | None:
    if device == "cuda":
        return cuda_hardware_fingerprint()
    if device != "cpu":
        return None
    model = cpu_model()
    count = os.cpu_count()
    if model is None or count is None:
        return None
    return digest(
        {
            "kind": "cpu-host",
            "version": 1,
            "machine": platform.machine(),
            "cpu": model,
            "logical_cpus": count,
        }
    )


def current_fingerprints(device: str) -> tuple[str | None, str | None]:
    return runtime_fingerprint(device), hardware_fingerprint(device)
