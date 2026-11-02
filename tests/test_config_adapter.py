from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from utteran.config import Config
from utteran.config_adapter import ConfigAdaptationError, ExecutionContext, adapt_settings
from utteran.jobs import stage_config_hashes
from utteran_contracts import ProcessingSettings


@pytest.fixture
def settings_data() -> dict[str, Any]:
    root = Path(__file__).parents[1]
    return json.loads((root / "docs/architecture/phase-a/examples.json").read_text())["preset"][
        "settings"
    ]


def context(**changes: Any) -> ExecutionContext:
    base = Config.model_validate(
        {
            "general": {
                "output_dir": "synthetic-output",
                "job_dir": "synthetic-jobs",
                "log_retention_days": 17,
            },
            "alignment": {"fallback_min_duration": 1.5},
        }
    )
    return ExecutionContext(
        base, "faster-whisper", "large-v3-turbo", "cpu", "cpu", "int8", **changes
    )


def test_structured_and_legacy_effective_config_hashes_match(settings_data: dict[str, Any]) -> None:
    dto = ProcessingSettings.model_validate(settings_data)
    ctx = context()
    converted = adapt_settings(dto, ctx)
    legacy = Config.model_validate(
        {
            **ctx.base_config.model_dump(),
            "asr": {
                **ctx.base_config.asr.model_dump(),
                "backend": "faster-whisper",
                "device": "cpu",
                "compute_type": "int8",
                "initial_prompt": None,
            },
            "diarization": {
                **ctx.base_config.diarization.model_dump(),
                "enabled": False,
                "device": "cpu",
            },
            "output": settings_data["output"],
        }
    )
    assert converted.config.model_dump() == legacy.model_dump()
    assert stage_config_hashes(converted.config, "synthetic") == stage_config_hashes(
        legacy, "synthetic"
    )
    assert converted.config.general.log_retention_days == 17
    assert (
        dict((s.path, s.source) for s in converted.sources)["/alignment/fallback_min_duration"]
        == "context"
    )


def test_adapter_is_pure_and_preserves_inputs(
    settings_data: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    dto = ProcessingSettings.model_validate(settings_data)
    ctx = context()
    before = dto.model_dump_json(), ctx.base_config.model_dump_json()
    monkeypatch.setenv("UTTERAN_ASR__MODEL", "must-not-apply")
    monkeypatch.setenv("UTTERAN_GENERAL__LOG_RETENTION_DAYS", "999")
    monkeypatch.setattr(Config, "load", lambda **kw: pytest.fail("settings I/O"))
    monkeypatch.setattr(Path, "exists", lambda *_: pytest.fail("filesystem probe"))
    first, second = adapt_settings(dto, ctx), adapt_settings(dto, ctx)
    assert first.config.model_dump() == second.config.model_dump()
    assert first.sources == second.sources
    assert before == (dto.model_dump_json(), ctx.base_config.model_dump_json())
    assert first.config.asr.model == "large-v3-turbo"
    assert first.config.general.log_retention_days == 17


@pytest.mark.parametrize("prompt", [None, "", "synthetic prompt"])
def test_prompt_auto_language_and_speaker_sentinels(
    settings_data: dict[str, Any], prompt: str | None
) -> None:
    settings_data["asr"].update(initial_prompt=prompt, language="auto")
    cfg = adapt_settings(ProcessingSettings.model_validate(settings_data), context()).config
    assert cfg.asr.initial_prompt == prompt
    assert cfg.asr.language is None
    assert (
        cfg.diarization.num_speakers,
        cfg.diarization.min_speakers,
        cfg.diarization.max_speakers,
    ) == (0, 0, 0)


def test_cpp_maps_all_native_settings_and_vad(settings_data: dict[str, Any]) -> None:
    settings_data["asr"].update(
        backend="whisper-cpp",
        model="large-v3-turbo",
        whisper_cpp={"temperature": 0.3, "threads": 4, "dtw": "auto", "vad_model": None},
    )
    settings_data["asr"].pop("faster_whisper")
    settings_data["asr"]["vad"] = {"enabled": False, "threshold": 0.7}
    ctx = replace(
        context(native_variant="openvino_vulkan"),
        asr_backend="whisper-cpp",
        asr_device="openvino_vulkan",
        compute_type="ggml",
    )
    cfg = adapt_settings(ProcessingSettings.model_validate(settings_data), ctx).config
    assert cfg.asr.whisper_cpp.variant == "openvino_vulkan"
    assert cfg.asr.whisper_cpp.temperature == 0.3
    assert cfg.asr.whisper_cpp.threads == 4
    assert not cfg.asr.whisper_cpp.vad and not cfg.asr.vad_filter
    assert cfg.asr.whisper_cpp.vad_threshold == 0.7
    assert cfg.asr.whisper_cpp.vad_model is None
    assert cfg.asr.word_timestamps == "auto"


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("glossary", ["synthetic"], "unsupported_setting"),
        (
            "extra_arguments",
            {"backend": "whisper-cpp", "tokens": [], "allow_unverified": False},
            "unsupported_setting",
        ),
        ("openvino_genai", {}, "inactive_backend_settings"),
        ("whisper_cpp", {}, "inactive_backend_settings"),
        ("word_timestamps", "never", "unsupported_setting"),
        ("vad", {"enabled": True, "threshold": 0.5}, "unsupported_setting"),
    ],
)
def test_unimplemented_settings_are_never_ignored(
    settings_data: dict[str, Any], field: str, value: Any, code: str
) -> None:
    settings_data["asr"][field] = value
    with pytest.raises(ConfigAdaptationError) as caught:
        adapt_settings(ProcessingSettings.model_validate(settings_data), context())
    assert caught.value.code == code and caught.value.exit_code == 2


