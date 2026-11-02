"""B8 setup contracts: plan / approval / operation DTOs and the declarative setup catalog.

Pure DTOs and constant data only. Nothing here observes the machine, opens a database,
downloads, or starts processes. Public views carry fixed vocabularies, opaque IDs and
counts; no local path, token value, argv or worker output is part of any model here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, StrictStr

from utteran_contracts.base import Contract, VersionedContract
from utteran_contracts.control import Reason, Revision, Status
from utteran_contracts.settings import Ref

ProfileKind = Literal["cpu", "cuda", "intel", "vulkan"]
PROFILE_KINDS: tuple[ProfileKind, ...] = ("cpu", "cuda", "intel", "vulkan")
PlanId = Annotated[StrictStr, Field(pattern=r"^plan_[0-9a-f]{32}$")]
ApprovalId = Annotated[StrictStr, Field(pattern=r"^apr_[0-9a-f]{32}$")]
SetupId = Annotated[StrictStr, Field(pattern=r"^setup_[0-9a-f]{32}$")]
Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
StepId = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")]
ModelRef = Annotated[
    StrictStr, Field(pattern=r"^[a-z][a-z0-9-]{0,31}:[A-Za-z0-9][A-Za-z0-9_./-]{0,199}$")
]
AgreementId = Annotated[StrictStr, Field(pattern=r"^hf-gated:[A-Za-z0-9][A-Za-z0-9_./-]{0,199}$")]
CredentialId = Literal["hf_token"]
ByteCount = Annotated[StrictInt, Field(ge=0)]

PlanState = Literal["proposed", "approved", "consumed", "expired"]
ChangeAction = Literal["create", "reuse", "rebuild", "verify"]
ChangeKind = Literal["profile", "model", "native", "ir"]
ChangeReason = Literal[
    "profile_missing",
    "profile_compatible",
    "model_not_observed",
    "model_present_unverified",
]
StepKind = Literal[
    "hardware",
    "venv_sync",
    "credential_preflight",
    "model_download",
    "native_build",
    "ir_build",
    "smoke",
    "register",
]
StepRecovery = Literal["rerun", "verified_reuse", "manager_resume"]
StepStatus = Literal["pending", "running", "succeeded", "failed", "interrupted", "skipped"]
StepReason = Literal[
    "not_required",
    "validated_reuse",
    "verified",
    "launch_failed",
    "process_failed",
    "verification_failed",
    "credential_required",
    "agreement_required",
    "network_error",
    "preflight_invalid",
    "interrupted",
    "cancelled",
    "superseded",
]
EvidenceKind = Literal[
    "profile_manifest_verified",
    "manager_verified",
    "credential_checked",
    "smoke_output_present",
]
WaitingReason = Literal[
    "credential_required", "agreement_required", "inventory_changed", "approval_expired"
]
SetupErrorCode = Literal[
    "launch_failed",
    "process_failed",
    "verification_failed",
    "network_error",
    "preflight_invalid",
    "executor_failed",
    "unsupported_step",
]
# Fixed refusal codes for plan creation (HTTP 409/422; see docs/architecture/b8/api.md).
PlanRejection = Literal[
    "model_unknown",
    "unsupported_combination",
    "native_build_unsupported",
    "setup_backend_unsupported",
    "asr_model_required",
    "diarization_model_mismatch",
    "venv_build_platform_unsupported",
    "profile_rebuild_unsupported",
]

PLAN_TTL_SECONDS = 24 * 3600
APPROVAL_TTL_SECONDS = 24 * 3600
MAX_MODEL_REFS = 8


# --------------------------------------------------------------------------- catalog


@dataclass(frozen=True)
class SetupModel:
    """Declarative subset of the core model catalog (sizes copied, tested for drift).

    The GUI environment must not import the ``utteran`` core, so the plan builder uses
    this table; ``tests/test_b8_setup.py`` asserts it equals ``utteran.models.catalog``.
    """

    backend: str
    model_id: str
    format: str
    repository_id: str
    approximate_size_bytes: int
    gated: bool

    @property
    def ref(self) -> str:
        return f"{self.backend}:{self.model_id}"

    @property
    def managed_component(self) -> str:
        """Managed directory component (``ModelManager.managed_path`` rule)."""
        return re.sub(r"[^A-Za-z0-9_.-]+", "--", self.model_id).strip("-")


SETUP_MODELS: MappingProxyType[str, SetupModel] = MappingProxyType(
    {
        model.ref: model
        for model in (
            SetupModel(
                "faster-whisper",
                "tiny",
                "CTranslate2",
                "Systran/faster-whisper-tiny",
                78200000,
                False,
            ),
            SetupModel(
                "faster-whisper",
                "base",
                "CTranslate2",
                "Systran/faster-whisper-base",
                148000000,
                False,
            ),
            SetupModel(
                "faster-whisper",
                "small",
                "CTranslate2",
                "Systran/faster-whisper-small",
                486000000,
                False,
            ),
            SetupModel(
                "faster-whisper",
                "medium",
                "CTranslate2",
                "Systran/faster-whisper-medium",
                1642824990,
                False,
            ),
            SetupModel(
                "faster-whisper",
                "large-v3-turbo",
                "CTranslate2",
                "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                1717986918,
                False,
            ),
            SetupModel(
                "faster-whisper",
                "large-v3",
                "CTranslate2",
                "Systran/faster-whisper-large-v3",
                3328599654,
                False,
            ),
            SetupModel(
                "faster-whisper",
                "kotoba-whisper-v2.0",
                "CTranslate2",
                "kotoba-tech/kotoba-whisper-v2.0-faster",
                1632087572,
                False,
            ),
            SetupModel(
                "pyannote",
                "pyannote/speaker-diarization-community-1",
                "pyannote pipeline",
                "pyannote/speaker-diarization-community-1",
                35651584,
                True,
            ),
        )
    }
)
# Backends each profile's extras can host (setup.ps1 / cli.PROFILE_EXTRAS). A combination
# outside this table (e.g. OpenVINO GenAI on cuda) is rejected, never approximated.
PROFILE_BACKENDS: MappingProxyType[str, frozenset[str]] = MappingProxyType(
    {
        "cpu": frozenset({"faster-whisper", "pyannote"}),
        "cuda": frozenset({"faster-whisper", "pyannote"}),
        "intel": frozenset({"faster-whisper", "whisper-cpp", "openvino-genai", "pyannote"}),
        "vulkan": frozenset({"faster-whisper", "whisper-cpp", "pyannote"}),
    }
)
# Backends the B8 executor can set up end to end (no native build / IR generation yet).
EXECUTABLE_BACKENDS: frozenset[str] = frozenset({"faster-whisper", "pyannote"})
ASR_BACKENDS: frozenset[str] = frozenset({"faster-whisper", "whisper-cpp", "openvino-genai"})


# --------------------------------------------------------------------------- requests


class SetupPlanRequest(VersionedContract):
    profile_kind: ProfileKind
    model_refs: Annotated[tuple[ModelRef, ...], Field(min_length=1, max_length=MAX_MODEL_REFS)]
    diarization_enabled: StrictBool


class ApprovalRequest(VersionedContract):
    plan_hash: Digest
    accepted_agreement_ids: Annotated[tuple[AgreementId, ...], Field(max_length=16)]


class SetupStart(VersionedContract):
    plan_ref: Ref
    approval_id: ApprovalId


class SetupRetryRequest(VersionedContract):
    pass


class SetupCancelRequest(VersionedContract):
    mode: Literal["cooperative", "force"]


# --------------------------------------------------------------------------- views


class Estimate(Contract):
    """``null`` means unknown. Values come only from catalog sizes; nothing is guessed."""

    download_bytes: ByteCount | None
    disk_bytes: ByteCount | None
    duration_seconds: ByteCount | None
    confidence: Literal["low", "medium", "high"]


class SetupChange(Contract):
    resource_kind: ChangeKind
    resource_id: Annotated[StrictStr, Field(min_length=1, max_length=240)]
    action: ChangeAction
    reason_code: ChangeReason


class SetupStep(Contract):
    id: StepId
    kind: StepKind
    depends_on: tuple[StepId, ...]
    required: StrictBool
    recovery: StepRecovery
    target: Annotated[StrictStr, Field(min_length=1, max_length=240)] | None


class PlanContent(VersionedContract):
    """Hashed plan body (``plan_hash`` = SHA-256 of its canonical JSON)."""

    profile_kind: ProfileKind
    model_refs: tuple[ModelRef, ...]
    diarization_enabled: StrictBool
    inventory_fingerprint: Digest
    estimate: Estimate
    changes: tuple[SetupChange, ...]
    steps: Annotated[tuple[SetupStep, ...], Field(min_length=1)]
    preserved_environment_ids: tuple[ProfileKind, ...]
    required_agreements: tuple[AgreementId, ...]
    missing_credentials: tuple[CredentialId, ...]


class SetupPlan(VersionedContract):
    id: PlanId
    revision: Revision
    state: PlanState
    profile_kind: ProfileKind
    model_refs: tuple[ModelRef, ...]
    diarization_enabled: StrictBool
    inventory_revision: Revision
    inventory_fingerprint: Digest
    plan_hash: Digest
    created_at: AwareDatetime
    expires_at: AwareDatetime
    estimate: Estimate
    changes: tuple[SetupChange, ...]
    steps: tuple[SetupStep, ...]
    preserved_environment_ids: tuple[ProfileKind, ...]
    required_agreements: tuple[AgreementId, ...]
    missing_credentials: tuple[CredentialId, ...]


class Approval(VersionedContract):
    id: ApprovalId
    plan_id: PlanId
    plan_revision: Revision
    plan_hash: Digest
    accepted_agreement_ids: tuple[AgreementId, ...]
    expires_at: AwareDatetime


class StepEvidence(Contract):
    kind: EvidenceKind
    observed_at: AwareDatetime


class StepProgress(Contract):
    step_id: StepId
    kind: StepKind
    status: StepStatus
    reason_code: StepReason | None
    evidence: StepEvidence | None


class SetupErrorView(Contract):
    code: SetupErrorCode
    step_id: StepId | None


class SetupAttempt(Contract):
    id: Annotated[StrictStr, Field(pattern=r"^att_[0-9a-f]{32}$")]
    number: Revision
    state: Literal["active", "released", "interrupted"]
    launch: Literal["intended", "confirmed", "failed"]
    started_at: AwareDatetime
    ended_at: AwareDatetime | None


class SetupOperation(VersionedContract):
    id: SetupId
    revision: Revision
    plan_ref: Ref
    profile_kind: ProfileKind
    status: Status
    held: StrictBool
    recovery_required: Reason | None
    reason_code: Reason
    waiting_reason: WaitingReason | None
    attempt_number: Revision | None
    attempts: tuple[SetupAttempt, ...]
    steps: tuple[StepProgress, ...]
    retry_of: SetupId | None
    error: SetupErrorView | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class SetupOperationList(VersionedContract):
    revision: Revision
    items: tuple[SetupOperation, ...]
    next_cursor: Annotated[StrictStr, Field(max_length=200)] | None


PUBLIC_MODELS: tuple[type[Contract], ...] = (
    SetupPlanRequest,
    ApprovalRequest,
    SetupStart,
    SetupRetryRequest,
    SetupCancelRequest,
    Estimate,
    SetupChange,
    SetupStep,
    PlanContent,
    SetupPlan,
    Approval,
    StepEvidence,
    StepProgress,
    SetupErrorView,
    SetupAttempt,
    SetupOperation,
    SetupOperationList,
)
