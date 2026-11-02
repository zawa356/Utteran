"""B7 admission: validate, resolve, issue short-lived tokens and freeze job snapshots.

Nothing here starts a worker. Readiness requires a B4 ``ready`` candidate *and* a
configuration the current B3 worker can execute; a B5 preview, a model on disk or a
settings value alone is never treated as evidence. Unsupported routes are refused
declaratively and are never redirected to the legacy CLI.
"""

from __future__ import annotations

import hashlib
import hmac
import ntpath
import os
import secrets
import stat
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from utteran_contracts.capabilities import (
    CapabilityCandidate,
    InventoryRefreshRequest,
    ResolvedSelection,
)
from utteran_contracts.editing import EditingSession
from utteran_contracts.jobs import (
    ADAPTER_VERSION,
    PROMPT_COMPILER_VERSION,
    BlockReason,
    FallbackSpec,
    InputIdentity,
    JobSnapshot,
    JobSubmit,
    RerunRequest,
    Resolution,
    ResolvedFileView,
    ResolvedSpec,
    spec_view,
)
from utteran_contracts.preview import Provenance, ValidatedFile
from utteran_contracts.settings import Issue, Policy, Preset, ProcessingSettings
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.job_repository import (
    MAX_BATCH,
    AcceptedBatch,
    AcceptedJob,
    JobControlStore,
)
from utteran_control.store import IdempotencyScope, canonical
from utteran_settings.errors import SettingsError
from utteran_settings.inheritance import preview, safe_contract_issues
from utteran_settings.repository import Repository

TOKEN_TTL = timedelta(minutes=5)
MAX_LIVE_TOKENS = 256
HASH_BLOCK = 1024 * 1024
# Recipes whose execution the B3 worker implements (faster-whisper, local model, no
# diarization, single file). Everything else is declaratively unsupported in B7.
WORKER_RECIPES = frozenset({"fw-cpu", "fw-cuda"})
PROFILES = ("cpu", "cuda", "intel", "vulkan")
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT

Clock = Callable[[], datetime]


class AdmissionError(Exception):
    """Fixed code + JSON-pointer issues. Never carries a path, prompt or raw error."""

    def __init__(self, code: str, status: int, issues: Iterable[Issue] = ()) -> None:
        self.code = code
        self.status = status
        self.issues = tuple(issues)
        super().__init__(code)


def _issue(path: str, code: str) -> Issue:
    return Issue(path=path, code=code, message_key=f"jobs.{code}")


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _system_clock() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- input identity


def _network_path(text: str) -> bool:
    if os.name == "nt":
        drive, _ = ntpath.splitdrive(text)
        return text.startswith(("\\\\", "//")) or drive.startswith(("\\\\", "//"))
    return text.startswith("//")


def _checked_path(text: str, pointer: str, *, kind: Literal["file", "dir"]) -> Path:
    prefix = "input" if kind == "file" else "output_dir"
    if "\x00" in text or not os.path.isabs(text):
        raise AdmissionError("admission_invalid", 422, [_issue(pointer, f"{prefix}_path_invalid")])
    if _network_path(text):
        # Network shares cannot provide a trustworthy point-in-time identity here.
        raise AdmissionError("admission_invalid", 422, [_issue(pointer, f"{prefix}_network")])
    normalized = os.path.normpath(text)
    path = Path(normalized)
    # Any symlink / junction / reparse component: refuse instead of following it. Each
    # component is lstat-ed (realpath comparison would misfire on Windows 8.3 names).
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except OSError:
            raise AdmissionError(
                "admission_invalid", 422, [_issue(pointer, f"{prefix}_unavailable")]
            ) from None
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise AdmissionError("admission_invalid", 422, [_issue(pointer, f"{prefix}_link")])
    return path