@pytest.mark.parametrize(
    "name,value",
    [
        ("asr_backend", "auto"),
        ("asr_model", "auto"),
        ("asr_device", "auto"),
        ("compute_type", "auto"),
    ],
)
def test_unresolved_context_is_rejected(
    settings_data: dict[str, Any], name: str, value: str
) -> None:
    with pytest.raises(ConfigAdaptationError, match="unresolved_execution_context"):
        adapt_settings(
            ProcessingSettings.model_validate(settings_data), replace(context(), **{name: value})
        )


def test_alias_and_explicit_auto_resolution_context(settings_data: dict[str, Any]) -> None:
    settings_data["asr"].update(backend="auto", model="auto")
    cfg = adapt_settings(ProcessingSettings.model_validate(settings_data), context()).config
    assert cfg.asr.backend == "faster-whisper" and cfg.asr.model == "large-v3-turbo"
    settings_data["asr"].update(backend="faster-whisper", model="faster-whisper:large-v3-turbo")
    assert (
        adapt_settings(ProcessingSettings.model_validate(settings_data), context()).config.asr.model
        == "large-v3-turbo"
    )


def test_genai_and_conflicting_routes_are_explicitly_rejected(
    settings_data: dict[str, Any],
) -> None:
    settings_data["asr"].update(backend="openvino-genai")
    with pytest.raises(ConfigAdaptationError, match="unsupported_structured_backend"):
        adapt_settings(
            ProcessingSettings.model_validate(settings_data),
            replace(context(), asr_backend="openvino-genai"),
        )
    settings_data["asr"].update(backend="whisper-cpp")
    settings_data["asr"].pop("faster_whisper")
    with pytest.raises(ConfigAdaptationError, match="context_conflict"):
        adapt_settings(
            ProcessingSettings.model_validate(settings_data),
            replace(
                context(native_variant="openvino"), asr_backend="whisper-cpp", asr_device="cuda"
            ),
        )


def test_validation_error_and_trace_never_contain_private_values(
    settings_data: dict[str, Any],
) -> None:
    sentinel = "SYNTHETIC_PRIVATE_PROMPT"
    settings_data["asr"]["initial_prompt"] = sentinel
    settings_data["postprocessing"]["fallback_min_duration"] = 0
    with pytest.raises(ConfigAdaptationError) as caught:
        adapt_settings(ProcessingSettings.model_validate(settings_data), context())
    assert sentinel not in str(caught.value) + repr(caught.value)
    settings_data["postprocessing"].pop("fallback_min_duration")
    result = adapt_settings(ProcessingSettings.model_validate(settings_data), context())
    assert sentinel not in repr(result) + repr(result.sources)
    assert result.config.asr.initial_prompt == sentinel


def test_native_conflict_and_context_defaults_are_not_discarded(
    settings_data: dict[str, Any],
) -> None:
    settings_data["asr"].update(backend="whisper-cpp", whisper_cpp={"vad": False})
    settings_data["asr"].pop("faster_whisper")
    ctx = replace(context(native_variant="cpu"), asr_backend="whisper-cpp", compute_type="ggml")
    with pytest.raises(ConfigAdaptationError, match="context_conflict"):
        adapt_settings(ProcessingSettings.model_validate(settings_data), ctx)
    settings_data["asr"]["whisper_cpp"] = {"threads": 2}
    ctx.base_config.asr.whisper_cpp.temperature = 0.4
    ctx.base_config.diarization.memory_safety_margin = 0.2
    result = adapt_settings(ProcessingSettings.model_validate(settings_data), ctx)
    assert result.config.asr.whisper_cpp.temperature == 0.4
    assert result.config.diarization.memory_safety_margin == 0.2
    sources = {s.path: s.source for s in result.sources}
    assert sources["/asr/whisper_cpp/temperature"] == "context"
    assert sources["/asr/whisper_cpp/threads"] == "dto"


@pytest.mark.parametrize(
    "device", ["", " ", "\t", "auto", "AUTO", " auto ", "Auto ", " cpu", "cpu "]
)
def test_enabled_diarization_rejects_unresolved_or_padded_device(
    settings_data: dict[str, Any], device: str
) -> None:
    settings_data["diarization"]["enabled"] = True
    with pytest.raises(ConfigAdaptationError, match="unresolved_execution_context"):
        adapt_settings(
            ProcessingSettings.model_validate(settings_data),
            replace(context(), diarization_device=device),
        )


@pytest.mark.parametrize("device", ["cpu", "cuda:0", "xpu:0"])
def test_enabled_diarization_keeps_explicit_device(
    settings_data: dict[str, Any], device: str
) -> None:
    settings_data["diarization"]["enabled"] = True
    result = adapt_settings(
        ProcessingSettings.model_validate(settings_data),
        replace(context(), diarization_device=device),
    )
    assert result.config.diarization.device == device


@pytest.mark.parametrize("device", ["", " ", "auto", " auto "])
def test_disabled_diarization_preserves_legacy_device_behavior(
    settings_data: dict[str, Any], device: str
) -> None:
    settings_data["diarization"]["enabled"] = False
    assert (
        adapt_settings(
            ProcessingSettings.model_validate(settings_data),
            replace(context(), diarization_device=device),
        ).config.diarization.device
        == device
    )
