"""Environment aggregation and dynamic option generation for the web UI."""

from __future__ import annotations

import subprocess
import time
from typing import Any, cast

from utteran_gui.cli import CliAdapter, CliError, as_json_dict
from utteran_gui.logging_runtime import log_stage
from utteran_gui.security import mask_secrets

# `utteran devices --json` runs up to 7 isolated native probes sequentially
# (CTranslate2 CPU/CUDA, PyTorch CUDA/XPU, OpenVINO, ONNX Runtime, Vulkan),
# each with its own 20-second timeout (`DEFAULT_PROBE_TIMEOUT_SECONDS` in
# `utteran.devices`) - a worst case of ~140s of pure timeouts plus per-probe
# process/kill overhead. Measured directly on this project's own Phase 5k/5l
# reference machine (Core i7-1165G7 / Iris Xe / no NVIDIA GPU) with an
# uncached probe cache: 94.7s, with 4 of 7 probes timing out. `run_json`'s
# generic 60s default is comfortably below that, which is what let an
# uncached first launch appear to freeze - see AISTATE.md Phase 5l. A cached
# run completes in ~5s (measured on the same machine), so this long ceiling
# only matters on the very first launch or after a hardware/driver change.
_DEVICES_PROBE_TIMEOUT_SECONDS = 200.0

# `CliError` only covers a non-zero exit or unparsable JSON. A profile CLI
# call can also fail at the subprocess layer itself (killed by its own
# timeout, or the OS refusing to spawn it) - `subprocess.run(timeout=...)`
# raises `subprocess.TimeoutExpired`/`OSError` directly, not `CliError`.
# Before Phase 5l, an uncaught timeout here propagated out of `/api/environment`
# as an unhandled exception; app.js's `boot()` awaits this call before doing
# anything else (loading the queue, checking first-run wizard state), so the
# rejection aborted the rest of startup and the window was left showing
# "detecting..." with no further recovery - see AISTATE.md Phase 5l.
_CLI_FAILURES = (CliError, subprocess.SubprocessError, OSError)


