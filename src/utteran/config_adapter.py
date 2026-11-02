"""Pure structured-settings to legacy Config conversion; no settings I/O or probes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from utteran.asr.registry import validate_asr_configuration
from utteran.config import ASRConfig, Config
from utteran.errors import ConfigurationError
from utteran_contracts import ProcessingSettings


class ConfigAdaptationError(ConfigurationError):
    """Value-free, typed conversion failure, retaining the legacy exit code 2."""

    def __init__(self, code: str, path: str) -> None:
        self.code = code
        self.path = path
        super().__init__(f"構造化設定を変換できません: {code} ({path})")


@dataclass(frozen=True)
class ExecutionContext:
    """Explicit resolved choices and ALL legacy defaults outside ProcessingSettings.

    base_config must be supplied deliberately, e.g. Config.model_validate({}).
    Never use Config.load for a finalized worker request.
    """

    base_config: Config = field(repr=False)
    asr_backend: str
    asr_model: str = field(repr=False)
    asr_device: str
    diarization_device: str
    compute_type: str
    native_variant: Literal["cpu", "openvino", "vulkan", "openvino_vulkan"] | None = None


@dataclass(frozen=True)
class SettingSource:
    path: str
    source: Literal["context", "dto", "normalized"]


@dataclass(frozen=True)
class AdaptedConfig:
    config: Config = field(repr=False)
    sources: tuple[SettingSource, ...]


def _reject(path: str, code: str = "unsupported_setting") -> None:
    raise ConfigAdaptationError(code, path)


def adapt_settings(settings: ProcessingSettings, context: ExecutionContext) -> AdaptedConfig:
    """Convert only explicit inputs; Config.model_validate bypasses env/TOML loading.

    Core retains semantic validation and all runtime checks at pipeline/backend load.
    A conversion success is not an environment readiness claim.
    """
    if not isinstance(context.base_config, Config):
        _reject("/context/base_config", "invalid_execution_context")
    if not isinstance(context.diarization_device, str):
        _reject("/context/diarization_device", "invalid_execution_context")
    asr = settings.asr
    for name in ("asr_backend", "asr_model", "asr_device", "compute_type"):
        value = getattr(context, name)
        if not isinstance(value, str) or not value.strip() or value.strip().casefold() == "auto":
            _reject(f"/context/{name}", "unresolved_execution_context")
    if settings.diarization.enabled:
        device = context.diarization_device
        if not device.strip() or device != device.strip() or device.casefold() == "auto":
            _reject("/context/diarization_device", "unresolved_execution_context")
    if asr.backend != "auto" and asr.backend != context.asr_backend:
        _reject("/asr/backend", "context_conflict")
    # Use the same static catalogue alias normalization as legacy ASRConfig.
    expected = ASRConfig(backend=context.asr_backend, model=context.asr_model).model
    requested = ASRConfig(backend=context.asr_backend, model=asr.model).model
    if asr.model != "auto" and requested != expected:
        _reject("/asr/model", "context_conflict")
    if context.asr_backend not in {"faster-whisper", "whisper-cpp"}:
        _reject("/asr/backend", "unsupported_structured_backend")
    if asr.glossary:
        _reject("/asr/glossary")
    if asr.extra_arguments is not None:
        _reject("/asr/extra_arguments")
    for namespace in ("faster_whisper", "whisper_cpp", "openvino_genai"):
        active = "faster_whisper" if context.asr_backend == "faster-whisper" else "whisper_cpp"
        if namespace != active and getattr(asr, namespace) is not None:
            _reject(f"/asr/{namespace}", "inactive_backend_settings")

    payload = context.base_config.model_dump(mode="json")

    def paths(value: Any, prefix: str = "") -> list[str]:
        if isinstance(value, dict) and prefix != "/output/speaker_labels":
            return [p for key, item in value.items() for p in paths(item, f"{prefix}/{key}")]
        return [prefix]

    sources: dict[str, Literal["context", "dto", "normalized"]] = {
        path: "context" for path in paths(payload)
    }
    native_changes: dict[str, Any] = {}
    mapping: dict[str, Any] = {
        "backend": context.asr_backend,
        "model": expected,
        "device": context.asr_device,
        "compute_type": context.compute_type,
        "language": None if asr.language.strip().casefold() == "auto" else asr.language.strip(),
        "initial_prompt": asr.initial_prompt,
        "vad_filter": asr.vad.enabled,
        "beam_size": asr.beam_size,
        "word_timestamps": asr.word_timestamps,
        "condition_on_previous_text": asr.condition_on_previous_text,
    }
    if context.asr_backend == "faster-whisper":
        if context.native_variant is not None:
            _reject("/context/native_variant", "context_conflict")
        if asr.vad.threshold is not None:
            _reject("/asr/vad/threshold")
        if asr.word_timestamps == "never":
            _reject("/asr/word_timestamps", "unsupported_setting")
        compute = asr.faster_whisper.compute_type if asr.faster_whisper is not None else None
        if compute not in {None, "auto", context.compute_type}:
            _reject("/asr/faster_whisper/compute_type", "context_conflict")
    else:
        if context.native_variant is None or context.native_variant != context.asr_device:
            _reject("/context/native_variant", "context_conflict")
        if asr.condition_on_previous_text:
            _reject("/asr/condition_on_previous_text")
        native = asr.whisper_cpp.to_wire() if asr.whisper_cpp is not None else {}
        if "vad" in native and native["vad"] != asr.vad.enabled:
            _reject("/asr/whisper_cpp/vad", "context_conflict")
        if (
            asr.vad.threshold is not None
            and "vad_threshold" in native
            and native["vad_threshold"] != asr.vad.threshold
        ):
            _reject("/asr/vad/threshold", "context_conflict")
        native["variant"] = context.native_variant
        native["vad"] = asr.vad.enabled
        if asr.vad.threshold is not None:
            native["vad_threshold"] = asr.vad.threshold
        native_changes = native
        mapping["whisper_cpp"] = {**payload["asr"]["whisper_cpp"], **native}
    payload["asr"].update(mapping)
    diarization = settings.diarization.to_wire()
    device = diarization.pop("device_preference")
    if device != "auto" and device != context.diarization_device:
        _reject("/diarization/device_preference", "context_conflict")
    diarization["device"] = context.diarization_device
    for name in ("num_speakers", "min_speakers", "max_speakers"):
        diarization[name] = diarization[name] or 0
    payload["diarization"].update(diarization)
    payload["alignment"].update(settings.postprocessing.to_wire())
    payload["output"].update(settings.output.to_wire())
    for key in mapping:
        if key != "whisper_cpp":
            sources[f"/asr/{key}"] = "dto"
    for key in native_changes:
        sources[f"/asr/whisper_cpp/{key}"] = "dto"
    for section, values in (
        ("diarization", diarization),
        ("alignment", settings.postprocessing.to_wire()),
        ("output", settings.output.to_wire()),
    ):
        for key in values:
            sources[f"/{section}/{key}"] = "dto"
    for path in (
        "/asr/backend",
        "/asr/model",
        "/asr/device",
        "/asr/compute_type",
        "/asr/language",
        "/diarization/device",
        "/diarization/num_speakers",
        "/diarization/min_speakers",
        "/diarization/max_speakers",
    ):
        sources[path] = "normalized"
    try:
        config = Config.model_validate(payload)
    except ValidationError:
        raise ConfigAdaptationError("invalid_core_setting", "/settings") from None
    validate_asr_configuration(config)
    return AdaptedConfig(
        config, tuple(SettingSource(path, source) for path, source in sorted(sources.items()))
    )