def _stat(path: Path, pointer: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError:
        raise AdmissionError(
            "admission_invalid", 422, [_issue(pointer, "input_unavailable")]
        ) from None
    if getattr(info, "st_file_attributes", 0) & _REPARSE or stat.S_ISLNK(info.st_mode):
        raise AdmissionError("admission_invalid", 422, [_issue(pointer, "input_link")])
    if not stat.S_ISREG(info.st_mode):
        raise AdmissionError("admission_invalid", 422, [_issue(pointer, "input_not_file")])
    if info.st_size <= 0:
        raise AdmissionError("admission_invalid", 422, [_issue(pointer, "input_empty")])
    return info


def capture_input(text: str, pointer: str = "/input_path") -> InputIdentity:
    """Normalized path + stat identity + full content SHA-256, re-statted after hashing.

    inode/mtime alone are never treated as proof of identity; the content digest is.
    """
    path = _checked_path(text, pointer, kind="file")
    before = _stat(path, pointer)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(HASH_BLOCK), b""):
                digest.update(block)
    except OSError:
        raise AdmissionError(
            "admission_invalid", 422, [_issue(pointer, "input_unavailable")]
        ) from None
    after = _stat(path, pointer)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, name) != getattr(after, name) for name in fields):
        raise AdmissionError("admission_invalid", 422, [_issue(pointer, "input_changed")])
    return InputIdentity(
        path=str(path),
        platform="nt" if os.name == "nt" else "posix",
        device=max(after.st_dev, 0),
        inode=max(after.st_ino, 0),
        size=after.st_size,
        mtime_ns=max(after.st_mtime_ns, 0),
        ctime_ns=max(after.st_ctime_ns, 0),
        sha256=digest.hexdigest(),
    )


def check_output_dir(text: str, pointer: str = "/output_dir") -> None:
    path = _checked_path(text, pointer, kind="dir")
    if not path.is_dir():
        raise AdmissionError("admission_invalid", 422, [_issue(pointer, "output_dir_unavailable")])


# --------------------------------------------------------------------------- resolution


class ResolutionProvider(Protocol):
    """Profile-side refresh+resolve. Implementations return private BridgeResolution data."""

    def resolve(
        self, settings: ProcessingSettings, policy: Policy, *, revision: int
    ) -> ProviderResult: ...


@dataclass(frozen=True)
class ProviderResult:
    reason_code: str
    selection: ResolvedSelection | None
    worker_models: Mapping[str, str] = field(default_factory=dict)


class BridgeResolutionProvider:
    """Ask each allowed, installed profile in order; first non-empty selection wins.

    B4 inventories are per profile, so no cross-profile ranking is claimed.
    """

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter

    def resolve(
        self, settings: ProcessingSettings, policy: Policy, *, revision: int
    ) -> ProviderResult:
        profiles = [p for p in (policy.allowed_profile_ids or PROFILES) if p in PROFILES]
        last = "profile_missing"
        for profile in profiles:
            result = self.adapter.resolve(
                InventoryRefreshRequest(
                    schema_version=1,
                    profile_id=profile,  # type: ignore[arg-type]
                    settings=settings,
                    execution_path="worker",
                    revision=revision,
                ),
                policy,
            )
            if result.selection is not None and result.selection.selected is not None:
                return ProviderResult("supported", result.selection, dict(result.worker_models))
            if result.selection is not None:
                last = result.selection.error or "no_runnable_candidate"
            elif result.reason_code != "profile_missing":
                last = result.reason_code
        return ProviderResult(last, None)


def candidate_fingerprint(candidate: CapabilityCandidate) -> str:
    """Stable digest over identity, route, readiness and evidence fingerprints.

    Observation times are excluded (they change on every refresh); any identity,
    model, profile, runtime or evidence change produces a different value.
    """
    return _sha(
        {
            "id": candidate.id,
            "identity": candidate.deployment.identity.to_wire(),
            "route": candidate.requested_route.to_wire(),
            "runnable": candidate.runnable,
            "observations": sorted(
                (o.kind, o.state, o.source, o.reason_code, o.fingerprint or "")
                for o in candidate.observations
            ),
        }
    )


def _worker_issues(
    candidate: CapabilityCandidate, settings: ProcessingSettings, models: Mapping[str, str]
) -> list[str]:
    identity = candidate.deployment.identity
    issues = []
    if identity.recipe_id not in WORKER_RECIPES:
        issues.append("worker_backend_unsupported")
    if identity.execution_path != "worker":
        issues.append("worker_execution_path")
    if settings.diarization.enabled:
        issues.append("worker_diarization_unsupported")
    if settings.asr.glossary:
        issues.append("worker_glossary_unsupported")
    if settings.asr.extra_arguments is not None:
        issues.append("worker_arguments_unsupported")
    if not identity.local_complete_model or candidate.id not in models:
        issues.append("worker_model_not_local")
    if candidate.runnable != "ready":
        issues.append("candidate_not_ready")
    return issues


