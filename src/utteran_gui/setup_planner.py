"""B8 read-only setup observation and the pure plan builder.

``SetupObserver.observe()`` only stats files (profile venv + manifest via
``CliAdapter.profile_info``, the managed model directories) and reads the credential
*presence* flag. It never spawns, downloads, writes or imports the inference core.
``build_plan()`` is a pure function from (request, observation) to ``PlanContent``.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from utteran_contracts.setup import (
    ASR_BACKENDS,
    EXECUTABLE_BACKENDS,
    PROFILE_BACKENDS,
    PROFILE_KINDS,
    SETUP_MODELS,
    Estimate,
    PlanContent,
    PlanRejection,
    SetupChange,
    SetupPlanRequest,
    SetupStep,
)

_STATUS: dict[str, int] = {
    "model_unknown": 422,
    "model_refs_duplicate": 422,
    "asr_model_required": 422,
    "diarization_model_mismatch": 422,
}


class PlanRejected(Exception):
    """Fixed refusal code; never carries a path or free text."""

    def __init__(self, code: PlanRejection | Literal["model_refs_duplicate"]) -> None:
        self.code = code
        self.status = _STATUS.get(code, 409)
        super().__init__(code)


@dataclass(frozen=True)
class ProfileState:
    name: str
    exists: bool
    compatible: bool | None
    reason: str | None


@dataclass(frozen=True)
class SetupObservation:
    """Point-in-time, path-free observation. ``credential_present`` is not fingerprinted."""

    platform: Literal["nt", "posix"]
    lock_sha256: str | None
    profiles: tuple[ProfileState, ...]
    models_present: tuple[tuple[str, bool], ...]
    credential_present: bool

    @property
    def fingerprint(self) -> str:
        body = {
            "platform": self.platform,
            "lock": self.lock_sha256,
            "profiles": [[p.name, p.exists, p.compatible, p.reason] for p in self.profiles],
            "models": [list(item) for item in self.models_present],
        }
        text = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()

    def profile(self, name: str) -> ProfileState:
        return next(p for p in self.profiles if p.name == name)

    def model_present(self, ref: str) -> bool:
        return dict(self.models_present).get(ref, False)

    @property
    def credentials(self) -> frozenset[str]:
        return frozenset({"hf_token"}) if self.credential_present else frozenset()


class ProfileSource(Protocol):
    """Structural type of ``CliAdapter`` used here (profile_info + repo_root)."""

    @property
    def repo_root(self) -> Path: ...

    def profile_info(self, profile: str) -> object: ...


class SetupObserver:
    def __init__(
        self,
        cli: ProfileSource,
        *,
        models_root: Path,
        credential_present: Callable[[], bool],
        platform_name: Literal["nt", "posix"] | None = None,
    ) -> None:
        self.cli = cli
        self.models_root = models_root
        self.credential_present = credential_present
        self.platform: Literal["nt", "posix"] = platform_name or (
            "nt" if os.name == "nt" else "posix"
        )

    def observe(self) -> SetupObservation:
        lock = self.cli.repo_root / "uv.lock"
        try:
            lock_sha: str | None = hashlib.sha256(lock.read_bytes()).hexdigest()
        except OSError:
            lock_sha = None
        profiles = []
        for name in PROFILE_KINDS:
            info = self.cli.profile_info(name)
            profiles.append(
                ProfileState(
                    name,
                    bool(getattr(info, "exists", False)),
                    getattr(info, "compatible", None),
                    getattr(info, "compatibility_reason", None),
                )
            )
        models = tuple(
            (ref, (self.models_root / model.backend / model.managed_component).is_dir())
            for ref, model in sorted(SETUP_MODELS.items())
        )
        try:
            present = bool(self.credential_present())
        except Exception:
            present = False  # unknown presence is treated as missing, never as present
        return SetupObservation(self.platform, lock_sha, tuple(profiles), models, present)


def _check_refs(request: SetupPlanRequest) -> None:
    refs = request.model_refs
    if len(set(refs)) != len(refs):
        raise PlanRejected("model_refs_duplicate")
    hosted = PROFILE_BACKENDS[request.profile_kind]
    for ref in refs:
        backend = ref.split(":", 1)[0]
        if ref not in SETUP_MODELS and backend not in ASR_BACKENDS | {"pyannote"}:
            raise PlanRejected("model_unknown")
        if backend not in hosted:
            raise PlanRejected("unsupported_combination")  # e.g. OpenVINO GenAI on cuda
        if backend == "whisper-cpp":
            raise PlanRejected("native_build_unsupported")
        if backend not in EXECUTABLE_BACKENDS:
            raise PlanRejected("setup_backend_unsupported")
        if ref not in SETUP_MODELS:
            raise PlanRejected("model_unknown")
    if not any(SETUP_MODELS[ref].backend in ASR_BACKENDS for ref in refs):
        raise PlanRejected("asr_model_required")
    has_diarization = any(SETUP_MODELS[ref].backend == "pyannote" for ref in refs)
    if has_diarization != request.diarization_enabled:
        raise PlanRejected("diarization_model_mismatch")


def build_plan(request: SetupPlanRequest, observation: SetupObservation) -> PlanContent:
    """Pure: same request + observation -> same content (and therefore the same hash)."""
    _check_refs(request)
    profile = observation.profile(request.profile_kind)
    changes: list[SetupChange] = []
    steps: list[SetupStep] = []
    unknown_size = False
    if profile.exists and profile.compatible is True:
        changes.append(
            SetupChange(
                resource_kind="profile",
                resource_id=request.profile_kind,
                action="reuse",
                reason_code="profile_compatible",
            )
        )
    elif profile.exists:
        # In-place rebuild of an existing venv is not offered (no generation switch yet).
        raise PlanRejected("profile_rebuild_unsupported")
    else:
        if observation.platform != "nt":
            raise PlanRejected("venv_build_platform_unsupported")  # setup.ps1 is Windows-only
        changes.append(
            SetupChange(
                resource_kind="profile",
                resource_id=request.profile_kind,
                action="create",
                reason_code="profile_missing",
            )
        )
        steps.append(
            SetupStep(
                id="venv",
                kind="venv_sync",
                depends_on=(),
                required=True,
                recovery="verified_reuse",
                target=request.profile_kind,
            )
        )
        unknown_size = True  # dependency download size is not knowable without uv
    base = tuple(step.id for step in steps)
    models = [SETUP_MODELS[ref] for ref in request.model_refs]
    gated = [model for model in models if model.gated]
    agreements = tuple(sorted({f"hf-gated:{model.repository_id}" for model in gated}))
    if gated:
        steps.append(
            SetupStep(
                id="preflight",
                kind="credential_preflight",
                depends_on=base,
                required=True,
                recovery="rerun",
                target=gated[0].ref,
            )
        )
    download = 0
    model_steps: list[str] = []
    for index, model in enumerate(models, start=1):
        present = observation.model_present(model.ref)
        changes.append(
            SetupChange(
                resource_kind="model",
                resource_id=model.ref,
                action="verify" if present else "create",
                reason_code="model_present_unverified" if present else "model_not_observed",
            )
        )
        if not present:
            download += model.approximate_size_bytes
        step_id = f"model-{index}"
        depends = base + (("preflight",) if model.gated else ())
        steps.append(
            SetupStep(
                id=step_id,
                kind="model_download",
                depends_on=depends,
                required=True,
                recovery="manager_resume",
                target=model.ref,
            )
        )
        model_steps.append(step_id)
    asr = next(model for model in models if model.backend in ASR_BACKENDS)
    steps.append(
        SetupStep(
            id="smoke",
            kind="smoke",
            depends_on=(*base, *(("preflight",) if gated else ()), *model_steps),
            required=True,
            recovery="rerun",
            target=asr.ref,
        )
    )
    preserved = tuple(
        p.name
        for p in observation.profiles
        if p.exists and (p.name != request.profile_kind or profile.compatible is True)
    )
    size = None if unknown_size else download
    return PlanContent(
        schema_version=1,
        profile_kind=request.profile_kind,
        model_refs=request.model_refs,
        diarization_enabled=request.diarization_enabled,
        inventory_fingerprint=observation.fingerprint,
        estimate=Estimate(
            download_bytes=size, disk_bytes=size, duration_seconds=None, confidence="low"
        ),
        changes=tuple(changes),
        steps=tuple(steps),
        preserved_environment_ids=preserved,  # type: ignore[arg-type]
        required_agreements=agreements,
        missing_credentials=() if not gated or observation.credential_present else ("hf_token",),
    )


def validate_dag(steps: tuple[SetupStep, ...]) -> list[SetupStep]:
    """Topological order (declaration order must already respect dependencies)."""
    seen: set[str] = set()
    for step in steps:
        if step.id in seen or not set(step.depends_on) <= seen:
            raise ValueError("setup_step_dag_invalid")
        seen.add(step.id)
    return list(steps)
