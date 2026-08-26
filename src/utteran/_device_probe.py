"""One-shot native runtime probes executed by :mod:`utteran.devices`.

This module is intentionally a tiny JSON-in/JSON-out process boundary.  Native
drivers can block below Python's exception and signal handling, so callers must
never import and execute these probes in their own process.
"""

from __future__ import annotations

import json
import subprocess
import sys
import warnings
from typing import Any


def _error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ")[:500]


def _prepare_cuda_loader() -> None:
    from utteran.devices import register_cuda_dll_directories

    register_cuda_dll_directories()


def _ctranslate2_cpu() -> dict[str, object]:
    _prepare_cuda_loader()
    import ctranslate2

    return {
        "version": str(getattr(ctranslate2, "__version__", "unknown")),
        "compute_types": sorted(ctranslate2.get_supported_compute_types("cpu")),
    }


def _ctranslate2_cuda_count() -> dict[str, object]:
    _prepare_cuda_loader()
    import ctranslate2

    return {
        "version": str(getattr(ctranslate2, "__version__", "unknown")),
        "count": int(ctranslate2.get_cuda_device_count()),
    }


def _ctranslate2_cuda(index: int) -> dict[str, object]:
    _prepare_cuda_loader()
    import ctranslate2

    return {
        "compute_types": sorted(ctranslate2.get_supported_compute_types("cuda", index))
    }


def _torch(kind: str) -> dict[str, object]:
    _prepare_cuda_loader()
    import torch

    runtime = getattr(torch, kind, None)
    devices: list[dict[str, object]] = []
    if runtime is not None and runtime.is_available():
        for index in range(runtime.device_count()):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    properties = runtime.get_device_properties(index)
                    probe = torch.ones(1, device=f"{kind}:{index}")
                    result = (probe + 1).cpu()
                    runtime.synchronize(index)
                if float(result.item()) != 2.0:
                    raise RuntimeError(f"{kind.upper()} probe returned an unexpected result")
                memory = getattr(properties, "total_memory", None)
                name = getattr(properties, "name", None) or runtime.get_device_name(index)
                devices.append(
                    {
                        "index": index,
                        "name": str(name),
                        "memory_bytes": None if memory is None else int(memory),
                        "usable": True,
                    }
                )
            except Exception as exc:
                devices.append(
                    {
                        "index": index,
                        "name": f"{kind.upper()} {index}",
                        "memory_bytes": None,
                        "usable": False,
                        "error": _error(exc),
                    }
                )
    return {
        "version": str(getattr(torch, "__version__", "unknown")),
        "devices": devices,
    }


def _openvino() -> dict[str, object]:
    from openvino import Core

    return {"values": [str(item) for item in Core().available_devices]}


def _onnxruntime() -> dict[str, object]:
    import onnxruntime

    return {"values": [str(item) for item in onnxruntime.get_available_providers()]}


def _vulkan() -> dict[str, object]:
    from utteran.native import probe_glslc, probe_vulkan_runtime

    build = probe_glslc()
    runtime, device = probe_vulkan_runtime()
    return {
        "build_available": build.available,
        "build_error": None if build.available else build.detail,
        "runtime_available": runtime.available,
        "runtime_device": device,
        "runtime_error": None if runtime.available else runtime.detail,
    }


def _nvidia_metadata() -> dict[str, object]:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return {"returncode": completed.returncode, "stdout": completed.stdout[:10000]}


def _dispatch(name: str, argument: str | None) -> dict[str, object]:
    if name == "ctranslate2_cpu":
        return _ctranslate2_cpu()
    if name == "ctranslate2_cuda_count":
        return _ctranslate2_cuda_count()
    if name == "ctranslate2_cuda":
        if argument is None:
            raise ValueError("CUDA device index is required")
        return _ctranslate2_cuda(int(argument))
    if name == "torch_cuda":
        return _torch("cuda")
    if name == "torch_xpu":
        return _torch("xpu")
    if name == "openvino":
        return _openvino()
    if name == "onnxruntime":
        return _onnxruntime()
    if name == "vulkan":
        return _vulkan()
    if name == "nvidia_metadata":
        return _nvidia_metadata()
    raise ValueError(f"unknown probe: {name}")


def main(argv: list[str] | None = None) -> int:
    selected = list(sys.argv[1:] if argv is None else argv)
    if not selected:
        return 2
    try:
        result: dict[str, Any] = _dispatch(
            selected[0], selected[1] if len(selected) > 1 else None
        )
        payload: dict[str, Any] = {"ok": True, "result": result}
    except BaseException as exc:
        payload = {"ok": False, "error": _error(exc)}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
