"""Presentation-independent preparation and execution over the existing core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from utteran.asr.base import ASRBackend
from utteran.asr.registry import validate_asr_configuration
from utteran.audio import find_ffmpeg
from utteran.batch import BatchSummary, run_batch
from utteran.config import Config, TokenProvider
from utteran.diarization.base import DiarizationBackend
from utteran.diarization.registry import preflight_diarization_backend
from utteran.errors import ConfigurationError, InputFileNotFoundError, UnsupportedInputError
from utteran.jobs import JobStore
from utteran.memory import CalibrationStore
from utteran.pipeline import BackendPool, run_pipeline
from utteran.types import CancelToken, PipelineOutcome, ProgressCallback


@dataclass(frozen=True)
class ExecutionRequest:
    """Core-local prepared input, not a versioned job snapshot or IPC request."""

    input_path: Path = field(repr=False)
    config: Config = field(repr=False)
    recursive: bool = False
    include: tuple[str, ...] = field(default=(), repr=False)
    exclude: tuple[str, ...] = field(default=(), repr=False)


def validate_input_path(path: Path) -> None:
    if not path.exists():
        raise InputFileNotFoundError(f"入力ファイルが見つかりません: {path}")
    if not path.is_file() and not path.is_dir():
        raise UnsupportedInputError(f"通常ファイルまたはフォルダを指定してください: {path}")


def prepare_request(
    input_path: Path,
    config: Config,
    *,
    recursive: bool = False,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> ExecutionRequest:
    """Validate using existing core rules; never loads settings or downloads models."""
    validate_asr_configuration(config)
    validate_input_path(input_path)
    if input_path.is_file() and (recursive or include or exclude):
        raise ConfigurationError(
            "--recursive / --include / --exclude はフォルダ入力でのみ指定できます。"
        )
    return ExecutionRequest(input_path, config, recursive, include, exclude)


def preflight_dependencies(
    config: Config,
    token_provider: TokenProvider,
    *,
    ffmpeg_finder: Callable[[Path | None], Path] | None = None,
    diarization_preflight: Callable[[str, str, TokenProvider], None] | None = None,
) -> None:
    """Cheap existing prerequisites, separated from CLI model-download approval."""
    if config.diarization.enabled:
        (diarization_preflight or preflight_diarization_backend)(
            config.diarization.backend, config.diarization.model, token_provider
        )
    (ffmpeg_finder or find_ffmpeg)(config.ffmpeg.path)


def execute_single(
    input_path: Path,
    config: Config,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    token_provider: TokenProvider | None = None,
    resume: bool = True,
    force: bool = False,
    force_unlock: bool = False,
    asr_backend: ASRBackend | None = None,
    diarization_backend: DiarizationBackend | None = None,
    backend_pool: BackendPool | None = None,
    job_store: JobStore | None = None,
    calibration_store: CalibrationStore | None = None,
    runner: Callable[..., PipelineOutcome] | None = None,
) -> PipelineOutcome:
    """Return the existing outcome and propagate exceptions unchanged.

    Preparation has no dependency preflight side effects. Actual pipeline/backend
    checks remain in core; CLI download approval and presentation stay in CLI.
    """
    request = prepare_request(input_path, config)
    if not input_path.is_file():
        raise UnsupportedInputError(f"単一の音声または動画ファイルを指定してください: {input_path}")
    return (runner or run_pipeline)(
        request.input_path,
        request.config,
        progress=progress,
        cancel=cancel,
        token_provider=token_provider,
        resume=resume,
        force=force,
        force_unlock=force_unlock,
        asr_backend=asr_backend,
        diarization_backend=diarization_backend,
        backend_pool=backend_pool,
        job_store=job_store,
        calibration_store=calibration_store,
    )


def execute_batch(
    input_path: Path,
    config: Config,
    *,
    recursive: bool = False,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    token_provider: TokenProvider | None = None,
    resume: bool = True,
    force: bool = False,
    force_unlock: bool = False,
    dry_run: bool = False,
    asr_backend: ASRBackend | None = None,
    diarization_backend: DiarizationBackend | None = None,
    runner: Callable[..., BatchSummary] | None = None,
) -> BatchSummary:
    """Keep discovery, shared pool, partial failure and skip semantics in run_batch."""
    request = prepare_request(
        input_path, config, recursive=recursive, include=include, exclude=exclude
    )
    return (runner or run_batch)(
        request.input_path,
        request.config,
        recursive=request.recursive,
        include=request.include,
        exclude=request.exclude,
        progress=progress,
        cancel=cancel,
        token_provider=token_provider,
        resume=resume,
        force=force,
        force_unlock=force_unlock,
        dry_run=dry_run,
        asr_backend=asr_backend,
        diarization_backend=diarization_backend,
    )