def _fallbacks(selection: ResolvedSelection) -> tuple[FallbackSpec, ...]:
    result = []
    for candidate in selection.preapproved:
        identity = candidate.deployment.identity
        result.append(
            FallbackSpec(
                candidate_id=candidate.id,
                candidate_fingerprint=candidate_fingerprint(candidate),
                recipe_id=identity.recipe_id,
                profile_id=identity.profile_id,  # type: ignore[arg-type]
                profile_generation=identity.profile_generation,
                model_id=identity.model_id,
                model_revision=identity.model_revision,
                backend="faster-whisper"
                if identity.model_family == "ctranslate2"
                else "whisper-cpp"
                if identity.model_family == "ggml"
                else "openvino-genai",
                requested_route=candidate.requested_route,
                device=identity.device,
                compute_type=identity.compute_type,
                adapter_version="b4.1",
                runtime_fingerprint=identity.runtime_fingerprint,
            )
        )
    return tuple(result)


def build_spec(
    selection: ResolvedSelection,
    settings: ProcessingSettings,
    policy: Policy,
    models: Mapping[str, str],
    warnings: tuple[Issue, ...],
) -> ResolvedSpec:
    candidate = selection.selected
    assert candidate is not None
    identity = candidate.deployment.identity
    return ResolvedSpec(
        schema_version=1,
        candidate_id=candidate.id,
        candidate_fingerprint=candidate_fingerprint(candidate),
        recipe_id=identity.recipe_id,
        profile_id=identity.profile_id,  # type: ignore[arg-type]
        profile_generation=identity.profile_generation,
        profile_fingerprint=identity.profile_fingerprint,
        model_id=identity.model_id,
        model_revision=identity.model_revision,
        model_files_fingerprint=identity.model_files_fingerprint,
        backend="faster-whisper",
        requested_route=candidate.requested_route,
        device=identity.device,
        device_index=identity.device_index,
        compute_type=identity.compute_type,
        diarization_device=None,
        settings=settings,
        adapter_version=ADAPTER_VERSION,  # type: ignore[arg-type]
        prompt_compiler_version=PROMPT_COMPILER_VERSION,  # type: ignore[arg-type]
        inventory_revision=selection.inventory_revision,
        policy_ref={"id": policy.id, "revision": policy.revision},  # type: ignore[arg-type]
        reason_codes=selection.reason_codes,
        confidence=selection.confidence,
        evidence_kinds=selection.evidence_kinds,
        warnings=warnings,
        fallback_specs=_fallbacks(selection),
        runtime_fingerprint=identity.runtime_fingerprint,
        worker_support="supported",
        worker_model_dir=models[candidate.id],
    )


# --------------------------------------------------------------------------- tokens


@dataclass(frozen=True)
class _File:
    client_file_id: str
    identity: InputIdentity
    provenance: tuple[Provenance, ...]
    spec: ResolvedSpec


@dataclass
class _Record:
    resolution_id: str
    expires_at: datetime
    binding: str
    session_digest: str
    preset: Preset
    policy: Policy
    files: tuple[_File, ...]
    state: Literal["issued", "reserved", "consumed"] = "issued"