class EnvironmentService:
    """Read machine state exclusively through profile CLI JSON contracts."""

    def __init__(self, cli: CliAdapter) -> None:
        self.cli = cli

    def snapshot(
        self, requested_profile: str | None = None, *, refresh_devices: bool = False
    ) -> dict[str, object]:
        log_stage(
            "environment_snapshot_start",
            requested_profile=requested_profile,
            refresh_devices=refresh_devices,
        )
        started = time.monotonic()
        local_profiles = self.cli.profiles()
        errors: list[str] = []
        profile_rows = [
            {
                "name": profile.name,
                "path": str(profile.path),
                "exists": profile.exists,
                "updated_at": profile.updated_at,
                "compatible": profile.compatible,
                "compatibility_reason": profile.compatibility_reason,
            }
            for profile in local_profiles
        ]
        bootstrap = next((profile for profile in local_profiles if profile.exists), None)
        if bootstrap is not None:
            try:
                payload = as_json_dict(
                    self.cli.run_json(bootstrap.name, ["profiles", "list", "--json"])
                )
                reported = {
                    str(item.get("name")): item for item in _list_of_dicts(payload.get("profiles"))
                }
                for row in profile_rows:
                    item = reported.get(str(row["name"]))
                    if item is not None:
                        row["exists"] = bool(row["exists"] and item.get("exists") is True)
                        row["updated_at"] = item.get("updated_at")
            except _CLI_FAILURES as exc:
                errors.append(mask_secrets(str(exc)))
        existing = [str(row["name"]) for row in profile_rows if row["exists"] is True]
        active = requested_profile if requested_profile in existing else None
        if active is None and len(existing) == 1:
            active = existing[0]
        response: dict[str, object] = {
            "profiles": profile_rows,
            "active_profile": active,
            "devices": None,
            "models": [],
            "native": None,
            "options": _empty_options(),
            "errors": errors,
            "profile_warnings": [
                f"{row['name']} profileの依存環境が現在の版と一致しません。"
                "セットアップから環境を再構築してください。"
                for row in profile_rows
                if row["exists"] is True and row["compatible"] is False
            ],
        }
        if active is None:
            log_stage(
                "environment_snapshot_done",
                requested_profile=requested_profile,
                active_profile=None,
                duration_seconds=round(time.monotonic() - started, 3),
            )
            return response
        devices: dict[str, Any] = {}
        models: list[dict[str, Any]] = []
        native: dict[str, Any] = {}
        try:
            devices_started = time.monotonic()
            device_arguments = ["devices", "--json"]
            if refresh_devices:
                device_arguments.append("--refresh")
            devices = as_json_dict(
                self.cli.run_json(active, device_arguments, timeout=_DEVICES_PROBE_TIMEOUT_SECONDS)
            )
            log_stage(
                "environment_devices_probe_done",
                profile=active,
                duration_seconds=round(time.monotonic() - devices_started, 3),
            )
        except _CLI_FAILURES as exc:
            log_stage(
                "environment_devices_probe_failed",
                profile=active,
                duration_seconds=round(time.monotonic() - devices_started, 3),
                error=str(exc)[:500],
            )
            errors.append(mask_secrets(str(exc)))
        try:
            raw_models = self.cli.run_json(
                active, ["models", "list", "--available", "--all", "--json"]
            )
            if isinstance(raw_models, list):
                models = [
                    cast(dict[str, Any], item) for item in raw_models if isinstance(item, dict)
                ]
        except _CLI_FAILURES as exc:
            errors.append(mask_secrets(str(exc)))
        try:
            native = as_json_dict(self.cli.run_json(active, ["native", "status", "--json"]))
        except _CLI_FAILURES as exc:
            errors.append(mask_secrets(str(exc)))
        models = annotate_model_capabilities(models, devices, native)
        response.update(
            {
                "devices": devices or None,
                "models": models,
                "native": native or None,
                "options": derive_options(devices, models, native),
                "model_path": _read_model_path(self.cli, active, errors),
                "openvino_models": _read_openvino_models(self.cli, active, errors),
                "errors": errors,
            }
        )
        log_stage(
            "environment_snapshot_done",
            requested_profile=requested_profile,
            active_profile=active,
            duration_seconds=round(time.monotonic() - started, 3),
            error_count=len(errors),
        )
        return response


def annotate_model_capabilities(
    models: list[dict[str, Any]],
    devices: dict[str, Any],
    native: dict[str, Any],
) -> list[dict[str, Any]]:
    """Describe execution capability independently from model quantization."""
    ctranslate = _dict(devices.get("ctranslate2"))
    faster_gpu = any(
        item.get("usable") is True for item in _list_of_dicts(ctranslate.get("cuda_devices"))
    )
    runnable = _dict(native.get("runnable"))
    native_variants = _dict(_dict(devices.get("native")).get("variants"))
    whisper_gpu = any(
        runnable.get(name) is True and native_variants.get(name) is True
        for name in ("vulkan", "openvino", "openvino_vulkan")
    )
    torch = _dict(devices.get("pytorch"))
    diar_gpu = any(
        item.get("usable") is True
        for key in ("cuda_devices", "xpu_devices")
        for item in _list_of_dicts(torch.get(key))
    )
    annotated: list[dict[str, Any]] = []
    for source in models:
        model = dict(source)
        backend = str(model.get("backend", ""))
        gpu = (
            faster_gpu
            if backend == "faster-whisper"
            else whisper_gpu
            if backend == "whisper-cpp"
            else any(
                str(item).split(".", 1)[0].upper() == "GPU"
                for item in _dict(devices.get("openvino")).get("values", [])
            )
            if backend == "openvino-genai"
            else diar_gpu
            if backend == "pyannote"
            else False
        )
        model["gpu_execution"] = gpu
        model["execution_label"] = "GPU実行可" if gpu else "CPU実行"
        if backend == "whisper-cpp-vad":
            model["execution_label"] = "VAD補助モデル"
            model["recommendation_reason"] = "無音区間を除き、反復を抑えて処理を高速化します。"
        elif backend == "whisper-cpp":
            quantization = str(model.get("quantization") or "f16")
            model["recommendation_reason"] = (
                f"この環境ではGPUを利用できます。{quantization}は量子化方式で、"
                "GPU可否を決めるものではありません。"
                if gpu
                else "対応するGPUネイティブ構成がないため、この環境ではCPU実行です。"
            )
        elif backend == "faster-whisper":
            model["recommendation_reason"] = (
                "この環境のCUDA GPUで実行できます。"
                if gpu
                else "この環境ではCTranslate2のGPUが検出されないためCPU実行です。"
            )
        elif backend == "openvino-genai":
            model["recommendation_reason"] = (
                "ネイティブビルド不要でIntel CPU/GPU/NPUを利用できます。"
                "日本語など非スペース言語では話者分離と併用できません。"
            )
        elif backend == "pyannote":
            model["recommendation_reason"] = (
                "検出済みGPU/XPUで話者分離できます。"
                if gpu
                else "この環境では話者分離はCPU実行です。"
            )
        annotated.append(model)
    return annotated


