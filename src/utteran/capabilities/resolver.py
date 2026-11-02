"""Pure deterministic policy resolution over one immutable inventory snapshot."""

from __future__ import annotations

from datetime import datetime

from utteran.capabilities.evaluator import digest, evaluate_candidate, evidence_fingerprint
from utteran_contracts import Policy
from utteran_contracts.capabilities import (
    BenchmarkEvidence,
    CapabilityCandidate,
    InventorySnapshot,
    ReasonCode,
    ResolvedSelection,
    SelectionAlternative,
)

# Explicit B4 heuristic, independent from legacy devices._auto_selection.
# This is a preference, never evidence of quality, speed, or hardware success.
_STATIC_ORDER = ("fw-cuda", "wc-vulkan", "wc-ov-vk", "wc-ov", "genai-ov", "fw-cpu", "wc-cpu")


def benchmark_key(benchmark: BenchmarkEvidence) -> str:
    data = benchmark.to_wire()
    for name in ("fingerprint", "observed_at", "expires_at", "elapsed_seconds"):
        data.pop(name)
    return digest(data)


def _valid_benchmark(
    candidate: CapabilityCandidate, inventory: InventorySnapshot, now: datetime
) -> bool:
    value = candidate.deployment.benchmark
    identity = candidate.deployment.identity
    asr = inventory.settings.asr
    threads = asr.whisper_cpp.threads if asr.whisper_cpp is not None else None
    return bool(
        value is not None
        and value.fingerprint == evidence_fingerprint(identity, inventory.settings)
        and value.observed_at <= now < value.expires_at
        and value.model_revision == identity.model_revision
        and value.precision == identity.compute_type
        and value.language == asr.language
        and value.diarization == inventory.settings.diarization.enabled
        and (asr.word_timestamps == "auto" or value.word_timestamps == asr.word_timestamps)
        and (threads is None or threads == value.threads)
    )


def resolve(inventory: InventorySnapshot, policy: Policy, *, now: datetime) -> ResolvedSelection:
    """Recheck evidence time at resolution; never perform a refresh or execute fallback."""
    alternatives: list[SelectionAlternative] = []
    eligible: list[CapabilityCandidate] = []
    for original in inventory.candidates:
        candidate = evaluate_candidate(
            original.deployment, inventory.settings, inventory.execution_path, now=now
        )
        reasons = list(dict.fromkeys(issue.code for issue in candidate.issues))
        identity = candidate.deployment.identity
        if policy.allowed_profile_ids and identity.profile_id not in policy.allowed_profile_ids:
            reasons.append("profile_not_allowed")
        memory = candidate.deployment.required_memory_bytes
        if policy.max_memory_bytes is not None:
            if memory is None:
                reasons.append("memory_unknown")
            elif memory > policy.max_memory_bytes:
                reasons.append("memory_limit")
        # No bounded execution-time evidence is supported by B4. Smoke and a
        # benchmark average cannot establish a hard deadline.
        if policy.execution_deadline_seconds is not None:
            reasons.append("deadline_unverified")
        is_eligible = candidate.runnable == "ready" and not reasons
        alternatives.append(
            SelectionAlternative(
                candidate=candidate,
                eligible=is_eligible,
                reason_codes=tuple(reasons),
            )
        )
        if is_eligible:
            eligible.append(candidate)

    selected: CapabilityCandidate | None = None
    error: str | None = None
    reasons_out: list[ReasonCode] = []
    benchmark_used = False
    if policy.mode == "fixed":
        selected = next((c for c in eligible if c.id == policy.fixed_candidate_id), None)
        error = "fixed_candidate_unavailable" if selected is None else None
        reasons_out.append("fixed_candidate_unavailable" if selected is None else "fixed_selected")
    else:
        # Compare measurements only when EVERY eligible candidate has fresh,
        # matching evidence in the same group. Never compare winners of groups.
        comparable = bool(eligible) and all(_valid_benchmark(c, inventory, now) for c in eligible)
        keys = {
            benchmark_key(c.deployment.benchmark)
            for c in eligible
            if c.deployment.benchmark is not None
        }
        benchmark_used = comparable and len(keys) == 1 and policy.objective in {"speed", "balanced"}
        if benchmark_used:
            eligible.sort(
                key=lambda c: (
                    c.deployment.benchmark.elapsed_seconds
                    if c.deployment.benchmark
                    else float("inf"),
                    c.id,
                )
            )
            reasons_out.append("comparable_benchmark")
        else:
            eligible.sort(
                key=lambda c: (_STATIC_ORDER.index(c.deployment.identity.recipe_id), c.id)
            )
            reasons_out.extend(("static_heuristic", "stable_id_tiebreak"))
            if any(c.deployment.benchmark is not None for c in eligible):
                reasons_out.append("benchmark_incomparable")
        selected = eligible[0] if eligible else None
        if selected is None:
            error = "no_runnable_candidate"
            reasons_out.append("no_runnable_candidate")

    return ResolvedSelection.model_validate(
        dict(
            schema_version=1,
            requested_settings=inventory.settings,
            policy=policy,
            inventory_revision=inventory.revision,
            resolved_at=now,
            selected=selected,
            preapproved=tuple(eligible[1:])
            if policy.mode == "auto" and policy.fallback == "preapproved" and selected is not None
            else (),
            alternatives=tuple(sorted(alternatives, key=lambda a: a.candidate.id)),
            exclusions=inventory.exclusions,
            reason_codes=tuple(reasons_out),
            error=error,
            evidence_kinds=(("smoke", "benchmark") if benchmark_used else ("smoke", "static_rule"))
            if selected is not None
            else (),
            confidence="medium" if benchmark_used else "low",
        )
    )
