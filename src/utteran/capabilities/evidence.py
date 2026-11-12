"""B4 private smoke evidence: version 1 file format, fail-closed reading, overlay.

Only the explicit smoke child (``utteran.capabilities.smoke``) writes records. Every
other caller -- the metadata-only bridge refresh/resolve used by B7 admission and
prestart -- only reads them, and adopts a record only when it exactly matches a freshly
computed identity/settings scope. See docs/architecture/b4/smoke.md §3-§4.

Records hold digests and fixed codes only: no token, path, recording, transcript,
prompt, settings body, raw stderr or traceback. They are private: do not log them.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StrictInt, ValidationError, model_validator

from utteran.capabilities.evaluator import digest, evidence_fingerprint
from utteran.capabilities.fingerprints import current_fingerprints
from utteran_contracts import ProcessingSettings
from utteran_contracts.base import Contract
from utteran_contracts.capabilities import (
    CapabilityObservation,
    DeploymentInventory,
    Digest,
    ExecutionIdentity,
    ReasonCode,
)
from utteran_paths import (
    EVIDENCE_TEMP_PATTERN,
    DataPaths,
    is_marked_dev_root,
    resolve_data_paths,
)

EVIDENCE_FORMAT = "utteran-capability-smoke-evidence"
EVIDENCE_VERSION = 1
EVIDENCE_TTL = timedelta(hours=24)
EVIDENCE_FILE_LIMIT = 8 * 1024
EVIDENCE_RECORD_LIMIT = 64
SMOKE_RECIPES = frozenset({"fw-cpu", "fw-cuda"})

_SCOPE = re.compile(r"^[a-f0-9]{64}$")
_RECORD_NAME = re.compile(r"^[a-f0-9]{64}\.json$")
_TEMP_NAME = re.compile(EVIDENCE_TEMP_PATTERN)
_REPLACE_ATTEMPTS = 100

SmokeRecordOutcome = Literal[
    "passed",
    "running",
    "model_load_failed",
    "backend_unavailable",
    "out_of_memory",
    "inference_failed",
    "compute_mismatch",
    "runtime_mismatch",
]
EvidenceStatus = Literal[
    "adopted",
    "absent",
    "invalid",
    "unsafe",
    "too_large",
    "scope_mismatch",
    "not_passed",
    "future",
    "expired",
]
Fingerprints = Callable[[str], tuple[str | None, str | None]]
Index = Annotated[StrictInt, Field(ge=0)] | None


class SmokeEvidence(Contract):
    """One smoke result for one evidence scope. ``passed`` means: the model loaded and the
    fixture inference completed; whether the read-back route verifies is decided later
    by the evaluator (``_verify_smoke_route``), never here."""

    format: Literal["utteran-capability-smoke-evidence"]
    version: Literal[1]
    scope: Digest
    recipe_id: Literal["fw-cpu", "fw-cuda"]
    state: Literal["pending", "passed", "failed"]
    outcome: SmokeRecordOutcome
    observed_at: AwareDatetime
    expires_at: AwareDatetime
    encoder_observed: Literal["unknown", "cpu", "cuda"]
    decoder_observed: Literal["unknown", "cpu", "cuda"]
    encoder_device_index_observed: Index
    decoder_device_index_observed: Index
    required_memory_bytes: Annotated[StrictInt, Field(ge=1)] | None
    available_memory_bytes: Annotated[StrictInt, Field(ge=0)] | None
    fixture_sha256: Digest
    checksum: Digest

    @model_validator(mode="after")
    def consistent(self) -> SmokeEvidence:
        if not (self.observed_at < self.expires_at <= self.observed_at + EVIDENCE_TTL):
            raise ValueError("invalid evidence time range")
        if (self.state == "passed") != (self.outcome == "passed"):
            raise ValueError("state and outcome disagree")
        if self.state == "pending" and self.outcome != "running":
            raise ValueError("pending evidence must be running")
        return self


class EvidenceWriteError(Exception):
    """Fixed code only; never carries a path or OS message."""

    def __init__(self, code: Literal["evidence_write_failed", "evidence_limit"]) -> None:
        self.code = code
        super().__init__(code)


def _body(record_wire: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record_wire.items() if key != "checksum"}


def seal_evidence(**fields: Any) -> SmokeEvidence:
    """Build a record with format/version and its content checksum filled in."""
    draft = SmokeEvidence.model_validate(
        {
            "format": EVIDENCE_FORMAT,
            "version": EVIDENCE_VERSION,
            **fields,
            "checksum": "0" * 64,
        }
    )
    return draft.model_copy(update={"checksum": digest(_body(draft.to_wire()))})


def evidence_directory(paths: DataPaths | None = None) -> Path | None:
    """Evidence location, or ``None`` unless an explicit, G-09-marked data root is used."""
    paths = paths or resolve_data_paths()
    if paths.root is None or paths.capability_evidence is None:
        return None
    return paths.capability_evidence if is_marked_dev_root(paths.root) else None


def _check_time(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an explicit aware evaluation time is required")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise ValueError("non-finite number")


def _directory_safe(directory: Path) -> bool | None:
    """True when a real directory, None when absent, False when a link or other type."""
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return None
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode)


def inspect_evidence(
    directory: Path, scope: str, *, now: datetime
) -> tuple[EvidenceStatus, SmokeEvidence | None]:
    """Read-only. Returns a fixed status; only ``adopted`` carries the record."""
    _check_time(now)
    if not _SCOPE.fullmatch(scope):
        return "invalid", None
    safe = _directory_safe(directory)
    if safe is None:
        return "absent", None
    if not safe:
        return "unsafe", None
    path = directory / f"{scope}.json"
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return "unsafe", None
    if not stat.S_ISREG(info.st_mode):
        return "unsafe", None
    if info.st_size > EVIDENCE_FILE_LIMIT:
        return "too_large", None
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return "unsafe", None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return "unsafe", None
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(EVIDENCE_FILE_LIMIT + 1)
    except OSError:
        return "unsafe", None
    finally:
        os.close(descriptor)
    if len(raw) > EVIDENCE_FILE_LIMIT:
        return "too_large", None
    try:
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_reject_constant
        )
        if not isinstance(data, dict):
            raise ValueError("not an object")
        record = SmokeEvidence.model_validate(data)
        if record.checksum != digest(_body(record.to_wire())):
            raise ValueError("checksum mismatch")
    except (ValueError, UnicodeError, ValidationError, TypeError):
        return "invalid", None
    if record.scope != scope:
        return "scope_mismatch", None
    if record.state != "passed":
        return "not_passed", None
    if record.observed_at > now:
        return "future", None
    if record.expires_at <= now:
        return "expired", None
    return "adopted", record


def read_evidence(directory: Path, scope: str, *, now: datetime) -> SmokeEvidence | None:
    status, record = inspect_evidence(directory, scope, now=now)
    return record if status == "adopted" else None


def _check_writable(directory: Path) -> None:
    for path in (directory.parent, directory):
        if _directory_safe(path) is not True:
            raise EvidenceWriteError("evidence_write_failed")


def write_evidence(directory: Path, record: SmokeEvidence) -> None:
    """Atomic replace of ``<scope>.json``. Called only by the explicit smoke child."""
    if record.checksum != digest(_body(record.to_wire())):
        raise EvidenceWriteError("evidence_write_failed")
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError:
        raise EvidenceWriteError("evidence_write_failed") from None
    _check_writable(directory)
    target = directory / f"{record.scope}.json"
    try:
        existing = os.lstat(target)
    except FileNotFoundError:
        existing = None
    except OSError:
        raise EvidenceWriteError("evidence_write_failed") from None
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise EvidenceWriteError("evidence_write_failed")
    if existing is None:
        try:
            count = sum(1 for entry in os.scandir(directory) if _RECORD_NAME.fullmatch(entry.name))
        except OSError:
            raise EvidenceWriteError("evidence_write_failed") from None
        if count >= EVIDENCE_RECORD_LIMIT:
            raise EvidenceWriteError("evidence_limit")
    payload = json.dumps(record.to_wire(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    temporary = directory / f".{record.scope}.{secrets.token_hex(8)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(payload)
            sink.flush()
            os.fsync(sink.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                # Windows refuses to replace a file another process has open; retry briefly.
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(0.01)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise EvidenceWriteError("evidence_write_failed") from None


def remove_evidence(directory: Path, scope: str) -> bool:
    """Remove one record of our own format (smoke child only, when it cannot invalidate)."""
    if not _SCOPE.fullmatch(scope) or _directory_safe(directory) is not True:
        return False
    target = directory / f"{scope}.json"
    try:
        if not stat.S_ISREG(os.lstat(target).st_mode):
            return False
        os.unlink(target)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def remove_temporaries(directory: Path) -> int:
    """Delete leftover ``.<scope>.<random>.tmp`` files of an interrupted writer only."""
    if _directory_safe(directory) is not True:
        return 0
    removed = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return 0
    for entry in entries:
        if _TEMP_NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
            with contextlib.suppress(OSError):
                os.unlink(entry.path)
                removed += 1
    return removed


# --------------------------------------------------------------------------- overlay


def smoke_identity(
    identity: ExecutionIdentity, fingerprints: Fingerprints = current_fingerprints
) -> ExecutionIdentity | None:
    """Identity with freshly computed runtime/hardware fingerprints, or None if no smoke
    evidence can apply (only worker-path faster-whisper recipes have a smoke executor)."""
    if (
        identity.recipe_id not in SMOKE_RECIPES
        or identity.execution_path != "worker"
        or identity.model_family != "ctranslate2"
    ):
        return None
    runtime, hardware = fingerprints(identity.device)
    return ExecutionIdentity.model_validate(
        {
            **identity.model_dump(mode="json"),
            "runtime_fingerprint": runtime,
            "hardware_fingerprint": hardware,
        }
    )


def _observation(
    kind: str,
    state: str,
    code: ReasonCode,
    scope: str,
    record: SmokeEvidence,
    **extra: Any,
) -> CapabilityObservation:
    return CapabilityObservation.model_validate(
        dict(
            kind=kind,
            state=state,
            source="smoke",
            observed_at=record.observed_at,
            expires_at=record.expires_at,
            fingerprint=scope,
            reason_code=code,
            **extra,
        )
    )


def _merge(
    deployment: DeploymentInventory,
    identity: ExecutionIdentity,
    old_scope: str,
    scope: str,
    record: SmokeEvidence,
) -> DeploymentInventory:
    smoke = _observation(
        "smoke",
        "passed",
        "supported",
        scope,
        record,
        encoder_observed=record.encoder_observed,
        decoder_observed=record.decoder_observed,
        encoder_device_index_observed=record.encoder_device_index_observed,
        decoder_device_index_observed=record.decoder_device_index_observed,
    )
    runtime = (
        _observation("runtime", "passed", "supported", scope, record)
        if identity.runtime_fingerprint is not None
        else _observation("runtime", "unknown", "not_observed", scope, record)
    )
    hardware = (
        _observation("hardware", "passed", "supported", scope, record)
        if identity.hardware_fingerprint is not None
        else _observation("hardware", "unknown", "not_observed", scope, record)
    )
    required, available = record.required_memory_bytes, record.available_memory_bytes
    if required is None or available is None:
        memory = _observation("memory", "unknown", "memory_unknown", scope, record)
    elif required > available:
        memory = _observation("memory", "failed", "memory_insufficient", scope, record)
    else:
        memory = _observation("memory", "passed", "supported", scope, record)
    added = {o.kind: o for o in (smoke, runtime, hardware, memory)}
    kept = tuple(
        o.model_copy(update={"fingerprint": scope}) if o.fingerprint == old_scope else o
        for o in deployment.observations
        if o.kind not in added
    )
    return DeploymentInventory(
        identity=identity,
        observations=kept + tuple(added.values()),
        required_memory_bytes=required,
        available_memory_bytes=available,
        benchmark=deployment.benchmark,
    )


def apply_smoke_evidence(
    deployments: tuple[DeploymentInventory, ...],
    settings: ProcessingSettings,
    *,
    now: datetime,
    directory: Path | None,
    fingerprints: Fingerprints = current_fingerprints,
) -> tuple[DeploymentInventory, ...]:
    """Read-only overlay. A deployment changes only when an adopted record matches the
    scope of its freshly re-identified identity; otherwise it is returned unchanged."""
    _check_time(now)
    if directory is None or _directory_safe(directory) is not True:
        return deployments
    result = []
    for deployment in deployments:
        identity = smoke_identity(deployment.identity, fingerprints)
        if identity is None:
            result.append(deployment)
            continue
        scope = evidence_fingerprint(identity, settings)
        record = read_evidence(directory, scope, now=now)
        if record is None or record.recipe_id != identity.recipe_id:
            result.append(deployment)
            continue
        old_scope = evidence_fingerprint(deployment.identity, settings)
        result.append(_merge(deployment, identity, old_scope, scope, record))
    return tuple(result)
