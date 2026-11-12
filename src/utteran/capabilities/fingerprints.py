"""Metadata-only runtime/hardware identities for B4 smoke evidence (0.1.50).

These values bind a smoke result to the runtime and host it ran on. They are computed
identically by the explicit smoke child and by every later metadata-only refresh, in the
same profile interpreter and with the same bridge environment, so an upgrade or a
different host yields a different evidence scope.

Deliberately limited (see docs/architecture/b4/smoke.md §1.2):

- Only ``cpu``. CTranslate2 exposes neither the CUDA driver/runtime version nor a GPU
  identity, and CUDA ordinals are API-specific, so CUDA stays ``None`` (unknown).
- No inference import, no subprocess (``platform.processor()`` runs ``uname -p`` on
  POSIX), no driver probe, no network, no writes.
"""

from __future__ import annotations

import importlib.metadata
import os
import platform
import sys
from pathlib import Path

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
    if device != "cpu":
        return None
    versions = package_versions()
    if any(versions[name] is None for name in _REQUIRED):
        return None
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


def hardware_fingerprint(device: str) -> str | None:
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
