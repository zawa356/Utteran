"""Environment aggregation and dynamic option generation for the web UI."""

from __future__ import annotations

from typing import Any, cast

from utteran_gui.cli import CliAdapter, CliError, as_json_dict
from utteran_gui.security import mask_secrets


class EnvironmentService:
    """Read machine state exclusively through profile CLI JSON contracts."""

    def __init__(self, cli: CliAdapter) -> None:
        self.cli = cli

    def snapshot(self, requested_profile: str | None = None) -> dict[str, object]:
        local_profiles = self.cli.profiles()
        errors: list[str] = []
        profile_rows = [
            {
                "name": profile.name,
                "path": str(profile.path),
                "exists": profile.exists,
                "updated_at": profile.updated_at,
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
            except CliError as exc:
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
        }
        if active is None:
            return response
        devices: dict[str, Any] = {}
        models: list[dict[str, Any]] = []
        native: dict[str, Any] = {}
        try:
            devices = as_json_dict(self.cli.run_json(active, ["devices", "--json"]))
        except CliError as exc:
            errors.append(mask_secrets(str(exc)))
        try:
            raw_models = self.cli.run_json(
                active, ["models", "list", "--available", "--all", "--json"]
            )
            if isinstance(raw_models, list):
                models = [
                    cast(dict[str, Any], item) for item in raw_models if isinstance(item, dict)
                ]
        except CliError as exc:
            errors.append(mask_secrets(str(exc)))
        try:
            native = as_json_dict(self.cli.run_json(active, ["native", "status", "--json"]))
        except CliError as exc:
            errors.append(mask_secrets(str(exc)))
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
        return response


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
    except CliError as exc:
        errors.append(mask_secrets(str(exc)))
        return None


def _read_openvino_models(cli: CliAdapter, profile: str, errors: list[str]) -> list[dict[str, Any]]:
    try:
        value = cli.run_json(profile, ["models", "list-openvino", "--json"])
        return _list_of_dicts(value)
    except CliError as exc:
        errors.append(mask_secrets(str(exc)))
        return []


def _dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list_of_dicts(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, Any], item) for item in value if isinstance(item, dict)]