def derive_options(
    devices: dict[str, Any],
    models: list[dict[str, Any]],
    native: dict[str, Any],
) -> dict[str, object]:
    """Expose only backend/model/device combinations proven available now."""
    backend_flags = _dict(devices.get("backends"))
    installed = [item for item in models if item.get("installed") is True]
    by_backend: dict[str, list[dict[str, str]]] = {}
    for model in installed:
        backend = str(model.get("backend", ""))
        if not backend:
            continue
        by_backend.setdefault(backend, []).append(
            {
                "id": str(model.get("model_id", "")),
                "key": str(model.get("key", "")),
                "label": str(model.get("display_name") or model.get("model_id") or ""),
            }
        )

    asr: list[dict[str, object]] = []
    ctranslate = _dict(devices.get("ctranslate2"))
    faster_devices: list[dict[str, str]] = []
    if ctranslate.get("available") is True:
        faster_devices.append({"id": "cpu", "label": "CPU"})
        for device in _list_of_dicts(ctranslate.get("cuda_devices")):
            if device.get("usable") is True:
                index = int(cast(int, device.get("index", 0)))
                faster_devices.append(
                    {"id": f"cuda:{index}", "label": str(device.get("name") or f"CUDA {index}")}
                )
    if (
        backend_flags.get("faster-whisper") is True
        and faster_devices
        and by_backend.get("faster-whisper")
    ):
        asr.append(
            {
                "id": "faster-whisper",
                "label": "faster-whisper",
                "models": by_backend["faster-whisper"],
                "devices": faster_devices,
            }
        )

    runnable = _dict(native.get("runnable"))
    native_report = _dict(devices.get("native"))
    variants = _dict(native_report.get("variants"))
    whisper_devices = [
        {"id": name, "label": name.replace("_", " + ")}
        for name in ("cpu", "openvino", "vulkan", "openvino_vulkan")
        if variants.get(name) is True and runnable.get(name, True) is True
    ]
    if (
        backend_flags.get("whisper-cpp") is True
        and whisper_devices
        and by_backend.get("whisper-cpp")
    ):
        asr.append(
            {
                "id": "whisper-cpp",
                "label": "whisper.cpp",
                "models": by_backend["whisper-cpp"],
                "devices": whisper_devices,
            }
        )

    openvino_values = {
        str(item).split(".", 1)[0].upper()
        for item in _dict(devices.get("openvino")).get("values", [])
    }
    genai_devices = []
    for identifier in ("CPU", "GPU", "NPU"):
        if identifier not in openvino_values:
            continue
        recommended = identifier != "NPU"
        genai_devices.append(
            {
                "id": identifier.casefold(),
                "label": f"OpenVINO {identifier}" + (" (非推奨)" if not recommended else ""),
                "recommended": recommended,
                "recommendation_reason": (
                    None
                    if recommended
                    else "初回ロード約306秒、キャッシュ約2.06 GiB。"
                    "キャッシュ後は約4.8秒ですが、現時点ではGPUの方が高速です。"
                ),
            }
        )
    if (
        backend_flags.get("openvino-genai") is True
        and genai_devices
        and by_backend.get("openvino-genai")
    ):
        asr.append(
            {
                "id": "openvino-genai",
                "label": "OpenVINO GenAI",
                "models": by_backend["openvino-genai"],
                "devices": genai_devices,
            }
        )

    diarization: list[dict[str, object]] = []
    torch = _dict(devices.get("pytorch"))
    diarization_devices: list[dict[str, str]] = []
    if torch.get("available") is True:
        diarization_devices.append({"id": "cpu", "label": "CPU"})
        for kind, key in (("cuda", "cuda_devices"), ("xpu", "xpu_devices")):
            for device in _list_of_dicts(torch.get(key)):
                if device.get("usable") is True:
                    index = int(cast(int, device.get("index", 0)))
                    diarization_devices.append(
                        {
                            "id": f"{kind}:{index}",
                            "label": str(device.get("name") or f"{kind.upper()} {index}"),
                        }
                    )
    if backend_flags.get("pyannote") is True and diarization_devices and by_backend.get("pyannote"):
        diarization.append(
            {
                "id": "pyannote",
                "label": "pyannote.audio",
                "models": by_backend["pyannote"],
                "devices": diarization_devices,
            }
        )
    auto = _dict(devices.get("auto_selection"))
    default_backend = str(auto.get("asr_backend", ""))
    default_device = str(auto.get("asr_device", ""))
    default_model = ""
    selected_asr = next((item for item in asr if item.get("id") == default_backend), None)
    if selected_asr is not None:
        available_models = cast(list[dict[str, str]], selected_asr["models"])
        preferred = "large-v3-turbo-q5_0" if default_backend == "whisper-cpp" else "large-v3-turbo"
        default_model = str(
            next(
                (item["id"] for item in available_models if item["id"] == preferred),
                available_models[0]["id"] if available_models else "",
            )
        )
    guidance: list[str] = []
    if default_backend == "whisper-cpp" and selected_asr is None:
        fast_variants = ("vulkan", "openvino", "openvino_vulkan")
        if not any(runnable.get(name) is True for name in fast_variants):
            guidance.append(
                "高速なwhisper.cpp構成が未ビルドです。`utteran native status`を確認し、"
                "`utteran native build`を実行してください。"
            )
        if not by_backend.get("whisper-cpp"):
            guidance.append("whisper.cpp用GGMLモデルがありません。モデル管理から取得してください。")
    elif default_backend == "whisper-cpp":
        guidance.append("CLIのauto選択に従い、whisper.cppの高速構成を既定にしました。")
    return {
        "asr": asr,
        "diarization": diarization,
        "languages": ["auto", "ja", "en"],
        "formats": ["srt", "vtt", "json", "txt", "md"],
        "defaults": {
            "asr_backend": default_backend,
            "asr_model": default_model,
            "asr_device": default_device,
            "diarization_backend": str(auto.get("diarization_backend", "")),
            "diarization_device": str(auto.get("diarization_device", "")),
        },
        "guidance": guidance,
    }


def _empty_options() -> dict[str, object]:
    return {
        "asr": [],
        "diarization": [],
        "languages": [],
        "formats": [],
        "defaults": {},
        "guidance": [],
    }


def _read_model_path(cli: CliAdapter, profile: str, errors: list[str]) -> str | None:
    try:
        return str(cli.run_text(profile, ["models", "path"]).strip())
    except _CLI_FAILURES as exc:
        errors.append(mask_secrets(str(exc)))
        return None


def _read_openvino_models(cli: CliAdapter, profile: str, errors: list[str]) -> list[dict[str, Any]]:
    try:
        value = cli.run_json(profile, ["models", "list-openvino", "--json"])
        return _list_of_dicts(value)
    except _CLI_FAILURES as exc:
        errors.append(mask_secrets(str(exc)))
        return []


def _dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]
