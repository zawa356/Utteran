"""Version-one configuration DTOs. Runtime/backend compatibility belongs to core."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, ClassVar, Literal

from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

from utteran_contracts.base import Contract, VersionedContract


class Ref(Contract):
    id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    revision: Annotated[StrictInt, Field(ge=1)]


class Issue(Contract):
    path: StrictStr
    code: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    message_key: Annotated[StrictStr, Field(min_length=1, max_length=200)]


class ErrorDetail(Contract):
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset(["resource_id"])
    code: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    message_key: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    request_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    retryable: StrictBool
    issues: tuple[Issue, ...]
    resource_id: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None = None


class Error(VersionedContract):
    error: ErrorDetail


class VadSettings(Contract):
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset(["threshold"])
    enabled: StrictBool
    threshold: Annotated[float, Field(ge=0, le=1, strict=True)] | None = None


class FasterWhisperSettings(Contract):
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset(["compute_type"])
    compute_type: (
        Literal["auto", "int8", "int8_float32", "int8_float16", "float16", "float32"] | None
    ) = None


class WhisperCppSettings(Contract):
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset(
        [
            "temperature",
            "temperature_increment",
            "no_context",
            "dtw",
            "threads",
            "entropy_threshold",
            "logprob_threshold",
            "no_speech_threshold",
            "vad",
            "vad_threshold",
            "repetition_limit",
            "max_word_duration_seconds",
        ]
    )
    temperature: Annotated[float, Field(ge=0, le=1, strict=True)] | None = None
    temperature_increment: Annotated[float, Field(ge=0, le=1, strict=True)] | None = None
    no_context: StrictBool | None = None
    dtw: StrictStr | None = None
    threads: Annotated[StrictInt, Field(ge=0)] | None = None
    entropy_threshold: Annotated[float, Field(strict=True)] | None = None
    logprob_threshold: Annotated[float, Field(strict=True)] | None = None
    no_speech_threshold: Annotated[float, Field(ge=0, le=1, strict=True)] | None = None
    vad: StrictBool | None = None
    vad_model: Annotated[StrictStr, Field(min_length=1, max_length=32768)] | None = None
    vad_threshold: Annotated[float, Field(ge=0, le=1, strict=True)] | None = None
    repetition_limit: Annotated[StrictInt, Field(ge=0)] | None = None
    max_word_duration_seconds: Annotated[float, Field(gt=0, strict=True)] | None = None


class GenAISettings(Contract):
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset(["max_new_tokens"])
    max_new_tokens: Annotated[StrictInt, Field(ge=1)] | None = None


class ExtraArguments(Contract):
    backend: Literal["whisper-cpp"]
    tokens: Annotated[
        tuple[Annotated[StrictStr, Field(max_length=4096)], ...], Field(max_length=64)
    ]
    allow_unverified: StrictBool


class ASRSettings(Contract):
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset(
        ["faster_whisper", "whisper_cpp", "openvino_genai", "extra_arguments"]
    )
    backend: Literal["auto", "faster-whisper", "whisper-cpp", "openvino-genai"]
    model: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    language: StrictStr
    initial_prompt: Annotated[StrictStr, Field(max_length=32768)] | None
    glossary: Annotated[
        tuple[Annotated[StrictStr, Field(min_length=1, max_length=256)], ...],
        Field(max_length=1000),
    ]
    beam_size: Annotated[StrictInt, Field(ge=1, le=100)]
    vad: VadSettings
    word_timestamps: Literal["auto", "always", "never"]
    condition_on_previous_text: StrictBool
    faster_whisper: FasterWhisperSettings | None = None
    whisper_cpp: WhisperCppSettings | None = None
    openvino_genai: GenAISettings | None = None
    extra_arguments: ExtraArguments | None = None


class DiarizationSettings(Contract):
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset(["memory_safety_margin"])
    enabled: StrictBool
    backend: Literal["pyannote"]
    model: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    device_preference: Literal["auto", "cpu", "cuda", "xpu"]
    num_speakers: Annotated[StrictInt, Field(ge=1, le=100)] | None
    min_speakers: Annotated[StrictInt, Field(ge=1, le=100)] | None
    max_speakers: Annotated[StrictInt, Field(ge=1, le=100)] | None
    memory_guard: Literal["auto", "warn", "off"]
    memory_safety_margin: Annotated[float, Field(ge=0, lt=1, strict=True)] | None = None

    @model_validator(mode="after")
    def speaker_count_shape(self) -> DiarizationSettings:
        if self.num_speakers is not None and (
            self.min_speakers is not None or self.max_speakers is not None
        ):
            raise ValueError("exact and ranged speaker counts conflict")
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError("speaker range is reversed")
        return self


class PostprocessingSettings(Contract):
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset(
        [
            "max_nearest_distance",
            "min_segment_duration",
            "min_segment_words",
            "silence_switch_threshold",
            "min_clear_turn_duration",
            "max_same_speaker_bridge_gap",
            "unknown_emission_score",
            "min_unknown_duration",
            "min_unknown_characters",
            "max_unsupported_fragment_duration",
            "max_unsupported_fragment_characters",
            "min_fragment_speaker_overlap",
            "renumber_speakers",
            "boundary_snap_max_characters",
            "boundary_snap_max_gap",
            "fallback_characters_per_second",
            "fallback_duration_padding",
            "fallback_min_duration",
        ]
    )
    boundary_snap_enabled: StrictBool
    boundary_snap_unit: Literal["A", "B"]
    merge_gap: Annotated[float, Field(ge=0, strict=True)]
    speaker_switch_penalty: Annotated[float, Field(ge=0, strict=True)]
    max_nearest_distance: Annotated[float, Field(ge=0, strict=True)] | None = None
    min_segment_duration: Annotated[float, Field(ge=0, strict=True)] | None = None
    min_segment_words: Annotated[StrictInt, Field(ge=0)] | None = None
    silence_switch_threshold: Annotated[float, Field(ge=0, strict=True)] | None = None
    min_clear_turn_duration: Annotated[float, Field(ge=0, strict=True)] | None = None
    max_same_speaker_bridge_gap: Annotated[float, Field(ge=0, strict=True)] | None = None
    unknown_emission_score: Annotated[float, Field(ge=0, strict=True)] | None = None
    min_unknown_duration: Annotated[float, Field(ge=0, strict=True)] | None = None
    min_unknown_characters: Annotated[StrictInt, Field(ge=0)] | None = None
    max_unsupported_fragment_duration: Annotated[float, Field(ge=0, strict=True)] | None = None
    max_unsupported_fragment_characters: Annotated[StrictInt, Field(ge=0)] | None = None
    min_fragment_speaker_overlap: Annotated[float, Field(ge=0, strict=True)] | None = None
    renumber_speakers: StrictBool | None = None
    boundary_snap_max_characters: Annotated[StrictInt, Field(ge=0)] | None = None
    boundary_snap_max_gap: Annotated[float, Field(ge=0, strict=True)] | None = None
    fallback_characters_per_second: Annotated[float, Field(ge=0, strict=True)] | None = None
    fallback_duration_padding: Annotated[float, Field(ge=0, strict=True)] | None = None
    fallback_min_duration: Annotated[float, Field(ge=0, strict=True)] | None = None


class OutputSettings(Contract):
    formats: Annotated[
        tuple[Literal["srt", "vtt", "json", "txt", "md"], ...],
        Field(min_length=1, json_schema_extra={"uniqueItems": True}),
    ]
    newline: Literal["lf", "crlf"]
    srt_bom: StrictBool
    show_speaker: StrictBool
    speaker_labels: Mapping[str, Annotated[StrictStr, Field(max_length=256)]]

    @field_validator("formats")
    @classmethod
    def unique_formats(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate output format")
        return value

    @field_validator("speaker_labels")
    @classmethod
    def freeze_labels(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        return MappingProxyType(dict(value))

    @field_serializer("speaker_labels")
    def serialize_labels(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class ProcessingSettings(VersionedContract):
    asr: ASRSettings
    diarization: DiarizationSettings
    postprocessing: PostprocessingSettings
    output: OutputSettings


class PresetWrite(VersionedContract):
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    settings: ProcessingSettings


class Preset(VersionedContract):
    id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    revision: Annotated[StrictInt, Field(ge=1)]
    name: StrictStr
    settings: ProcessingSettings


class PolicyWrite(VersionedContract):
    name: Annotated[StrictStr, Field(min_length=1, max_length=120)]
    mode: Literal["auto", "fixed"]
    fixed_candidate_id: Annotated[StrictStr, Field(min_length=1, max_length=200)] | None
    allowed_profile_ids: tuple[Annotated[StrictStr, Field(min_length=1, max_length=200)], ...]
    objective: Literal["quality", "speed", "balanced"]
    max_memory_bytes: Annotated[StrictInt, Field(ge=1)] | None
    fallback: Literal["none", "preapproved"]
    execution_deadline_seconds: Annotated[float, Field(ge=1, strict=True)] | None

    @model_validator(mode="after")
    def policy_shape(self) -> PolicyWrite:
        if self.mode == "fixed" and (self.fixed_candidate_id is None or self.fallback != "none"):
            raise ValueError("fixed policy requires a candidate and forbids fallback")
        if self.mode == "auto" and self.fixed_candidate_id is not None:
            raise ValueError("automatic policy cannot specify a fixed candidate")
        return self


class Policy(PolicyWrite):
    id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    revision: Annotated[StrictInt, Field(ge=1)]
