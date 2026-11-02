"""Pure evidence normalization and readiness; no clocks, probes, or I/O."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from utteran.asr.registry import NON_SPACE_LANGUAGES
from utteran.capabilities.recipes import get_recipe
from utteran_contracts import ProcessingSettings
from utteran_contracts.capabilities import (
    CapabilityCandidate,
    CapabilityObservation,
    DeploymentInventory,
    ExecutionIdentity,
    ExecutionPath,
    InventoryExclusion,
    InventorySnapshot,
    ObservationKind,
    ReasonCode,
    SelectionIssue,
)

_NON_SPACE = NON_SPACE_LANGUAGES | {"auto", ""}


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
    ).hexdigest()


def evidence_fingerprint(identity: ExecutionIdentity, settings: ProcessingSettings) -> str:
    """Private evidence scope, including all settings and component identities."""
    return digest({"identity": identity.to_wire(), "conditions": settings.to_wire()})


def _check_time(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an explicit aware evaluation time is required")


def normalize_observation(
    observation: CapabilityObservation,
    fingerprint: str,
    now: datetime,
) -> CapabilityObservation:
    """Stale failures also become unknown; never relabel an old result as current."""
    _check_time(now)
    code: ReasonCode | None = None
    if observation.state == "not_applicable":
        return observation
    if observation.fingerprint != fingerprint:
        code = "fingerprint_changed"
    elif observation.observed_at is None:
        code = "observation_time_missing"
    elif observation.observed_at > now:
        code = "observation_from_future"
    elif observation.expires_at is None:
        code = "cache_stale"
    elif observation.expires_at <= now:
        code = "expired"
    if code is None:
        return observation
    return observation.model_copy(
        update=dict(
            state="unknown",
            reason_code=code,
            initialized_route=None,
            encoder_observed="unknown",
            decoder_observed="unknown",
        )
    )


def _settings_issues(
    identity: ExecutionIdentity,
    settings: ProcessingSettings,
) -> list[SelectionIssue]:
    recipe = get_recipe(identity.recipe_id)
    asr = settings.asr
    codes: list[ReasonCode] = []
    if identity.english_only and asr.language.strip().casefold() not in {"en", "auto"}:
        codes.append("model_language_conflict")
    if asr.backend not in {"auto", recipe.backend}:
        codes.append("backend_conflict")
    if asr.model not in {"auto", identity.model_id}:
        codes.append("model_conflict")
    active = {
        "faster-whisper": "faster_whisper",
        "whisper-cpp": "whisper_cpp",
        "openvino-genai": "openvino_genai",
    }[recipe.backend]
    if any(
        getattr(asr, name) is not None
        for name in (
            "faster_whisper",
            "whisper_cpp",
            "openvino_genai",
        )
        if name != active
    ):
        codes.append("inactive_backend_settings")
    if asr.faster_whisper is not None and asr.faster_whisper.compute_type not in {
        None,
        "auto",
        identity.compute_type,
    }:
        codes.append("compute_conflict")
    if asr.glossary or asr.extra_arguments is not None:
        codes.append("unsupported_setting")
    if recipe.backend == "faster-whisper" and (
        asr.word_timestamps == "never" or asr.vad.threshold is not None
    ):
        codes.append("unsupported_setting")
    if recipe.backend == "whisper-cpp":
        native = asr.whisper_cpp
        if asr.condition_on_previous_text or (
            native is not None
            and (
                (native.vad is not None and native.vad != asr.vad.enabled)
                or (
                    asr.vad.threshold is not None
                    and native.vad_threshold is not None
                    and native.vad_threshold != asr.vad.threshold
                )
            )
        ):
            codes.append("unsupported_setting")
    if recipe.backend == "openvino-genai" and (
        (asr.word_timestamps == "always" and asr.language.strip().casefold() in _NON_SPACE)
        or asr.condition_on_previous_text
        or (asr.openvino_genai is not None and asr.openvino_genai.max_new_tokens is not None)
    ):
        codes.append("unsupported_setting")
    if settings.diarization.enabled:
        diar = settings.diarization
        if identity.diarization_device == "xpu" and identity.platform == "Linux":
            codes.append("diarization_platform_unsupported")
        if asr.word_timestamps == "never":
            codes.append("unsupported_setting")
        if recipe.backend == "openvino-genai" and asr.language.strip().casefold() in _NON_SPACE:
            codes.append("genai_diarization_language")
        if diar.device_preference not in {"auto", identity.diarization_device}:
            codes.append("diarization_device_conflict")
        if diar.model != identity.diarization_model:
            codes.append("diarization_model_conflict")
    return [SelectionIssue(kind="settings", code=code) for code in dict.fromkeys(codes)]


def _route_conflicts(observation: CapabilityObservation, identity: ExecutionIdentity) -> bool:
    requested = get_recipe(identity.recipe_id).requested_route
    initialized = observation.initialized_route
    if initialized is not None and initialized.native_variant != requested.native_variant:
        return True
    for component in ("encoder", "decoder"):
        expected = getattr(requested, component)
        if expected == "backend-default":
            continue
        if expected == "selected-device":
            expected = identity.device
        actual = getattr(observation, component + "_observed")
        if actual != "unknown" and actual != expected:
            return True
        if initialized is not None and getattr(initialized, component) not in {
            expected,
            getattr(requested, component),
        }:
            return True
    return False


def evaluate_candidate(
    deployment: DeploymentInventory,
    settings: ProcessingSettings,
    execution_path: ExecutionPath,
    *,
    now: datetime,
) -> CapabilityCandidate:
    _check_time(now)
    identity = deployment.identity
    recipe = get_recipe(identity.recipe_id)
    fingerprint = evidence_fingerprint(identity, settings)
    observations: list[CapabilityObservation] = []
    issues = _settings_issues(identity, settings)
    blocked = bool(issues)

    def generated(kind: ObservationKind, code: ReasonCode, state: str) -> None:
        observations.append(
            CapabilityObservation.model_validate(
                dict(
                    kind=kind,
                    state=state,
                    source="core-evaluator",
                    observed_at=now,
                    expires_at=None,
                    fingerprint=fingerprint,
                    reason_code=code,
                )
            )
        )

    support_codes: list[ReasonCode] = []
    if identity.platform not in recipe.platforms:
        support_codes.append(
            "not_observed" if identity.platform == "unknown" else "unsupported_platform"
        )
    if identity.compute_type not in recipe.compute_types:
        support_codes.append("unsupported_compute")
    if identity.device not in recipe.devices or (
        identity.device == "cpu" and identity.device_index != 0
    ):
        support_codes.append("unsupported_device")
    if identity.model_family != recipe.model_family:
        support_codes.append("model_family_mismatch")
    support_failed = any(code != "not_observed" for code in support_codes)
    generated(
        "software_support",
        support_codes[0] if support_codes else "supported",
        "failed" if support_failed else "unknown" if support_codes else "passed",
    )
    issues.extend(SelectionIssue(kind="software_support", code=code) for code in support_codes)
    blocked |= support_failed

    transport_codes: list[ReasonCode] = []
    if identity.execution_path != execution_path:
        transport_codes.append("profile_scope_mismatch")
    if execution_path == "worker":
        if not recipe.worker_supported:
            transport_codes.append("worker_backend_unsupported")
        if settings.diarization.enabled:
            transport_codes.append("worker_diarization_unsupported")
        if not identity.local_complete_model:
            transport_codes.append("worker_model_not_local")
        if identity.input_count != 1:
            transport_codes.append("worker_single_input_only")
    generated(
        "execution_path",
        transport_codes[0] if transport_codes else "supported",
        "failed" if transport_codes else "passed",
    )
    issues.extend(SelectionIssue(kind="execution_path", code=code) for code in transport_codes)
    blocked |= bool(transport_codes)

    complete = all(
        (
            identity.profile_fingerprint,
            identity.runtime_fingerprint,
            identity.hardware_fingerprint,
            identity.model_files_fingerprint,
        )
    )
    complete &= identity.model_revision != "unknown" and identity.profile_generation != "unknown"
    complete &= identity.python_abi != "unknown"
    if recipe.native_required:
        complete &= identity.native_fingerprint is not None
    if settings.diarization.enabled:
        complete &= (
            identity.diarization_fingerprint is not None
            and identity.diarization_device is not None
            and identity.diarization_device_index is not None
        )
    if not complete:
        issues.append(SelectionIssue(kind="smoke", code="evidence_scope_incomplete"))

    supplied = {item.kind: item for item in deployment.observations}
    required: tuple[ObservationKind, ...] = (
        "hardware",
        "runtime",
        "venv",
        "model",
        "native",
        "smoke",
        "memory",
        "diarization",
    )
    for kind in required:
        applicable = not (
            (kind == "native" and not recipe.native_required)
            or (kind == "diarization" and not settings.diarization.enabled)
        )
        if not applicable:
            generated(kind, "not_required", "not_applicable")
            continue
        observation = supplied.get(kind)
        if observation is None:
            generated(kind, "smoke_missing" if kind == "smoke" else "not_observed", "unknown")
            issues.append(SelectionIssue(kind=kind, code=observations[-1].reason_code))
            continue
        observation = normalize_observation(observation, fingerprint, now)

        if (
            kind == "smoke"
            and observation.state == "passed"
            and _route_conflicts(observation, identity)
        ):
            observation = observation.model_copy(
                update={
                    "state": "failed",
                    "reason_code": "smoke_route_mismatch",
                }
            )
        observations.append(observation)
        if observation.state != "passed":
            code = (
                "required_not_applicable"
                if observation.state == "not_applicable"
                else observation.reason_code
            )
            issues.append(SelectionIssue(kind=kind, code=code))
            blocked |= observation.state == "failed"

    if deployment.required_memory_bytes is None or deployment.available_memory_bytes is None:
        issues.append(SelectionIssue(kind="memory", code="memory_unknown"))
    elif deployment.required_memory_bytes > deployment.available_memory_bytes:
        issues.append(SelectionIssue(kind="memory", code="memory_insufficient"))
        blocked = True
    return CapabilityCandidate(
        schema_version=1,
        id="cand-" + digest(identity.to_wire()),
        revision=digest({"deployment": deployment.to_wire(), "conditions": fingerprint}),
        deployment=deployment,
        requested_route=recipe.requested_route,
        observations=tuple(observations),
        issues=tuple(issues),
        runnable="blocked" if blocked else "unknown" if issues else "ready",
    )


def make_inventory(
    deployments: tuple[DeploymentInventory, ...],
    settings: ProcessingSettings,
    execution_path: ExecutionPath,
    *,
    revision: int,
    now: datetime,
) -> InventorySnapshot:
    candidates = []
    exclusions = []
    for deployment in deployments:
        recipe = get_recipe(deployment.identity.recipe_id)
        conflicts: list[ReasonCode] = []
        if settings.asr.backend not in {"auto", recipe.backend}:
            conflicts.append("backend_conflict")
        if settings.asr.model not in {"auto", deployment.identity.model_id}:
            conflicts.append("model_conflict")
        if conflicts:
            exclusions.append(
                InventoryExclusion(identity=deployment.identity, reason_codes=tuple(conflicts))
            )
        else:
            candidates.append(evaluate_candidate(deployment, settings, execution_path, now=now))
    return InventorySnapshot(
        schema_version=1,
        revision=revision,
        observed_at=now,
        settings=settings,
        execution_path=execution_path,
        candidates=tuple(sorted(candidates, key=lambda item: item.id)),
        exclusions=tuple(sorted(exclusions, key=lambda item: digest(item.identity.to_wire()))),
    )
