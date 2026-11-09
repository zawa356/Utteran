"""Synthetic profile CLI for the UI E2E harness and the frontend parser fixture (UI-3 S2).

The *real* ``EnvironmentService`` (active-profile rule, ``derive_options``,
``annotate_model_capabilities``, error masking) runs on top of ``SyntheticCli``, which answers the
same CLI JSON contracts a profile venv would. No real profile, device, model, path or process is
touched: profile "paths" are the fixed relative placeholder ``synthetic-root/...`` and are never
opened.

Profiles: ``cpu`` (CPU only, no installed model), ``cuda`` (one synthetic CUDA GPU, faster-whisper
and pyannote models), ``intel`` (XPU, OpenVINO, whisper.cpp native build, needs a rebuild, and a
failing ``models path`` call -> partial result), ``vulkan`` (not built). The fixture also has
``cuda_models_list_failed``: the same cuda profile where only ``models list`` fails.

    uv run python tools/ui2/synthetic_environment.py --write   # regenerate the frontend fixture
    uv run python tools/ui2/synthetic_environment.py --check   # fail if it is out of date

Nothing here is imported by the product.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from utteran_gui.cli import CliError, ProfileInfo  # noqa: E402
from utteran_gui.environment import EnvironmentService  # noqa: E402

FIXTURE = ROOT / "frontend" / "src" / "test" / "fixtures" / "environment-snapshots.json"
# POSIX-style on every OS so the generated fixture is identical on Linux and Windows.
PLACEHOLDER_ROOT = PurePosixPath("synthetic-root")
PROFILE_ORDER = ("cpu", "cuda", "intel", "vulkan")
# Seconds the synthetic `devices --json` takes per profile, so ordering races are deterministic.
DEVICE_DELAYS = {"cuda": 1.5, "intel": 0.2}
REFRESH_DELAY = 0.5
# A long, HTML-looking device name: the UI must show it as text and wrap it.
LONG_XPU_NAME = (
    "Synthetic Intel(R) Arc(TM) Graphics <b>XPU</b> with a deliberately very long device name 0"
)

_EXISTS = {"cpu": True, "cuda": True, "intel": True, "vulkan": False}
_COMPATIBLE: dict[str, tuple[bool | None, str | None]] = {
    "cpu": (True, None),
    "cuda": (None, "current_lock_missing"),
    "intel": (False, "dependency_lock_changed"),
    "vulkan": (None, None),
}


def _gpu(index: int, name: str, usable: bool = True) -> dict[str, Any]:
    return {
        "index": index,
        "name": name,
        "memory_bytes": None,
        "compute_types": [],
        "usable": usable,
        "error": None,
    }


def _devices(profile: str) -> dict[str, Any]:
    cuda = profile == "cuda"
    intel = profile == "intel"
    return {
        "cpu": {"logical_cores": 8, "physical_cores": 4, "avx2": True, "avx512": False},
        "ctranslate2": {
            "available": True,
            "version": "0.0.0-synthetic",
            "cpu_compute_types": ["int8"],
            "cuda_device_count": 1 if cuda else 0,
            "cuda_devices": [_gpu(0, "Synthetic CUDA GPU 0")] if cuda else [],
            "error": None,
            "cpu_status": "completed",
            "cuda_status": "completed",
        },
        "cuda_libraries": {"cudnn": None, "cublas": None},
        "pytorch": {
            "available": True,
            "version": "0.0.0-synthetic",
            "cuda_available": cuda,
            "cuda_devices": [_gpu(0, "Synthetic CUDA GPU 0")] if cuda else [],
            "error": None,
            "xpu_available": intel,
            "xpu_devices": [_gpu(0, LONG_XPU_NAME)] if intel else [],
            "cuda_status": "completed",
            "xpu_status": "completed",
        },
        "openvino": {
            "available": intel,
            "values": ["CPU", "GPU.0", "NPU"] if intel else [],
            "error": None,
            "status": "completed",
        },
        "onnxruntime": {"available": False, "values": [], "error": None, "status": "completed"},
        "ffmpeg": {"available": True, "path": None, "version": None, "error": None},
        "backends": {
            "faster-whisper": True,
            "whisper-cpp": True,
            "openvino-genai": False,
            "pyannote": True,
        },
        "auto_selection": {
            "asr_backend": "whisper-cpp" if intel else "faster-whisper",
            "asr_device": "openvino" if intel else ("cuda:0" if cuda else "cpu"),
            "asr_compute_type": "int8",
            "diarization_backend": "pyannote",
            "diarization_device": "cuda:0" if cuda else "cpu",
            "notes": [],
        },
        "warnings": [],
        "profile": {"current": profile, "profiles": []},
        "vulkan": {
            "build_available": False,
            "build_error": None,
            "runtime_available": False,
            "runtime_device": None,
            "runtime_error": None,
            "status": "timeout" if intel else "completed",
        },
        "native": {
            "built": intel,
            "whisper_cpp_tag": "v0.0.0-synthetic" if intel else None,
            "variants": {
                "cpu": intel,
                "openvino": intel,
                "vulkan": False,
                "openvino_vulkan": False,
            },
        },
        "probes": [
            {
                "name": "ctranslate2",
                "label": "CTranslate2",
                "status": "completed",
                "duration_seconds": 0.1,
                "detail": None,
                "cached": True,
            },
            {
                "name": "vulkan",
                "label": "Vulkan",
                "status": "timeout" if intel else "completed",
                "duration_seconds": 20.0 if intel else 0.1,
                "detail": None,
                "cached": False,
            },
        ],
        "probe_cache_hit": False,
    }


def _model(key: str, model_id: str, backend: str, installed: bool) -> dict[str, Any]:
    return {
        "key": key,
        "model_id": model_id,
        "display_name": f"Synthetic {model_id}",
        "backend": backend,
        "format": "synthetic",
        "description": "synthetic catalog entry",
        "approximate_size_bytes": 1,
        "size_bytes": 1 if installed else None,
        "gated": backend == "pyannote",
        "recommended": False,
        "english_only": False,
        "model_size": None,
        "quantization": None,
        "installed": installed,
        "path": None,
    }


def _models(profile: str) -> list[dict[str, Any]]:
    cuda = profile == "cuda"
    intel = profile == "intel"
    return [
        _model("fw-large-v3-turbo", "large-v3-turbo", "faster-whisper", cuda),
        _model("fw-small", "small", "faster-whisper", cuda),
        _model("ggml-large-v3-turbo-q5_0", "large-v3-turbo-q5_0", "whisper-cpp", intel),
        _model("pyannote-3.1", "speaker-diarization-3.1", "pyannote", cuda),
    ]


class SyntheticCli:
    """Answers ``CliAdapter``'s JSON/text contracts with synthetic data (no subprocess)."""

    def __init__(self, *, delays: bool = True, fail_models_list: bool = False) -> None:
        self.delays = delays
        # Only `models list` fails: the real service then returns `models: []` (its initial
        # value) plus one masked error string — indistinguishable from "zero installed" by shape.
        self.fail_models_list = fail_models_list

    def profile_info(self, profile: str) -> ProfileInfo:
        compatible, reason = _COMPATIBLE[profile]
        base = PLACEHOLDER_ROOT / "profiles" / profile
        return ProfileInfo(
            name=profile,
            path=base,  # type: ignore[arg-type]
            exists=_EXISTS[profile],
            executable=base / "python",  # type: ignore[arg-type]
            updated_at=None,
            compatible=compatible,
            compatibility_reason=reason,
        )

    def profiles(self) -> tuple[ProfileInfo, ...]:
        return tuple(self.profile_info(name) for name in PROFILE_ORDER)

    def run_json(self, profile: str, arguments: list[str], *, timeout: float = 60.0) -> object:
        command = " ".join(arguments)
        if command == "profiles list --json":
            return {
                "current": profile,
                "profiles": [
                    {"name": name, "exists": _EXISTS[name], "updated_at": None}
                    for name in PROFILE_ORDER
                ],
            }
        if arguments[:1] == ["devices"]:
            if self.delays:
                refresh = REFRESH_DELAY if "--refresh" in arguments else 0.0
                time.sleep(DEVICE_DELAYS.get(profile, 0.05) + refresh)
            return _devices(profile)
        if command == "models list --available --all --json":
            if self.fail_models_list:
                raise CliError("CLI exited 1: synthetic model catalog could not be read")
            return _models(profile)
        if command == "native status --json":
            runnable = {"cpu": True, "openvino": True} if profile == "intel" else {}
            # `manifest` mirrors the real shape; the frontend must drop it (it can hold paths).
            return {"manifest": {"root": str(PLACEHOLDER_ROOT / "native")}, "runnable": runnable}
        if command == "models list-openvino --json":
            if profile != "intel":
                return []
            xml = str(PLACEHOLDER_ROOT / "models" / "openvino" / "encoder.xml")
            return [
                {"model_size": "large-v3-turbo", "installed": True, "xml_path": xml},
                {"model_size": "small", "installed": False, "xml_path": xml},
            ]
        raise CliError(f"CLI exited 2: unknown synthetic command {command}")

    def run_text(self, profile: str, arguments: list[str], *, timeout: float = 60.0) -> str:
        if arguments == ["models", "path"]:
            if profile == "intel":
                # A failure whose text carries a path: the UI must show a fixed message instead.
                where = PLACEHOLDER_ROOT / "profiles" / "intel" / "models"
                raise CliError(f"CLI exited 1: cannot read {where}")
            return str(PLACEHOLDER_ROOT / "models")
        raise CliError("CLI exited 2: unknown synthetic text command")


def synthetic_environment_service(*, delays: bool = True) -> EnvironmentService:
    return EnvironmentService(SyntheticCli(delays=delays))  # type: ignore[arg-type]


def snapshots() -> dict[str, object]:
    """Real ``snapshot()`` output for each request the frontend fixture covers."""
    service = synthetic_environment_service(delays=False)
    requests: dict[str, str | None] = {
        "no_profile_parameter": None,
        "cpu": "cpu",
        "cuda": "cuda",
        "intel": "intel",
        "vulkan_not_built": "vulkan",
    }
    result: dict[str, object] = {
        key: service.snapshot(profile) for key, profile in requests.items()
    }
    failing = EnvironmentService(SyntheticCli(delays=False, fail_models_list=True))  # type: ignore[arg-type]
    result["cuda_models_list_failed"] = failing.snapshot("cuda")
    return result


def render() -> str:
    return json.dumps(snapshots(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.write:
        FIXTURE.write_text(text, encoding="utf-8", newline="\n")
        return 0
    current = FIXTURE.read_text(encoding="utf-8") if FIXTURE.exists() else ""
    if current != text:
        print(f"{FIXTURE.relative_to(ROOT)} is out of date; run with --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
