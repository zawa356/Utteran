"""F-A: execution readiness of one settings value, separate from setup success.

``POST /api/v1/readiness-checks`` answers "would B7 admit a job with these effective
settings under this policy right now?" with the same metadata-only bridge ``resolve`` B7
admission uses (no inference, no smoke, no download, no write). It does not issue a
resolution token and never changes evidence. A setup operation that succeeded is *not*
readiness: only a B4 ``ready`` candidate the B3 worker can execute is.

The response is an allowlist of fixed codes and catalog identifiers (no path, prompt,
digest or child output).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ValidationError

from utteran_contracts.base import VersionedContract
from utteran_contracts.capabilities import CapabilityCandidate
from utteran_contracts.settings import Policy, ProcessingSettings, Ref
from utteran_contracts.setup import NATIVE_VERIFICATION_RECIPES, VERIFICATION_RECIPES
from utteran_gui.job_admission import AdmissionError, JobAdmission, ProviderResult, _worker_issues
from utteran_settings.errors import SettingsError
from utteran_settings.inheritance import safe_contract_issues
from utteran_settings.repository import Repository

# Candidate issues that a (re-)verification smoke can resolve: missing, expired or
# superseded evidence. Identity/VRAM gaps of CUDA are *not* in this set (a smoke cannot
# fill them in this version), nor are missing models or profiles (setup does that).
# (Expired, pending, failed or out-of-scope evidence is never adopted: it reads as missing.)
VERIFIABLE = frozenset({"smoke_missing"})
# Issue kinds a smoke cannot fix (install, model, settings or policy problems come first).
BLOCKING_KINDS = frozenset(
    {"venv", "model", "native", "settings", "software_support", "execution_path", "policy"}
)


class ReadinessRequest(VersionedContract):
    settings: ProcessingSettings
    policy_ref: Ref


def _candidate(
    candidate: CapabilityCandidate, settings: ProcessingSettings, models: Any
) -> dict[str, Any]:
    identity = candidate.deployment.identity
    issues = sorted({f"{issue.kind}:{issue.code}" for issue in candidate.issues})
    # Readiness and the model directory map exist only for eligible candidates; for the
    # rest those two codes restate "not ready" (a missing model is its own "model" issue).
    worker = sorted(
        set(_worker_issues(candidate, settings, models))
        - {"candidate_not_ready", "worker_model_not_local"}
    )
    return {
        "recipe_id": identity.recipe_id,
        "profile_id": identity.profile_id,
        "model_id": identity.model_id,
        "device": identity.device,
        "device_index": identity.device_index,
        "compute_type": identity.compute_type,
        "runnable": candidate.runnable,
        "issues": issues,
        "worker_issues": worker,
        "verifiable": (
            identity.recipe_id
            in (*VERIFICATION_RECIPES.values(), *NATIVE_VERIFICATION_RECIPES.values())
            and candidate.runnable != "ready"
            and any(issue.code in VERIFIABLE for issue in candidate.issues)
            and not any(issue.kind in BLOCKING_KINDS for issue in candidate.issues)
            and not worker
        ),
    }


def readiness_view(result: ProviderResult, settings: ProcessingSettings) -> dict[str, Any]:
    selection = result.selection
    candidates: list[dict[str, Any]] = []
    state: Literal["ready", "not_ready"] = "not_ready"
    reasons: list[str] = []
    if selection is None:
        reasons.append(result.reason_code)
        seen_ids: set[str] = set()
        for unselected in result.unselected:
            for alternative in unselected.alternatives:
                if alternative.candidate.id not in seen_ids:
                    seen_ids.add(alternative.candidate.id)
                    candidates.append(_candidate(alternative.candidate, settings, {}))
    else:
        seen: set[str] = set()
        chosen = [selection.selected] if selection.selected is not None else []
        for candidate in [*chosen, *(alt.candidate for alt in selection.alternatives)]:
            if candidate.id in seen:
                continue
            seen.add(candidate.id)
            candidates.append(_candidate(candidate, settings, result.worker_models))
        if selection.selected is not None:
            problems = _worker_issues(selection.selected, settings, result.worker_models)
            if problems:
                reasons.extend(problems)
            else:
                state = "ready"
                reasons.extend(issue.code for issue in selection.selected.issues)
        else:
            reasons.append(selection.error or "no_runnable_candidate")
    return {
        "schema_version": 1,
        "state": state,
        "reason_codes": sorted(set(reasons)),
        "selected": candidates[0] if state == "ready" else None,
        "candidates": candidates[:16],
    }


def check_readiness(admission: JobAdmission, body: Any) -> dict[str, Any]:
    try:
        request = ReadinessRequest.model_validate(body)
    except ValidationError as exc:
        raise AdmissionError("contract_validation_failed", 422, safe_contract_issues(exc)) from None
    try:
        policy = Repository(admission.settings_path).get(
            "execution-policies", request.policy_ref.id, request.policy_ref.revision
        )
    except SettingsError as exc:
        if exc.status == 404:
            raise AdmissionError("policy_unresolved", 422) from None
        raise AdmissionError("settings_store_unavailable", 503) from None
    if not isinstance(policy, Policy) or policy.revision != request.policy_ref.revision:
        raise AdmissionError("policy_unresolved", 422)
    try:
        result = admission.provider.resolve(
            request.settings, policy, revision=admission._next_revision()
        )
    except Exception:
        result = ProviderResult("resolver_unavailable", None)
    return readiness_view(result, request.settings)
