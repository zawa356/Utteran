"""Private worker IPC v1 contracts; independent of product and HTTP versions."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

PROTOCOL_VERSION = 1
REQUEST_LIMIT = 16 * 1024 * 1024
EVENT_LIMIT = 1024 * 1024
WorkerProtocolVersion = Literal[1]
Identifier = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Profile = Literal["cpu", "cuda", "intel", "vulkan"]


class WorkerErrorCode(StrEnum):
    START_FAILED = "start_failed"
    PROFILE_INVALID = "profile_invalid"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    PROFILE_INCOMPATIBLE = "profile_incompatible"
    MODULE_MISSING = "module_missing"
    HANDSHAKE_TIMEOUT = "handshake_timeout"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    SCHEMA_MISMATCH = "schema_mismatch"
    MALFORMED_MESSAGE = "malformed_message"
    OVERSIZED_MESSAGE = "oversized_message"
    INVALID_SEQUENCE = "invalid_sequence"
    ID_MISMATCH = "id_mismatch"
    WORKER_CRASH = "worker_crash"
    UNEXPECTED_EOF = "unexpected_eof"
    IO_TIMEOUT = "io_timeout"
    PROCESS_TIMEOUT = "process_timeout"
    VALIDATION_TIMEOUT = "validation_timeout"
    CANCELLED = "cancelled"
    CORE_FAILURE = "core_failure"
    INVALID_CONTEXT = "invalid_context"
    UNSUPPORTED_SETTING = "unsupported_setting"
    MODEL_MISSING = "model_missing"
    DEPENDENCY_MISSING = "dependency_missing"
    ARTIFACT_INVALID = "artifact_invalid"


class IPCModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )

    def __repr_args__(self) -> list[tuple[str, Any]]:
        return []

    def public_projection(self) -> dict[str, str | int]:
        return {"contract": type(self).__name__, "protocol_version": PROTOCOL_VERSION}


class Envelope(IPCModel):
    def public_projection(self) -> dict[str, Any]:
        result = super().public_projection()
        for key in ("type", "sequence", "stage", "state", "fraction", "code"):
            if hasattr(self, key):
                result[key] = getattr(self, key)
        return result

    schema_version: Literal[1] = 1
    protocol_version: WorkerProtocolVersion = 1

    @field_validator("schema_version", "protocol_version", mode="before")
    @classmethod
    def exact_version(cls, value: Any) -> Any:
        if type(value) is not int or value != 1:
            raise ValueError("unsupported version")
        return value


class WorkerExecutionContext(IPCModel):
    # Core validates every nested key against its own Config model before conversion.
    base_config: dict[str, JsonValue]
    asr_backend: str
    asr_model: str
    asr_device: str
    diarization_device: str
    compute_type: str
    native_variant: Literal["cpu", "openvino", "vulkan", "openvino_vulkan"] | None = None


class Identified(Envelope):
    request_id: Identifier
    job_id: Identifier
    attempt_id: Identifier


class WorkerRequest(Identified):
    type: Literal["execute"] = "execute"
    profile: Profile
    input_path: Annotated[str, Field(strict=True, min_length=1, max_length=32768)]
    settings: ProcessingSettings
    context: WorkerExecutionContext
    mode: Literal["single"] = "single"
    resume: Annotated[bool, Field(strict=True)] = True
    force: Annotated[bool, Field(strict=True)] = False


class WorkerCancel(Identified):
    type: Literal["cancel"] = "cancel"


class Event(Envelope):
    sequence: Annotated[int, Field(strict=True, ge=0)]
    request_id: Identifier | None = None
    job_id: Identifier | None = None
    attempt_id: Identifier | None = None

    @model_validator(mode="after")
    def identity(self) -> Self:
        ids = self.request_id, self.job_id, self.attempt_id
        if getattr(self, "type", None) == "hello":
            if any(v is not None for v in ids) or self.sequence != 0:
                raise ValueError("invalid hello identity")
        elif not all(v is not None for v in ids) and (
            getattr(self, "type", None) != "error" or any(v is not None for v in ids)
        ):
            raise ValueError("missing event identity")
        return self


class WorkerHello(Event):
    type: Literal["hello"] = "hello"
    profile: Profile
    capabilities: tuple[Literal["single", "cancel"], ...] = ("single", "cancel")


class WorkerAccepted(Event):
    type: Literal["accepted"] = "accepted"


class WorkerProgress(Event):
    type: Literal["progress"] = "progress"
    stage: Literal["audio", "asr", "diarization", "merge", "export", "job"]
    state: Literal["running", "skipped", "validated"]
    fraction: Annotated[float, Field(ge=0, le=1)] | None = None


class WorkerArtifact(IPCModel):
    # Deliberately private result metadata, never public progress/diagnostics.
    path: Annotated[str, Field(min_length=1, max_length=32768)]
    size: Annotated[int, Field(strict=True, ge=0)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class WorkerCompleted(Event):
    type: Literal["completed"] = "completed"
    core_job_id: Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{16}$")]
    artifacts: Annotated[tuple[WorkerArtifact, ...], Field(min_length=1, max_length=5)]
    manifest: WorkerArtifact


class WorkerError(Event):
    type: Literal["error"] = "error"
    code: WorkerErrorCode


class WorkerCancelAcknowledged(Event):
    type: Literal["cancel_acknowledged"] = "cancel_acknowledged"


from utteran_contracts.settings import ProcessingSettings  # noqa: E402

WorkerRequest.model_rebuild()
WorkerEvent = Annotated[
    WorkerHello
    | WorkerAccepted
    | WorkerProgress
    | WorkerCompleted
    | WorkerError
    | WorkerCancelAcknowledged,
    Field(discriminator="type"),
]
EVENT_ADAPTER: TypeAdapter[WorkerEvent] = TypeAdapter(WorkerEvent)
