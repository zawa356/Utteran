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
from utteran.types import CancelToken, ProgressEvent
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


def explicit_config(request: WorkerRequest) -> Config:
    data = request.context.base_config
    _keys(Config, data)
    try:
        base = Config.model_validate(data)
        ctx = request.context
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
    for value in (ctx.asr_backend, ctx.asr_model, ctx.asr_device, ctx.compute_type):
        if value != value.strip() or not value.strip() or value.casefold() == "auto":
            raise ProtocolError(WorkerErrorCode.INVALID_CONTEXT)
    return cfg


def offline_preflight(config: Config) -> None:
    # pyannote Pipeline.from_pretrained can resolve transitive remote components.
    # whisper.cpp invokes native argv (including prompt/input); not enabled here.
    if config.asr.backend != "faster-whisper" or config.diarization.enabled:
        raise ProtocolError(WorkerErrorCode.UNSUPPORTED_SETTING)
    if importlib.util.find_spec("faster_whisper") is None:
        raise ProtocolError(WorkerErrorCode.DEPENDENCY_MISSING)
    # Only an explicit absolute local model directory: no catalog/cache lookup,
    # Hub ID, tokenizer fallback, or auto device selection at this boundary.
    model = Path(config.asr.model)
    if (
        not model.is_absolute()
        or not model.is_dir()
        or any(
            not (model / name).is_file()
            for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json")
        )
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
