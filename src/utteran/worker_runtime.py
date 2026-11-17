"""B3 execution boundary. No CLI imports, setup, or ambient Config.load."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel, ValidationError

from utteran import application
from utteran.config import Config, EnvironmentTokenProvider
from utteran.config_adapter import ConfigAdaptationError, ExecutionContext, adapt_settings
from utteran.errors import CancelledError, DependencyError, ModelNotFoundError
from utteran.models.catalog import list_models
from utteran.types import CancelToken, ProgressEvent
from utteran_contracts.engines import get_engine
from utteran_contracts.worker import WorkerArtifact, WorkerErrorCode, WorkerRequest
from utteran_contracts.worker_framing import ProtocolError


def _keys(model: type[BaseModel], data: Any) -> None:
    if not isinstance(data, dict) or data.keys() - model.model_fields.keys():
        raise ProtocolError(WorkerErrorCode.INVALID_CONTEXT)
    for key, value in data.items():
        annotation = model.model_fields[key].annotation
        candidates = (annotation, *get_args(annotation))
        nested = next(
            (t for t in candidates if isinstance(t, type) and issubclass(t, BaseModel)), None
        )
        if nested is not None:
            _keys(nested, value)


def _catalogue_id(backend: str, model: str) -> bool:
    """True only for an exact canonical catalogue ID of that backend (static table)."""
    return any(entry.model_id == model for entry in list_models(backend=backend))


def _local_model_dir(value: str) -> Path:
    """Structural check of the B7-frozen directory; existence/files are offline_preflight's.

    No expanduser, resolve, cwd or environment lookup: the value must already be the
    explicit absolute, normalized local path B7 recorded for the resolved model ID.
    """
    path = Path(value)
    if (
        value != value.strip()
        or "\0" in value
        or not path.is_absolute()
        or any(part == ".." for part in path.parts)
        or str(path) != value
    ):
        raise ProtocolError(WorkerErrorCode.INVALID_CONTEXT)
    return path


def bind_local_model(config: Config, model_dir: Path) -> Config:
    """Explicit execution boundary: load the verified local directory for the checked ID.

    Called only after adapt_settings compared the catalogue ID with the preset; the
    adapter itself stays pure and never sees a path.
    """
    payload = config.model_dump(mode="json")
    payload["asr"]["model"] = str(model_dir)
    bound = Config.model_validate(payload)
    if bound.asr.model != str(model_dir):
        raise ProtocolError(WorkerErrorCode.INVALID_CONTEXT)
    return bound


def explicit_config(request: WorkerRequest) -> Config:
    data = request.context.base_config
    _keys(Config, data)
    ctx = request.context
    for value in (ctx.asr_backend, ctx.asr_model, ctx.asr_device, ctx.compute_type):
        if value != value.strip() or not value.strip() or value.casefold() == "auto":
            raise ProtocolError(WorkerErrorCode.INVALID_CONTEXT)
    # The ID field carries only the resolved catalogue ID; a path, Hub repository or
    # unknown name here is a malformed request, not a preset conflict.
    if not _catalogue_id(ctx.asr_backend, ctx.asr_model):
        raise ProtocolError(WorkerErrorCode.INVALID_CONTEXT)
    model_dir = _local_model_dir(ctx.asr_model_dir)
    try:
        base = Config.model_validate(data)
        cfg = adapt_settings(
            request.settings,
            ExecutionContext(
                base,
                ctx.asr_backend,
                ctx.asr_model,
                ctx.asr_device,
                ctx.diarization_device,
                ctx.compute_type,
                ctx.native_variant,
            ),
        ).config
    except (ValidationError, ConfigAdaptationError, ValueError):
        raise ProtocolError(WorkerErrorCode.UNSUPPORTED_SETTING) from None
    # Runtime directories are explicit. External requests cannot select executables,
    # native plugins, an alternative environment, or opt in to raw diagnostics.
    if (
        cfg.ffmpeg.path is not None
        or cfg.general.native_dir is not None
        or cfg.general.venv_dir is not None
        or cfg.general.raw_subprocess_logs
        or cfg.general.default_profile not in {None, request.profile}
    ):
        raise ProtocolError(WorkerErrorCode.INVALID_CONTEXT)
    for path in (Path(request.input_path), cfg.general.output_dir, cfg.general.job_dir):
        if path is None or not path.is_absolute():
            raise ProtocolError(WorkerErrorCode.INVALID_CONTEXT)
    # adapt_settings normalized cfg.asr.model to the checked ID; execution loads only the
    # frozen local directory (offline_preflight then verifies it on disk).
    return bind_local_model(cfg, model_dir)


def offline_preflight(config: Config) -> None:
    """Refuse anything the engine declaration does not let the worker execute.

    The declaration (utteran_contracts.engines) is the single list of worker routes:
    whisper.cpp passes prompt/input as native argv and pyannote may resolve remote
    components, so both stay closed there until proven otherwise.
    """
    try:
        engine = get_engine(config.asr.backend)
    except ValueError:
        raise ProtocolError(WorkerErrorCode.UNSUPPORTED_SETTING) from None
    worker = engine.worker
    if worker.status != "implemented" or (config.diarization.enabled and not worker.diarization):
        raise ProtocolError(WorkerErrorCode.UNSUPPORTED_SETTING)
    if (
        engine.runtime_module is not None
        and importlib.util.find_spec(engine.runtime_module) is None
    ):
        raise ProtocolError(WorkerErrorCode.DEPENDENCY_MISSING)
    # Only an explicit absolute local model directory: no catalog/cache lookup,
    # Hub ID, tokenizer fallback, or auto device selection at this boundary.
    # These files are a necessary pre-check, not a load guarantee: the runtime and the
    # model's own files (e.g. vocabulary) are only proven by the real load in core.
    model = Path(config.asr.model)
    if (
        not model.is_absolute()
        or not model.is_dir()
        or any(not (model / name).is_file() for name in worker.required_model_files)
    ):
        raise ProtocolError(WorkerErrorCode.MODEL_MISSING)
    application.preflight_dependencies(config, EnvironmentTokenProvider())


def artifact(path: Path) -> WorkerArtifact:
    path = path.resolve(strict=True)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return WorkerArtifact(path=str(path), size=path.stat().st_size, sha256=digest.hexdigest())


def execute(
    request: WorkerRequest, cancel: CancelToken, emit: Callable[..., None]
) -> dict[str, Any]:
    cancel.raise_if_cancelled()
    config = explicit_config(request)
    prepared = application.prepare_request(Path(request.input_path), config)
    offline_preflight(config)
    cancel.raise_if_cancelled()
    emit("progress", stage="job", state="validated", fraction=None)

    def progress(event: ProgressEvent) -> None:
        cancel.raise_if_cancelled()
        stage = (
            event.stage
            if event.stage in {"audio", "asr", "diarization", "merge", "export"}
            else "job"
        )
        fraction = None
        if event.total is not None and event.total > 0:
            value = event.completed / event.total
            if math.isfinite(value):
                fraction = max(0.0, min(1.0, value))
        emit(
            "progress",
            stage=stage,
            state="skipped" if event.skipped else "running",
            fraction=fraction,
        )

    outcome = application.execute_single(
        prepared.input_path,
        prepared.config,
        progress=progress,
        cancel=cancel,
        token_provider=EnvironmentTokenProvider(),
        resume=request.resume,
        force=request.force,
    )
    # Execution finished and core's finally blocks released locks before completion.
    if not outcome.job_id or not outcome.output_paths:
        raise ProtocolError(WorkerErrorCode.ARTIFACT_INVALID)
    manifest_path = config.effective_job_dir / outcome.job_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("job_id") != outcome.job_id
        or manifest.get("version") != 1
        or any(
            manifest.get("stages", {}).get(s, {}).get("status") != "done"
            for s in ("audio", "asr", "diarization", "merge", "export")
        )
    ):
        raise ProtocolError(WorkerErrorCode.ARTIFACT_INVALID)
    return {
        "core_job_id": outcome.job_id,
        "artifacts": tuple(artifact(p) for p in outcome.output_paths),
        "manifest": artifact(manifest_path),
    }


def error_code(error: Exception) -> WorkerErrorCode:
    if isinstance(error, ProtocolError):
        return error.code
    if isinstance(error, CancelledError):
        return WorkerErrorCode.CANCELLED
    if isinstance(error, ModelNotFoundError):
        return WorkerErrorCode.MODEL_MISSING
    if isinstance(error, DependencyError):
        return WorkerErrorCode.DEPENDENCY_MISSING
    return WorkerErrorCode.CORE_FAILURE
