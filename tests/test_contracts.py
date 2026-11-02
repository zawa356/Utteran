from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from utteran_contracts import (
    ASRSettings,
    DiarizationSettings,
    EditingSession,
    Error,
    ExtraArguments,
    FileInput,
    Policy,
    PolicyWrite,
    Preset,
    PresetWrite,
    ProcessingSettings,
    SettingOverride,
    VadSettings,
    validation_error_projection,
)

ROOT = Path(__file__).parents[1]


@pytest.fixture
def examples() -> dict[str, Any]:
    return json.loads(
        (ROOT / "docs/architecture/phase-a/examples.json").read_text(encoding="utf-8")
    )  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    "key,model",
    [
        ("preset", PresetWrite),
        ("policy", PolicyWrite),
        ("editing", EditingSession),
        ("error", Error),
    ],
)
def test_phase_a_examples_roundtrip(examples: dict[str, Any], key: str, model: Any) -> None:
    dto = model.model_validate(examples[key])
    assert dto.to_wire() == examples[key]
    assert model.model_validate_json(dto.model_dump_json()).to_wire() == examples[key]


@pytest.mark.parametrize("version", [0, 2, "1", True, 1.0, None])
def test_version_is_explicit_integer_one(examples: dict[str, Any], version: Any) -> None:
    with pytest.raises(ValidationError):
        PresetWrite.model_validate({**examples["preset"], "schema_version": version})


@pytest.mark.parametrize(
    "model,payload",
    [
        (VadSettings, {"enabled": False, "unknown": 1}),
        (VadSettings, {"enabled": "false"}),
        (VadSettings, {"enabled": True, "threshold": 1.1}),
        (VadSettings, {"enabled": True, "threshold": "0.5"}),
        (VadSettings, {"enabled": True, "threshold": None}),
        (VadSettings, {"enabled": True, "threshold": float("nan")}),
        (ExtraArguments, {"backend": "faster-whisper", "tokens": [], "allow_unverified": False}),
    ],
)
def test_rejects_unknown_types_ranges_and_enums(model: Any, payload: Any) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_absence_null_false_auto_and_empty_prompt(examples: dict[str, Any]) -> None:
    empty = VadSettings(enabled=False)
    assert VadSettings.model_validate(empty) is empty
    assert empty.threshold is None and "threshold" not in empty.model_fields_set
    assert json.loads(empty.model_dump_json()) == {"enabled": False}
    settings = ASRSettings.model_validate(
        {**examples["preset"]["settings"]["asr"], "language": "auto", "initial_prompt": ""}
    )
    # Normalization belongs to the future core adapter: preserve empty, null and absent.
    assert settings.initial_prompt == "" and settings.language == "auto"
    assert settings.model_copy(update={"initial_prompt": None}).initial_prompt is None
    assert SettingOverride(path="/asr/initial_prompt", value=None).value is None
    assert SettingOverride(path="/diarization/enabled", value=False).value is False
    assert SettingOverride(path="/asr/word_timestamps", value="auto").value == "auto"
    with pytest.raises(ValidationError):
        SettingOverride.model_validate({"path": "/asr/initial_prompt"})


