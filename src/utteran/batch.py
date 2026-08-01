"""Stable sequential folder processing with shared model backends."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from utteran.asr.base import ASRBackend
from utteran.config import Config, TokenProvider, default_token_provider
from utteran.diarization.base import DiarizationBackend
from utteran.errors import (
    AudioDecodeError,
    CancelledError,
    InputFileNotFoundError,
    UnsupportedInputError,
    UtteranError,
)
from utteran.logging import mask_secrets
from utteran.pipeline import BackendPool, run_pipeline
from utteran.types import CancelToken, PipelineOutcome, ProgressCallback

SUPPORTED_MEDIA_SUFFIXES = frozenset(
    {
        ".wav",
        ".mp3",
        ".m4a",
        ".flac",
        ".ogg",
        ".aac",
        ".wma",
        ".mp4",
        ".mkv",
        ".mov",
        ".avi",
        ".webm",
        ".ts",
    }
)

BatchStatus: TypeAlias = Literal["success", "skipped", "failed"]


@dataclass(frozen=True)
class BatchItemResult:
    """Outcome and explanation for one selected input."""

    path: Path
    status: BatchStatus
    reason: str
    outcome: PipelineOutcome | None = None


@dataclass(frozen=True)
class BatchSummary:
    """Aggregate counts and exit semantics for a sequential batch."""

    items: tuple[BatchItemResult, ...]

    @property
    def success_count(self) -> int:
        """Return the number of newly processed files."""
        return sum(item.status == "success" for item in self.items)

    @property
    def skipped_count(self) -> int:
        """Return the number of safely skipped files."""
        return sum(item.status == "skipped" for item in self.items)

    @property
    def failed_count(self) -> int:
        """Return the number of processing failures."""
        return sum(item.status == "failed" for item in self.items)

    @property
    def exit_code(self) -> int:
        """Map no/partial/total failure to the documented CLI codes."""
        if self.failed_count == 0:
            return 0
        if self.failed_count == len(self.items):
            return 1
        return 5


def discover_inputs(
    directory: Path,
    *,
    recursive: bool = False,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> list[Path]:
    """Select likely media in deterministic name order before ffmpeg validation."""
    if not directory.exists():
        raise InputFileNotFoundError(f"入力パスが見つかりません: {directory}")
    if not directory.is_dir():
        raise UnsupportedInputError(f"フォルダを指定してください: {directory}")
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    selected: list[tuple[str, Path]] = []
    for candidate in iterator:
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(directory).as_posix()
        included = _matches(relative, candidate.name, include)
        if include:
            if not included:
                continue
        elif candidate.suffix.casefold() not in SUPPORTED_MEDIA_SUFFIXES:
            continue
        if _matches(relative, candidate.name, exclude):
            continue
        selected.append((relative, candidate))
    selected.sort(key=lambda item: (item[0].casefold(), item[0]))
    return [path for _relative, path in selected]


def run_batch(
    directory: Path,
    config: Config,
    *,
    recursive: bool = False,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    resume: bool = True,
    force: bool = False,
    force_unlock: bool = False,
    dry_run: bool = False,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    token_provider: TokenProvider | None = None,
    asr_backend: ASRBackend | None = None,
    diarization_backend: DiarizationBackend | None = None,
) -> BatchSummary:
    """Process selected files sequentially without reloading model backends."""
    selected = discover_inputs(
        directory,
        recursive=recursive,
        include=include,
        exclude=exclude,
    )
    ignored_roots = (config.effective_job_dir.absolute(), config.general.output_dir.absolute())
    selected = [
        path
        for path in selected
        if not any(_is_below(path.absolute(), root) for root in ignored_roots)
    ]
    provider = token_provider or default_token_provider()
    pool = BackendPool(
        provider,
        asr_backend=asr_backend,
        diarization_backend=diarization_backend,
    )
    items: list[BatchItemResult] = []
    try:
        for path in selected:
            if cancel is not None:
                cancel.raise_if_cancelled()
            if dry_run:
                items.append(BatchItemResult(path, "skipped", "dry-run: 処理対象"))
                continue
            try:
                outcome = run_pipeline(
                    path,
                    config,
                    progress=progress,
                    cancel=cancel,
                    token_provider=provider,
                    backend_pool=pool,
                    resume=resume,
                    force=force,
                    force_unlock=force_unlock,
                )
                if outcome.executed_stages:
                    reason = "完了: " + ", ".join(outcome.executed_stages)
                    status: BatchStatus = "success"
                else:
                    reason = "既に完了しているジョブ"
                    status = "skipped"
                items.append(BatchItemResult(path, status, reason, outcome))
            except AudioDecodeError as exc:
                items.append(BatchItemResult(path, "skipped", mask_secrets(str(exc))))
            except (CancelledError, KeyboardInterrupt):
                raise
            except UtteranError as exc:
                items.append(BatchItemResult(path, "failed", mask_secrets(str(exc))))
            except Exception as exc:
                items.append(
                    BatchItemResult(
                        path,
                        "failed",
                        f"予期しないエラー: {mask_secrets(str(exc))}",
                    )
                )
    finally:
        pool.close()
    return BatchSummary(tuple(items))


def _matches(relative: str, name: str, patterns: tuple[str, ...]) -> bool:
    """Match a glob against both the relative POSIX path and basename."""
    return any(
        fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(name, pattern)
        for pattern in patterns
    )


def _is_below(path: Path, root: Path) -> bool:
    """Return whether a candidate is inside a generated-data root."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