class ResolutionTokens:
    """Process-local, HMAC-protected, single-use tokens. Restart invalidates all of them."""

    def __init__(self, *, clock: Clock | None = None, ttl: timedelta = TOKEN_TTL) -> None:
        self._key = secrets.token_bytes(32)
        self._clock = clock or _system_clock
        self._ttl = ttl
        self._lock = threading.Lock()
        self._records: dict[str, _Record] = {}

    @staticmethod
    def binding(session: str) -> str:
        return hashlib.sha256(b"utteran-session:" + session.encode()).hexdigest()

    def _mac(self, resolution_id: str, expiry: int, binding: str) -> str:
        message = f"v1.{resolution_id}.{expiry}.{binding}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def issue(self, record: _Record) -> tuple[str, datetime]:
        now = self._clock()
        expires = now + self._ttl
        record.expires_at = expires
        expiry = int(expires.timestamp() * 1000)
        with self._lock:
            for key in [k for k, v in self._records.items() if v.expires_at <= now]:
                del self._records[key]
            if len(self._records) >= MAX_LIVE_TOKENS:
                raise AdmissionError("resolution_capacity", 503)
            self._records[record.resolution_id] = record
        mac = self._mac(record.resolution_id, expiry, record.binding)
        token = f"v1.{record.resolution_id[4:]}.{expiry:013d}.{mac}"
        return token, expires

    def reserve(self, token: str, binding: str, session_digest: str) -> _Record:
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != "v1" or not parts[2].isdigit():
            raise AdmissionError("resolution_invalid", 409)
        resolution_id, expiry = "res_" + parts[1], int(parts[2])
        if not hmac.compare_digest(parts[3], self._mac(resolution_id, expiry, binding)):
            raise AdmissionError("resolution_invalid", 409)  # forged, or another session
        with self._lock:
            record = self._records.get(resolution_id)
            if record is None or int(record.expires_at.timestamp() * 1000) != expiry:
                raise AdmissionError("resolution_invalid", 409)
            if self._clock() >= record.expires_at:
                del self._records[resolution_id]
                raise AdmissionError("resolution_expired", 409)
            if record.state != "issued":
                raise AdmissionError("resolution_consumed", 409)
            if not hmac.compare_digest(record.session_digest, session_digest):
                raise AdmissionError(
                    "resolution_stale", 409, [_issue("/editing_session", "editing_changed")]
                )
            record.state = "reserved"
            return record

    def release(self, record: _Record) -> None:
        with self._lock:
            if record.state == "reserved":
                record.state = "issued"

    def consume(self, record: _Record) -> None:
        with self._lock:
            record.state = "consumed"  # kept until expiry so reuse reports "consumed"


# --------------------------------------------------------------------------- admission


