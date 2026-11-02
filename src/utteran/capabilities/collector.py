"""Explicit read-only collection, deliberately without native probes or smoke.

Only managed models are inspected. Legacy device caches lack profile-scoped
observation times and driver identities, so they cannot establish B4 readiness.
An injected provider can supply stronger evidence through the same pure evaluator.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utteran.capabilities.evaluator import digest, evidence_fingerprint, make_inventory
from utteran.capabilities.recipes import RECIPES
from utteran.config import EnvironmentTokenProvider
from utteran.models.catalog import list_models
from utteran.models.manager import ModelManager, _missing_required_files
from utteran.native import NativeBuilder
from utteran.profiles import PROFILE_EXTRAS
from utteran_contracts import ProcessingSettings
from utteran_contracts.capabilities import (
    CapabilityObservation,
    DeploymentInventory,
    ExecutionIdentity,
    InventoryRefreshRequest,
    InventorySnapshot,
    ReasonCode,
)
from utteran_paths import resolve_data_paths

InventoryProvider = Callable[[InventoryRefreshRequest, datetime], tuple[DeploymentInventory, ...]]


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("invalid metadata")
    return value


def _file_identity(path: Path) -> str:
    # Stat identity, not an upstream Hub revision or a content-verification claim.
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    return digest(
        [
            (str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
            for p in files
        ]
    )


def _profile_state(profile: str, root: Path, lock: Path) -> tuple[ReasonCode, str, str]:
    if not root.is_dir():
        return "profile_missing", "unknown", digest(None)
    try:
        manifest_path = root / ".utteran-profile.json"
        manifest = _read_json(manifest_path)
    except FileNotFoundError:
        return "profile_manifest_missing", "unknown", digest(None)
    except (OSError, ValueError, UnicodeError):
        return "profile_manifest_invalid", "unknown", digest(None)
    generation = digest(
        {
            "manifest": manifest,
            "stat": manifest_path.stat().st_mtime_ns,
            "root": root.stat().st_ctime_ns,
        }
    )
    fingerprint = digest(
        {"generation": generation, "path": str(root.resolve()), "extras": PROFILE_EXTRAS[profile]}
    )
    if manifest.get("schema_version") != 1:
        return "profile_manifest_invalid", generation, fingerprint
    if not lock.is_file():
        return "current_lock_missing", generation, fingerprint
    if manifest.get("lock_sha256") != hashlib.sha256(lock.read_bytes()).hexdigest():
        return "dependency_lock_changed", generation, fingerprint
    if manifest.get("extras") != list(PROFILE_EXTRAS[profile]):
        return "profile_extras_changed", generation, fingerprint
    recorded = manifest.get("venv_path")
    if not isinstance(recorded, str) or os.path.normcase(
        str(Path(recorded).expanduser().resolve())
    ) != os.path.normcase(str(root.resolve())):
        return "profile_path_changed", generation, fingerprint
    return "supported", generation, fingerprint


def collect_managed_deployments(
    profile: str,
    settings: ProcessingSettings,
    execution_path: str,
    *,
    now: datetime,
    profile_root: Path,
    repo_root: Path,
    model_root: Path,
    native_root: Path,
) -> tuple[DeploymentInventory, ...]:
    """No sync/download/build/manifest writes, inference imports, or process calls.

    CPU is an explicit recipe device; GPU index zero is only a requested target,
    with hardware/runtime unknown. Other indices require a supplied provider.
    """
    profile_code, generation, profile_fp = _profile_state(
        profile, profile_root, repo_root / "uv.lock"
    )
    dependency_versions: dict[str, str | None] = {}
    for name in ("utteran", "faster-whisper", "ctranslate2", "torch", "openvino", "openvino-genai"):
        try:
            dependency_versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependency_versions[name] = None
    profile_fp = digest(
        {
            "profile": profile_fp,
            "dependencies": dependency_versions,
            "os": (platform.system(), platform.release(), platform.version(), platform.machine()),
            "abi": sys.implementation.cache_tag,
        }
    )
    native = NativeBuilder(native_root)
    native_status = native.status()
    manager = ModelManager(model_root, token_provider=EnvironmentTokenProvider())
    result: list[DeploymentInventory] = []
    for recipe in RECIPES:
        # Join the registered recipe to its actual backend catalog, rather than
        # creating arbitrary backend/native/device combinations.
        for model in list_models(backend=recipe.backend):
            path = manager.managed_path(model)
            present = path.is_dir()
            missing = _missing_required_files(model, path)
            files_fp = _file_identity(path) if present else None
            variant = recipe.requested_route.native_variant
            native_ok = variant is not None and native_status["runnable"].get(variant) is True
            # Manifest and executable identities are separate from runtime evidence.
            native_fp = digest(native_status["manifest"]) if native_ok else None
            if native_ok:
                executable = native_status["manifest"]["backends"][variant]["executable"]
                native_fp = digest((native_fp, _file_identity(Path(executable))))
            local_complete = (
                present
                and not missing
                and all(
                    (path / name).is_file()
                    for name in (
                        "model.bin",
                        "config.json",
                        "tokenizer.json",
                        "preprocessor_config.json",
                    )
                )
            )
            identity = ExecutionIdentity.model_validate(
                dict(
                    recipe_id=recipe.id,
                    recipe_revision=recipe.revision,
                    adapter_version="b4.1",
                    smoke_version=1,
                    profile_id=profile,
                    profile_generation=generation,
                    profile_fingerprint=profile_fp,
                    platform=platform.system()
                    if platform.system() in {"Windows", "Linux"}
                    else "unknown",
                    python_abi=sys.implementation.cache_tag or "unknown",
                    # Package metadata cannot identify the actual driver or hardware.
                    runtime_fingerprint=None,
                    hardware_fingerprint=None,
                    model_id=model.model_id,
                    model_family=recipe.model_family,
                    english_only=model.english_only,
                    model_revision="local-" + files_fp if files_fp else "unknown",
                    model_files_fingerprint=files_fp,
                    native_fingerprint=native_fp,
                    device=recipe.devices[0],
                    device_index=0,
                    compute_type=recipe.compute_types[0],
                    execution_path=execution_path,
                    local_complete_model=local_complete,
                    input_count=1,
                    diarization_device=(
                        settings.diarization.device_preference
                        if settings.diarization.device_preference != "auto"
                        else "cpu"
                    )
                    if settings.diarization.enabled
                    else None,
                    diarization_device_index=0 if settings.diarization.enabled else None,
                    diarization_model=settings.diarization.model
                    if settings.diarization.enabled
                    else None,
                    diarization_fingerprint=None,
                )
            )
            fp = evidence_fingerprint(identity, settings)
            venv_code = profile_code
            if profile_code == "supported" and not set(recipe.required_extras).issubset(
                PROFILE_EXTRAS[profile]
            ):
                venv_code = "component_missing"
            facts = [
                (
                    "venv",
                    "profile-manifest",
                    "passed"
                    if venv_code == "supported"
                    else "unknown"
                    if venv_code == "current_lock_missing"
                    else "failed",
                    venv_code,
                ),
                (
                    "model",
                    "model-files",
                    "failed" if missing else "passed",
                    "model_missing" if missing else "supported",
                ),
                (
                    "native",
                    "native-manifest",
                    "passed" if native_ok else "failed",
                    "supported" if native_ok else "native_missing",
                ),
            ]
            observations = tuple(
                CapabilityObservation.model_validate(
                    dict(
                        kind=kind,
                        source=source,
                        state=state,
                        reason_code=code,
                        fingerprint=fp,
                        observed_at=now,
                        expires_at=now + timedelta(minutes=5),
                    )
                )
                for kind, source, state, code in facts
            )
            # Presence is not smoke. Memory, driver/runtime, hardware and pyannote
            # remain unknown even when every file and package exists.
            result.append(
                DeploymentInventory(
                    identity=identity,
                    observations=observations,
                    required_memory_bytes=None,
                    available_memory_bytes=None,
                )
            )
    return tuple(result)


def local_provider(
    request: InventoryRefreshRequest, now: datetime
) -> tuple[DeploymentInventory, ...]:
    paths = resolve_data_paths()
    return collect_managed_deployments(
        request.profile_id,
        request.settings,
        request.execution_path,
        now=now,
        profile_root=Path(sys.prefix),
        repo_root=Path.cwd(),
        model_root=paths.models,
        native_root=paths.native,
    )


def refresh_inventory(
    request: InventoryRefreshRequest,
    *,
    now: datetime,
    provider: InventoryProvider = local_provider,
) -> InventorySnapshot:
    deployments = provider(request, now)
    if any(d.identity.profile_id != request.profile_id for d in deployments):
        raise ValueError("profile_scope_mismatch")
    return make_inventory(
        deployments, request.settings, request.execution_path, revision=request.revision, now=now
    )
