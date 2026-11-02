"""B4 data only. Evidence is supplied explicitly; constructing DTOs never probes."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, model_validator

from utteran_contracts.base import Contract, VersionedContract
from utteran_contracts.settings import Policy, ProcessingSettings

Identifier = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")]
Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
ModelID = Annotated[
    str,
    Field(
        strict=True,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)?$",
        max_length=200,
    ),
]
Backend = Literal["faster-whisper", "whisper-cpp", "openvino-genai"]
ModelFamily = Literal["ctranslate2", "ggml", "openvino-genai"]
ExecutionPath = Literal["cli", "worker"]
ObservationKind = Literal[
    "software_support",
    "hardware",
    "runtime",
    "venv",
    "model",
    "native",
    "smoke",
    "execution_path",
    "memory",
    "diarization",
]
ReasonCode = Literal[
    "supported",
    "unsupported_recipe",
    "unsupported_platform",
    "unsupported_compute",
    "unsupported_device",
    "model_family_mismatch",
    "backend_conflict",
    "model_conflict",
    "compute_conflict",
    "inactive_backend_settings",
    "unsupported_setting",
    "genai_diarization_language",
    "model_language_conflict",
    "diarization_platform_unsupported",
    "diarization_device_conflict",
    "diarization_model_conflict",
    "worker_backend_unsupported",
    "worker_diarization_unsupported",
    "worker_model_not_local",
    "worker_single_input_only",
    "profile_not_allowed",
    "not_observed",
    "not_required",
    "profile_missing",
    "profile_manifest_missing",
    "profile_manifest_invalid",
    "profile_path_changed",
    "profile_extras_changed",
    "dependency_lock_changed",
    "current_lock_missing",
    "model_missing",
    "model_revision_unknown",
    "native_missing",
    "runtime_missing",
    "hardware_missing",
    "smoke_missing",
    "smoke_route_mismatch",
    "component_missing",
    "probe_timeout",
    "probe_error",
    "cache_stale",
    "fingerprint_changed",
    "expired",
    "observation_time_missing",
    "observation_from_future",
    "evidence_scope_incomplete",
    "required_not_applicable",
    "memory_unknown",
    "memory_insufficient",
    "memory_limit",
    "deadline_unverified",
    "ready",
    "no_runnable_candidate",
    "fixed_candidate_unavailable",
    "fixed_selected",
    "static_heuristic",
    "stable_id_tiebreak",
    "comparable_benchmark",
    "benchmark_incomparable",
    "benchmark_stale",
    "bridge_unavailable",
    "bridge_invalid",
    "profile_scope_mismatch",
]


class RequestedRoute(Contract):
    native_variant: Literal["cpu", "vulkan", "openvino", "openvino_vulkan"] | None
    encoder: Literal[
        "cpu", "cuda", "gpu", "npu", "openvino-gpu", "selected-device", "backend-default"
    ]
    decoder: Literal["cpu", "cuda", "gpu", "npu", "vulkan", "selected-device", "backend-default"]


class CapabilityRecipe(VersionedContract):
    id: Identifier
    revision: Literal[1]
    backend: Backend
    model_family: ModelFamily
    requested_route: RequestedRoute
    devices: tuple[Literal["cpu", "cuda", "gpu", "npu"], ...]
    compute_types: tuple[Identifier, ...]
    platforms: tuple[Literal["Windows", "Linux"], ...]
    required_extras: tuple[Identifier, ...]
    native_required: StrictBool
    worker_supported: StrictBool
    word_timestamps: StrictBool
    non_space_diarization: StrictBool


class ExecutionIdentity(Contract):
    """Opaque identities only. Paths/executables are never part of public metadata."""

    recipe_id: Identifier
    recipe_revision: Literal[1]
    adapter_version: Literal["b4.1"]
    smoke_version: Literal[1]
    profile_id: Identifier
    profile_generation: Identifier
    profile_fingerprint: Digest | None
    platform: Literal["Windows", "Linux", "unknown"]
    python_abi: Identifier
    runtime_fingerprint: Digest | None
    hardware_fingerprint: Digest | None
    model_id: ModelID
    model_family: ModelFamily
    english_only: StrictBool
    model_revision: Identifier
    model_files_fingerprint: Digest | None
    native_fingerprint: Digest | None
    device: Literal["cpu", "cuda", "gpu", "npu"]
    device_index: Annotated[StrictInt, Field(ge=0)]
    compute_type: Identifier
    execution_path: ExecutionPath
    local_complete_model: StrictBool
    input_count: Annotated[StrictInt, Field(ge=1)]
    diarization_device: Literal["cpu", "cuda", "xpu"] | None
    diarization_device_index: Annotated[StrictInt, Field(ge=0)] | None
    diarization_model: ModelID | None
    diarization_fingerprint: Digest | None


class CapabilityObservation(Contract):
    kind: ObservationKind
    state: Literal["passed", "failed", "unknown", "not_applicable"]
    source: Literal[
        "core-registry",
        "core-evaluator",
        "profile-cli",
        "profile-manifest",
        "device-cache",
        "model-files",
        "native-manifest",
        "smoke",
        "provider",
    ]
    observed_at: AwareDatetime | None
    expires_at: AwareDatetime | None
    fingerprint: Digest | None
    reason_code: ReasonCode
    initialized_route: RequestedRoute | None = None
    encoder_observed: Literal["unknown", "cpu", "cuda", "openvino-gpu", "gpu", "npu"] = "unknown"
    decoder_observed: Literal["unknown", "cpu", "cuda", "vulkan", "gpu", "npu"] = "unknown"

    @model_validator(mode="after")
    def time_range(self) -> CapabilityObservation:
        if self.expires_at is not None and (
            self.observed_at is None or self.expires_at <= self.observed_at
        ):
            raise ValueError("invalid observation time range")
        return self


class BenchmarkEvidence(Contract):
    """Comparison key is generated from every listed experimental condition."""

    fingerprint: Digest
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    fixture_sha256: Digest
    duration_seconds: Annotated[float, Field(gt=0, strict=True)]
    language: Identifier
    model_revision: Identifier
    precision: Identifier
    word_timestamps: Literal["always", "never"]
    diarization: StrictBool
    threads: Annotated[StrictInt, Field(ge=1)]
    warmup_runs: Annotated[StrictInt, Field(ge=0)]
    runtime_version: Identifier
    elapsed_seconds: Annotated[float, Field(gt=0, strict=True)]


class DeploymentInventory(Contract):
    identity: ExecutionIdentity
    observations: tuple[CapabilityObservation, ...]
    required_memory_bytes: Annotated[StrictInt, Field(ge=1)] | None
    available_memory_bytes: Annotated[StrictInt, Field(ge=0)] | None
    benchmark: BenchmarkEvidence | None = None

    @model_validator(mode="after")
    def unique_observations(self) -> DeploymentInventory:
        kinds = [item.kind for item in self.observations]
        if len(kinds) != len(set(kinds)):
            raise ValueError("duplicate observation kind")
        return self


class SelectionIssue(Contract):
    kind: ObservationKind | Literal["policy", "settings"]
    code: ReasonCode


class CapabilityCandidate(VersionedContract):
    id: Identifier
    revision: Digest
    deployment: DeploymentInventory
    requested_route: RequestedRoute
    observations: tuple[CapabilityObservation, ...]
    runnable: Literal["ready", "blocked", "unknown"]
    issues: tuple[SelectionIssue, ...]


class InventoryExclusion(Contract):
    identity: ExecutionIdentity
    reason_codes: tuple[ReasonCode, ...]


class InventorySnapshot(VersionedContract):
    revision: Annotated[StrictInt, Field(ge=1)]
    observed_at: AwareDatetime
    settings: ProcessingSettings
    execution_path: ExecutionPath
    candidates: tuple[CapabilityCandidate, ...]
    exclusions: tuple[InventoryExclusion, ...] = ()

    @model_validator(mode="after")
    def unique_candidates(self) -> InventorySnapshot:
        ids = [candidate.id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate candidate id")
        return self


class SelectionAlternative(Contract):
    candidate: CapabilityCandidate
    eligible: StrictBool
    reason_codes: tuple[ReasonCode, ...]


class ResolvedSelection(VersionedContract):
    """Preparation result only; no job token, persistence, or fallback execution."""

    requested_settings: ProcessingSettings
    policy: Policy
    inventory_revision: Annotated[StrictInt, Field(ge=1)]
    resolved_at: AwareDatetime
    selected: CapabilityCandidate | None
    preapproved: tuple[CapabilityCandidate, ...]
    alternatives: tuple[SelectionAlternative, ...]
    exclusions: tuple[InventoryExclusion, ...]
    reason_codes: tuple[ReasonCode, ...]
    error: Literal["no_runnable_candidate", "fixed_candidate_unavailable"] | None
    evidence_kinds: tuple[Literal["smoke", "benchmark", "static_rule"], ...]
    confidence: Literal["low", "medium"]


class RecipeCatalog(VersionedContract):
    recipes: tuple[CapabilityRecipe, ...]


class InventoryRefreshRequest(VersionedContract):
    profile_id: Literal["cpu", "cuda", "intel", "vulkan"]
    settings: ProcessingSettings
    execution_path: ExecutionPath
    revision: Annotated[StrictInt, Field(ge=1)]


class InventoryRefreshResult(VersionedContract):
    inventory: InventorySnapshot | None
    reason_code: ReasonCode