@pytest.mark.parametrize(
    "pointer,value",
    [
        ("/asr/beam_size", None),
        ("/asr/beam_size", "5"),
        ("/asr/beam_size", True),
        ("/asr/beam_size", 0),
        ("/asr", {}),
        ("asr/beam_size", 5),
        ("/asr/not_registered", 1),
        ("/asr/glossary/0", "term"),
        ("/output/speaker_labels/private", "name"),
        ("/asr/~2", 1),
        ("/asr/whisper_cpp/threads", None),
        ("/output/formats", ["srt", "srt"]),
        ("/output/speaker_labels", {"SPEAKER_00": "x" * 257}),
    ],
)
def test_override_rejects_invalid_leaf_or_value(pointer: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        SettingOverride(path=pointer, value=value)


def test_override_duplicates_and_inheritance_scopes(examples: dict[str, Any]) -> None:
    data = examples["editing"]
    override = {"path": "/asr/beam_size", "value": 6}
    data["common_overrides"] = [override, override]
    with pytest.raises(ValidationError):
        EditingSession.model_validate(data)
    data["common_overrides"] = [override]
    data["files"][0]["overrides"] = [override]
    dto = EditingSession.model_validate(data)  # same leaf across scopes is intentionally legal
    assert dto.common_overrides[0].path == dto.files[0].overrides[0].path
    data["files"].append(data["files"][0])
    with pytest.raises(ValidationError):
        EditingSession.model_validate(data)


def test_deep_immutability_and_validated_updates(examples: dict[str, Any]) -> None:
    preset = Preset.model_validate({"id": "synthetic", "revision": 1, **examples["preset"]})
    with pytest.raises(ValidationError):
        preset.revision = 2
    with pytest.raises(TypeError):
        preset.settings.output.speaker_labels["SPEAKER_00"] = "synthetic"  # type: ignore[index]
    assert isinstance(preset.settings.asr.glossary, tuple)
    updated = preset.model_copy(update={"revision": 2})
    assert (preset.revision, updated.revision) == (1, 2)
    with pytest.raises(ValidationError):
        preset.model_copy(update={"revision": 0})
    raw = {"SPEAKER_00": "synthetic"}
    override = SettingOverride(path="/output/speaker_labels", value=raw)
    raw["SPEAKER_00"] = "changed"
    assert override.to_wire()["value"]["SPEAKER_00"] == "synthetic"
    with pytest.raises(TypeError):
        override.value["SPEAKER_00"] = "changed"  # type: ignore[index,call-overload]
    assert (
        SettingOverride.model_validate_json(override.model_dump_json()).to_wire()
        == override.to_wire()
    )


def test_sensitive_values_are_private_projections(examples: dict[str, Any]) -> None:
    sentinel = "SYNTHETIC_PRIVATE_SENTINEL"
    data = examples["preset"]
    data["settings"]["asr"].update(
        initial_prompt=sentinel,
        glossary=[sentinel],
        extra_arguments={"backend": "whisper-cpp", "tokens": [sentinel], "allow_unverified": False},
    )
    dto = PresetWrite.model_validate(data)
    file = FileInput(
        client_file_id="file",
        input_path=f"C:/{sentinel}/input.wav",
        output_dir=f"C:/{sentinel}/output",
        overrides=(),
    )
    for item in (dto, file, SettingOverride(path="/asr/initial_prompt", value=sentinel)):
        assert sentinel not in repr(item) + str(item) + json.dumps(item.public_projection())
        assert sentinel in item.model_dump_json()  # explicit private export must preserve values
    data["settings"]["asr"]["beam_size"] = sentinel
    with pytest.raises(ValidationError) as caught:
        PresetWrite.model_validate(data)
    assert sentinel not in str(caught.value) + repr(caught.value)
    assert sentinel not in json.dumps(validation_error_projection(caught.value))
    # There is no credential/token DTO or catch-all field.
    with pytest.raises(ValidationError):
        PresetWrite.model_validate({**examples["preset"], "token": sentinel})


def test_structural_policy_constraints(examples: dict[str, Any]) -> None:
    for updates in ({"fixed_candidate_id": None}, {"fallback": "preapproved"}, {"mode": "auto"}):
        with pytest.raises(ValidationError):
            PolicyWrite.model_validate({**examples["policy"], **updates})
    policy = Policy.model_validate({"id": "synthetic", "revision": 1, **examples["policy"]})
    assert policy.mode == "fixed"


def test_speaker_counts_are_pure_structure(examples: dict[str, Any]) -> None:
    data = examples["preset"]["settings"]["diarization"]
    for updates in ({"num_speakers": 2, "min_speakers": 1}, {"min_speakers": 3, "max_speakers": 2}):
        with pytest.raises(ValidationError):
            DiarizationSettings.model_validate({**data, **updates})
    # No backend/device availability validation in B1.
    settings = examples["preset"]["settings"]
    settings["asr"].update(backend="openvino-genai", language="ja")
    settings["diarization"]["enabled"] = True
    assert ProcessingSettings.model_validate(settings).diarization.enabled


def test_unordered_inputs_cannot_be_silently_serialized(examples: dict[str, Any]) -> None:
    settings = examples["preset"]["settings"]
    settings["asr"]["glossary"] = {"synthetic"}
    with pytest.raises(ValidationError):
        ProcessingSettings.model_validate(settings)


@pytest.mark.parametrize(
    "value", [[{"invalid": "srt"}], [["srt"]], [{"invalid": "srt"}, {"invalid": "srt"}]]
)
def test_unhashable_override_values_raise_validation_error(value: Any) -> None:
    with pytest.raises(ValidationError):
        SettingOverride(path="/output/formats", value=value)