class JobAdmission:
    def __init__(
        self,
        *,
        store: JobControlStore,
        settings_path: Path,
        provider: ResolutionProvider,
        tokens: ResolutionTokens | None = None,
        clock: Clock | None = None,
        max_files: int = MAX_BATCH,
    ) -> None:
        self.store = store
        self.settings_path = settings_path
        self.provider = provider
        self.clock = clock or _system_clock
        self.tokens = tokens or ResolutionTokens(clock=self.clock)
        self.max_files = max_files
        self._revision = 0
        self._lock = threading.Lock()
        self._resolution_replays: dict[str, tuple[str, Resolution, datetime]] = {}

    def _next_revision(self) -> int:
        with self._lock:
            self._revision += 1
            return self._revision

    # -------------------------------------------------------------- shared checks

    def _references(self, session: EditingSession) -> tuple[Preset, Policy]:
        try:
            repository = Repository(self.settings_path)
            preset = repository.get("presets", session.preset_ref.id, session.preset_ref.revision)
            policy = repository.get(
                "execution-policies", session.policy_ref.id, session.policy_ref.revision
            )
        except SettingsError as exc:
            if exc.status == 404:
                raise AdmissionError(
                    "admission_invalid", 422, [_issue("/preset_ref", "reference_unresolved")]
                ) from None
            raise AdmissionError("settings_store_unavailable", 503) from None
        if not isinstance(preset, Preset) or not isinstance(policy, Policy):
            raise AdmissionError(
                "admission_invalid", 422, [_issue("/policy_ref", "reference_unresolved")]
            )
        if (
            preset.revision != session.preset_ref.revision
            or policy.revision != session.policy_ref.revision
        ):
            raise AdmissionError(
                "admission_invalid", 422, [_issue("/preset_ref", "reference_unresolved")]
            )
        return preset, policy

    def _validated(
        self, session: EditingSession, preset: Preset, policy: Policy
    ) -> tuple[ValidatedFile, ...]:
        try:
            result = preview(session, preset, policy)
        except SettingsError as exc:
            raise AdmissionError("admission_invalid", 422, exc.issues) from None
        issues = [
            _issue(f"/files/{index}" + detail.path, detail.code)
            for index, file in enumerate(result.files)
            for detail in file.issues
        ]
        for index, file in enumerate(result.files):
            if file.worker_support == "unsupported":
                issues.extend(
                    _issue(f"/files/{index}" + w.path, "worker_unsupported")
                    for w in file.warnings
                    if w.code == "worker_unsupported"
                )
        if issues:
            raise AdmissionError("admission_invalid", 422, issues)
        return result.files

    def _inputs(self, session: EditingSession) -> list[InputIdentity]:
        identities: list[InputIdentity] = []
        issues: list[Issue] = []
        for index, file in enumerate(session.files):
            try:
                identities.append(capture_input(file.input_path, f"/files/{index}/input_path"))
                check_output_dir(file.output_dir, f"/files/{index}/output_dir")
            except AdmissionError as exc:
                issues.extend(exc.issues)
        if issues:
            raise AdmissionError("admission_invalid", 422, issues)
        return identities

    def _resolve_all(
        self, settings: Sequence[ProcessingSettings], policy: Policy
    ) -> list[ProviderResult]:
        cache: dict[str, ProviderResult] = {}
        out = []
        for item in settings:
            key = _sha(item.to_wire())
            if key not in cache:
                try:
                    cache[key] = self.provider.resolve(item, policy, revision=self._next_revision())
                except Exception:
                    cache[key] = ProviderResult("resolver_unavailable", None)
            out.append(cache[key])
        return out

    @staticmethod
    def _parse_session(body: Any) -> EditingSession:
        try:
            return EditingSession.model_validate(body)
        except ValidationError as exc:
            raise AdmissionError(
                "contract_validation_failed", 422, safe_contract_issues(exc)
            ) from None

    # -------------------------------------------------------------- resolution

    def resolve(self, key: str, body: Any, *, session: str) -> tuple[int, Resolution, bool]:
        """POST /api/v1/execution-resolutions. Replays (in-memory, token lifetime) by key."""
        digest = self.store.request_digest(
            {"resolve": body, "binding": ResolutionTokens.binding(session)}
        )
        now = self.clock()
        with self._lock:
            for stale in [k for k, v in self._resolution_replays.items() if v[2] <= now]:
                del self._resolution_replays[stale]
            known = self._resolution_replays.get(key)
        if known is not None:
            if not hmac.compare_digest(known[0], digest):
                raise AdmissionError("idempotency_conflict", 409)
            return 201, known[1], True
        editing = self._parse_session(body)
        if len(editing.files) > self.max_files:
            raise AdmissionError("admission_invalid", 422, [_issue("/files", "batch_too_large")])
        preset, policy = self._references(editing)
        validated = self._validated(editing, preset, policy)
        identities = self._inputs(editing)
        effective = [file.effective_settings for file in validated]
        assert all(item is not None for item in effective)
        results = self._resolve_all(effective, policy)  # type: ignore[arg-type]
        issues: list[Issue] = []
        files: list[_File] = []
        for index, (file, check, identity, result) in enumerate(
            zip(editing.files, validated, identities, results, strict=True)
        ):
            settings = check.effective_settings
            assert settings is not None
            selection = result.selection
            if selection is None or selection.selected is None:
                issues.append(
                    _issue(
                        f"/files/{index}",
                        result.reason_code
                        if selection is None
                        else selection.error or "no_runnable_candidate",
                    )
                )
                continue
            problems = _worker_issues(selection.selected, settings, result.worker_models)
            if problems:
                issues.extend(_issue(f"/files/{index}", code) for code in problems)
                continue
            files.append(
                _File(
                    client_file_id=file.client_file_id,
                    identity=identity,
                    provenance=check.provenance,
                    spec=build_spec(
                        selection, settings, policy, result.worker_models, check.warnings
                    ),
                )
            )
        if issues:
            raise AdmissionError("execution_unavailable", 409, issues)
        record = _Record(
            resolution_id="res_" + secrets.token_hex(16),
            expires_at=now,
            binding=ResolutionTokens.binding(session),
            session_digest=_sha(editing.to_wire()),
            preset=preset,
            policy=policy,
            files=tuple(files),
        )
        token, expires = self.tokens.issue(record)
        resolution = Resolution(
            schema_version=1,
            id=record.resolution_id,
            token=token,
            expires_at=expires,
            files=tuple(
                ResolvedFileView(
                    client_file_id=item.client_file_id,
                    input_identity=self.store.public_input_id(item.identity),
                    spec=spec_view(item.spec),
                )
                for item in files
            ),
        )
        with self._lock:
            self._resolution_replays[key] = (digest, resolution, expires)
        return 201, resolution, False

    # -------------------------------------------------------------- freeze

    def _freeze(self, submit: JobSubmit, session: str) -> tuple[list[JobSnapshot], _Record]:
        editing = submit.editing_session
        record = self.tokens.reserve(
            submit.resolution_token, ResolutionTokens.binding(session), _sha(editing.to_wire())
        )
        try:
            stale: list[Issue] = []
            preset, policy = self._references(editing)
            if _sha(preset.to_wire()) != _sha(record.preset.to_wire()) or _sha(
                policy.to_wire()
            ) != _sha(record.policy.to_wire()):
                raise AdmissionError(
                    "resolution_stale", 409, [_issue("/preset_ref", "reference_changed")]
                )
            validated = self._validated(editing, preset, policy)
            try:
                identities = self._inputs(editing)
            except AdmissionError as exc:
                raise AdmissionError("resolution_stale", 409, exc.issues) from None
            for index, (frozen, check, identity) in enumerate(
                zip(record.files, validated, identities, strict=True)
            ):
                if frozen.identity != identity:
                    stale.append(_issue(f"/files/{index}/input_path", "input_changed"))
                if check.effective_settings != frozen.spec.settings:
                    stale.append(_issue(f"/files/{index}", "settings_changed"))
            if stale:
                raise AdmissionError("resolution_stale", 409, stale)
            # Re-observe the environment; never re-select: the frozen candidate must still
            # be ready with identical fingerprints, else the whole submission is refused.
            results = self._resolve_all([f.spec.settings for f in record.files], record.policy)
            for index, (frozen, result) in enumerate(zip(record.files, results, strict=True)):
                code = _still_ready(frozen.spec, result)
                if code is not None:
                    stale.append(_issue(f"/files/{index}", code))
            if stale:
                raise AdmissionError("resolution_stale", 409, stale)
            created = self.clock()
            snapshots = [
                JobSnapshot(
                    schema_version=1,
                    created_at=created,
                    preset_ref=editing.preset_ref,
                    policy_ref=editing.policy_ref,
                    preset_digest=_sha(record.preset.to_wire()),
                    policy_digest=_sha(record.policy.to_wire()),
                    policy=record.policy,
                    common_overrides=editing.common_overrides,
                    file=file,
                    input_identity=frozen.identity,
                    provenance=frozen.provenance,
                    resolved=frozen.spec,
                    resume_mode=submit.resume_mode,
                )
                for file, frozen in zip(editing.files, record.files, strict=True)
            ]
            return snapshots, record
        except BaseException:
            self.tokens.release(record)
            raise

    def submit(self, scope: IdempotencyScope, body: Any, *, session: str) -> AcceptedBatch:
        digest = self.store.request_digest(body)
        replay = self.store.lookup_batch(scope, digest)  # replay precedes every other check
        if replay is not None:
            return replay
        try:
            submit = JobSubmit.model_validate(body)
        except ValidationError as exc:
            raise AdmissionError(
                "contract_validation_failed", 422, safe_contract_issues(exc)
            ) from None
        snapshots, record = self._freeze(submit, session)
        try:
            accepted = self.store.accept_batch(scope, digest, snapshots)
        except BaseException:
            self.tokens.release(record)
            raise
        self.tokens.consume(record)
        return accepted

    def rerun(
        self,
        job_id: str,
        expected_revision: int,
        scope: IdempotencyScope,
        body: Any,
        *,
        session: str,
    ) -> AcceptedBatch:
        digest = self.store.request_digest(
            {"rerun": job_id, "expected": expected_revision, "body": body}
        )
        replay = self.store.lookup_batch(scope, digest)
        if replay is not None:
            return replay
        try:
            request = RerunRequest.model_validate(body)
        except ValidationError as exc:
            raise AdmissionError(
                "contract_validation_failed", 422, safe_contract_issues(exc)
            ) from None
        source = self.store.get_job(job_id)
        if source.revision != expected_revision:
            raise ControlError(ErrorCode.REVISION_CONFLICT)
        if len(request.submission.editing_session.files) != 1:
            raise AdmissionError(
                "admission_invalid",
                422,
                [_issue("/submission/editing_session/files", "rerun_single_file")],
            )
        snapshots, record = self._freeze(request.submission, session)
        try:
            accepted = self.store.accept_batch(scope, digest, snapshots, rerun_of=job_id)
        except BaseException:
            self.tokens.release(record)
            raise
        self.tokens.consume(record)
        return accepted

    # -------------------------------------------------------------- existing snapshots

    def retry(self, job_id: str, expected_revision: int, scope: IdempotencyScope) -> AcceptedJob:
        """Same snapshot, new job. Refused (409) when the input no longer matches."""
        digest = self.store.request_digest(
            {"op": "retry", "job": job_id, "expected_revision": expected_revision}
        )
        replay = self.store.lookup_job(scope, digest)
        if replay is not None:
            return replay
        snapshot = self.store.get_snapshot(job_id)
        try:
            identity = capture_input(snapshot.input_identity.path)
        except AdmissionError:
            raise AdmissionError(
                "input_changed", 409, [_issue("/input_path", "input_unavailable")]
            ) from None
        if identity != snapshot.input_identity:
            raise AdmissionError("input_changed", 409, [_issue("/input_path", "input_changed")])
        return self.store.retry_job(job_id, expected_revision, scope)

    def prestart_check(self, snapshot: JobSnapshot) -> BlockReason | None:
        """Re-check the *frozen* snapshot only. Never picks a new candidate."""
        try:
            identity = capture_input(snapshot.input_identity.path)
        except AdmissionError as exc:
            changed = any(i.code == "input_changed" for i in exc.issues)
            return "input_changed" if changed else "input_unavailable"
        if identity != snapshot.input_identity:
            return "input_changed"
        try:
            check_output_dir(snapshot.file.output_dir)
        except AdmissionError:
            return "output_unavailable"
        try:
            result = self.provider.resolve(
                snapshot.resolved.settings, snapshot.policy, revision=self._next_revision()
            )
        except Exception:
            return "resolver_unavailable"
        code = _still_ready(snapshot.resolved, result)
        if code is None:
            return None
        if code == "resolver_unavailable":
            return "resolver_unavailable"
        return "candidate_changed" if code == "candidate_changed" else "candidate_unavailable"

    def revalidate(
        self, job_id: str, expected_revision: int, scope: IdempotencyScope
    ) -> AcceptedJob:
        digest = self.store.request_digest(
            {"op": "revalidate", "job": job_id, "expected_revision": expected_revision}
        )
        replay = self.store.lookup_job(scope, digest)
        if replay is not None:
            return replay
        job = self.store.get_job(job_id)
        if job.status != "blocked":
            raise ControlError(ErrorCode.INVALID_TRANSITION)
        if job.revision != expected_revision:
            raise ControlError(ErrorCode.REVISION_CONFLICT)
        failure = self.prestart_check(self.store.get_snapshot(job_id))
        try:
            return self.store.revalidated(job_id, expected_revision, scope, failure=failure)
        except ControlError as exc:
            if exc.code is ErrorCode.NOT_DISPATCHABLE:
                raise AdmissionError(
                    "revalidation_failed", 409, [_issue("", failure or "blocked")]
                ) from None
            raise


def _still_ready(spec: ResolvedSpec, result: ProviderResult) -> str | None:
    selection = result.selection
    if selection is None:
        return (
            "resolver_unavailable"
            if result.reason_code in {"resolver_unavailable", "probe_timeout", "bridge_unavailable"}
            else "candidate_unavailable"
        )
    for alternative in selection.alternatives:
        candidate = alternative.candidate
        if candidate.id != spec.candidate_id:
            continue
        if not alternative.eligible or candidate.runnable != "ready":
            return "candidate_unavailable"
        if candidate_fingerprint(candidate) != spec.candidate_fingerprint:
            return "candidate_changed"
        if result.worker_models.get(candidate.id) != spec.worker_model_dir:
            return "candidate_changed"
        return None
    return "candidate_unavailable"
