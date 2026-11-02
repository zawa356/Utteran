"""B7 job contracts: immutable private snapshots, typed requests and public projections.

Pure DTOs only. Nothing here reads files, resolves candidates, opens databases or starts
processes. ``Private*`` / ``JobSnapshot`` / ``InputIdentity`` / ``ResolvedSpec`` hold local
paths and user settings: they are stored only in the private control DB and are never part
of a public projection, an outbox event, a log line or a schema example. Public views are
built field by field from an explicit allowlist.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, StrictStr

from utteran_contracts.base import Contract, VersionedContract
from utteran_contracts.capabilities import RequestedRoute
from utteran_contracts.control import (
    AttemptId,
    QueueItemId,
    QueueView,
    Reason,
    Revision,
    Status,
)
from utteran_contracts.editing import EditingSession, FileInput, SettingOverride
from utteran_contracts.preview import Provenance
from utteran_contracts.settings import Issue, Policy, ProcessingSettings, Ref

Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")]
JobId = Annotated[StrictStr, Field(pattern=r"^job_[0-9a-f]{32}$")]
InputIdentityId = Annotated[StrictStr, Field(pattern=r"^in_[0-9a-f]{32}$")]
ResolutionId = Annotated[StrictStr, Field(pattern=r"^res_[0-9a-f]{32}$")]
# v1.<resolution hex>.<expiry ms>.<hmac hex>: opaque, carries no settings, prompt or path.
TOKEN_PATTERN = r"^v1\.[0-9a-f]{32}\.[0-9]{13}\.[0-9a-f]{64}$"
ResolutionToken = Annotated[StrictStr, Field(pattern=TOKEN_PATTERN, max_length=128)]
ResumeMode = Literal["resume", "fresh", "force"]
Backend = Literal["faster-whisper", "whisper-cpp", "openvino-genai"]
Confidence = Literal["low", "medium"]
EvidenceKind = Literal["smoke", "benchmark", "static_rule"]
# B7 passes ProcessingSettings verbatim to the B2 adapter; no prompt compiler exists yet.
PROMPT_COMPILER_VERSION = "b2-verbatim"
ADAPTER_VERSION = "b4.1"

JobErrorCode = Literal[
    # B3 worker client codes (fixed vocabulary, never raw text)
    "start_failed",
    "profile_invalid",
    "profile_unavailable",
    "profile_incompatible",
    "module_missing",
    "handshake_timeout",
    "protocol_mismatch",
    "schema_mismatch",
    "malformed_message",
    "oversized_message",
    "invalid_sequence",
    "id_mismatch",
    "worker_crash",
    "unexpected_eof",
    "io_timeout",
    "process_timeout",
    "validation_timeout",
    "cancelled",
    "core_failure",
    "invalid_context",
    "unsupported_setting",
    "model_missing",
    "dependency_missing",
    "artifact_invalid",
    # B7 dispatcher checks
    "input_changed",
    "result_mismatch",
    "worker_plan_unsupported",
]
BlockReason = Literal[
    "input_changed",
    "input_unavailable",
    "output_unavailable",
    "candidate_changed",
    "candidate_unavailable",
    "resolver_unavailable",
    "worker_plan_unsupported",
]


# --------------------------------------------------------------------------- private


class InputIdentity(Contract):
    """Point-in-time identity of one explicit input file. Private: carries the path."""

    path: Annotated[StrictStr, Field(min_length=1, max_length=32768)]
    platform: Literal["posix", "nt"]
    device: Annotated[StrictInt, Field(ge=0)]
    inode: Annotated[StrictInt, Field(ge=0)]
    size: Annotated[StrictInt, Field(ge=1)]
    mtime_ns: Annotated[StrictInt, Field(ge=0)]
    ctime_ns: Annotated[StrictInt, Field(ge=0)]
    sha256: Digest


class FallbackSpec(Contract):
    """Frozen preapproved alternative. B7 records it but never executes a fallback."""

    candidate_id: Identifier
    candidate_fingerprint: Digest
    recipe_id: Identifier
    profile_id: Literal["cpu", "cuda", "intel", "vulkan"]
    profile_generation: Identifier
    model_id: StrictStr
    model_revision: Identifier
    backend: Backend
    requested_route: RequestedRoute
    device: Literal["cpu", "cuda", "gpu", "npu"]
    compute_type: Identifier
    adapter_version: Literal["b4.1"]
    runtime_fingerprint: Digest | None


class ResolvedSpec(VersionedContract):
    """Frozen execution decision for one file. Private (settings, local model directory)."""

    candidate_id: Identifier
    # Stable digest of every identity/fingerprint field; observation timestamps excluded.
    candidate_fingerprint: Digest
    recipe_id: Identifier
    profile_id: Literal["cpu", "cuda", "intel", "vulkan"]
    profile_generation: Identifier
    profile_fingerprint: Digest | None
    model_id: StrictStr
    model_revision: Identifier
    model_files_fingerprint: Digest | None
    backend: Backend
    requested_route: RequestedRoute
    device: Literal["cpu", "cuda", "gpu", "npu"]
    device_index: Annotated[StrictInt, Field(ge=0)]
    compute_type: Identifier
    diarization_device: Literal["cpu", "cuda", "xpu"] | None
    settings: ProcessingSettings
    adapter_version: Literal["b4.1"]
    prompt_compiler_version: Literal["b2-verbatim"]
    inventory_revision: Revision
    policy_ref: Ref
    reason_codes: tuple[Identifier, ...]
    confidence: Confidence
    evidence_kinds: tuple[EvidenceKind, ...]
    warnings: tuple[Issue, ...]
    fallback_specs: tuple[FallbackSpec, ...]
    runtime_fingerprint: Digest | None
    worker_support: Literal["supported"]
    worker_model_dir: Annotated[StrictStr, Field(min_length=1, max_length=32768)]


class JobSnapshot(VersionedContract):
    """Phase A JobSnapshot plus provenance. Immutable once committed (DB trigger)."""

    created_at: AwareDatetime
    preset_ref: Ref
    policy_ref: Ref
    preset_digest: Digest
    policy_digest: Digest
    # Frozen copy of the policy revision (B5 DB is separate; no cross-DB commit is claimed).
    policy: Policy
    common_overrides: tuple[SettingOverride, ...]
    file: FileInput
    input_identity: InputIdentity
    provenance: tuple[Provenance, ...]
    resolved: ResolvedSpec
    resume_mode: ResumeMode


# --------------------------------------------------------------------------- requests


class JobSubmit(VersionedContract):
    resolution_token: ResolutionToken
    editing_session: EditingSession
    resume_mode: ResumeMode


class RetryRequest(VersionedContract):
    pass


class RerunRequest(VersionedContract):
    submission: JobSubmit


class CancelRequest(VersionedContract):
    mode: Literal["cooperative", "force"]


class QueueOrder(VersionedContract):
    waiting_item_ids: Annotated[tuple[QueueItemId, ...], Field(max_length=10000)]


class QueueControl(VersionedContract):
    dispatch: Literal["running", "paused"]


class QueueItemPatch(VersionedContract):
    held: StrictBool


# --------------------------------------------------------------------------- public views


class ResolvedSpecView(Contract):
    """Allowlisted projection of a ResolvedSpec: no settings, model path or prompt."""

    candidate_id: Identifier
    recipe_id: Identifier
    profile_id: Literal["cpu", "cuda", "intel", "vulkan"]
    profile_generation: Identifier
    model_id: StrictStr
    model_revision: Identifier
    backend: Backend
    requested_route: RequestedRoute
    device: Literal["cpu", "cuda", "gpu", "npu"]
    compute_type: Identifier
    inventory_revision: Revision
    policy_ref: Ref
    reason_codes: tuple[Identifier, ...]
    confidence: Confidence
    evidence_kinds: tuple[EvidenceKind, ...]
    warnings: tuple[Issue, ...]
    fallback_candidate_ids: tuple[Identifier, ...]
    worker_support: Literal["supported"]


class ResolvedFileView(Contract):
    client_file_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    input_identity: InputIdentityId
    spec: ResolvedSpecView


class Resolution(VersionedContract):
    id: ResolutionId
    token: ResolutionToken
    expires_at: AwareDatetime
    files: Annotated[tuple[ResolvedFileView, ...], Field(min_length=1)]


class JobSnapshotSummary(Contract):
    created_at: AwareDatetime
    client_file_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    preset_ref: Ref
    policy_ref: Ref
    resume_mode: ResumeMode
    input_identity: InputIdentityId
    spec: ResolvedSpecView


class JobAttempt(Contract):
    id: AttemptId
    number: Revision
    state: Literal["active", "released", "interrupted"]
    launch: Literal["intended", "confirmed", "failed"]
    started_at: AwareDatetime
    ended_at: AwareDatetime | None


class JobResult(Contract):
    """Verified completion evidence. Links to the legacy core job; no artifact paths."""

    legacy_core_job_id: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{16}$")]
    artifact_count: Annotated[StrictInt, Field(ge=1, le=5)]
    verified: Literal[True]


class JobErrorView(Contract):
    code: JobErrorCode


class Job(VersionedContract):
    id: JobId
    revision: Revision
    status: Status
    held: StrictBool
    blockers: tuple[Reason, ...]
    block_reason: BlockReason | None
    recovery_required: Reason | None
    reason_code: Reason
    snapshot: JobSnapshotSummary
    attempts: tuple[JobAttempt, ...]
    retry_of: JobId | None
    rerun_of: JobId | None
    result: JobResult | None
    error: JobErrorView | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class JobBatch(VersionedContract):
    jobs: Annotated[tuple[Job, ...], Field(min_length=1)]
    queue_revision: Revision


class JobList(VersionedContract):
    revision: Revision
    items: tuple[Job, ...]
    next_cursor: Annotated[StrictStr, Field(max_length=200)] | None


def spec_view(spec: ResolvedSpec) -> ResolvedSpecView:
    """Allowlist projection; settings, model directory and fingerprints stay private."""
    return ResolvedSpecView(
        candidate_id=spec.candidate_id,
        recipe_id=spec.recipe_id,
        profile_id=spec.profile_id,
        profile_generation=spec.profile_generation,
        model_id=spec.model_id,
        model_revision=spec.model_revision,
        backend=spec.backend,
        requested_route=spec.requested_route,
        device=spec.device,
        compute_type=spec.compute_type,
        inventory_revision=spec.inventory_revision,
        policy_ref=spec.policy_ref,
        reason_codes=spec.reason_codes,
        confidence=spec.confidence,
        evidence_kinds=spec.evidence_kinds,
        warnings=spec.warnings,
        fallback_candidate_ids=tuple(item.candidate_id for item in spec.fallback_specs),
        worker_support=spec.worker_support,
    )


PUBLIC_MODELS: tuple[type[Contract], ...] = (
    JobSubmit,
    RetryRequest,
    RerunRequest,
    CancelRequest,
    QueueOrder,
    QueueControl,
    QueueItemPatch,
    ResolvedSpecView,
    ResolvedFileView,
    Resolution,
    JobSnapshotSummary,
    JobAttempt,
    JobResult,
    JobErrorView,
    Job,
    JobBatch,
    JobList,
    QueueView,
)
