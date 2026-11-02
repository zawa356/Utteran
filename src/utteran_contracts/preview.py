"""B5 private preview contracts, extending the Phase A validation proposal."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from utteran_contracts.base import Contract, VersionedContract
from utteran_contracts.settings import Issue, Policy, Preset, ProcessingSettings, Ref

Source = Literal["preset", "common", "file", "normalization"]


class Provenance(Contract):
    path: StrictStr
    source: Source
    input_source: Literal["preset", "common", "file"]
    input_status: Literal["value", "null", "empty", "absent"]


class ValidatedFile(Contract):
    client_file_id: StrictStr
    valid: StrictBool
    effective_settings: ProcessingSettings | None
    provenance: tuple[Provenance, ...]
    issues: tuple[Issue, ...]
    warnings: tuple[Issue, ...]
    worker_support: Literal["unsupported", "unverified"]


class ValidationResult(VersionedContract):
    valid: StrictBool
    preset_ref: Ref
    policy_ref: Ref
    files: tuple[ValidatedFile, ...]


class PresetList(VersionedContract):
    revision: Annotated[StrictInt, Field(ge=1)]
    items: tuple[Preset, ...]
    next_cursor: StrictStr | None


class PolicyList(VersionedContract):
    revision: Annotated[StrictInt, Field(ge=1)]
    items: tuple[Policy, ...]
    next_cursor: StrictStr | None
