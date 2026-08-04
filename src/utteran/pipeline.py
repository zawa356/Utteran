"""Resumable single-file orchestration over persistent job stages."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

from utteran.align import align_transcription
from utteran.asr.base import ASRBackend
from utteran.asr.registry import create_asr_backend
from utteran.audio import normalize_audio
from utteran.config import Config, TokenProvider, default_token_provider
from utteran.diarization.base import DiarizationBackend
from utteran.diarization.registry import (
    create_diarization_backend,
    preflight_diarization_backend,
)
from utteran.errors import CancelledError, InputFileNotFoundError, UnsupportedInputError
from utteran.exporters import export_all
from utteran.jobs import Job, JobStore, StageName, stage_config_hashes
from utteran.logging import job_log, mask_secrets
from utteran.types import (
    AlignmentOptions,
    ASROptions,
    CancelToken,
    DiarizationOptions,
    DiarizationResult,
    ExportOptions,
    PipelineOutcome,
    PipelineResult,
    ProgressCallback,
    ProgressEvent,
    Segment,
    TranscriptionResult,
)


class BackendPool:
    """Load each configured backend once and reuse it across a sequential batch."""

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        asr_backend: ASRBackend | None = None,
        diarization_backend: DiarizationBackend | None = None,
    ) -> None:
        self._token_provider = token_provider
        self._asr = asr_backend
        self._diarization = diarization_backend
        self._asr_key: tuple[str, str, str, str] | None = None
        self._diarization_key: tuple[str, str, str] | None = None

    def asr(self, config: Config) -> ASRBackend:
        """Return a loaded ASR backend matching the effective settings."""
        key = (
            config.asr.backend,
            config.asr.model,
            config.asr.device,
            config.asr.compute_type,
        )
        if self._asr_key == key and self._asr is not None:
            logging.getLogger(__name__).info("ASRバックエンドを再利用: %s/%s/%s/%s", *key)
            return self._asr
        if self._asr is not None and self._asr_key is not None:
            self._asr.unload()
            self._asr = None
        self._asr = self._asr or create_asr_backend(config.asr.backend, config)
        self._asr.load(config.asr.model, config.asr.device, config.asr.compute_type)
        self._asr_key = key
        logging.getLogger(__name__).info("ASRバックエンドをロード: %s/%s/%s/%s", *key)
        return self._asr

    def diarization(self, config: Config) -> DiarizationBackend:
        """Return a loaded diarization backend matching the effective settings."""
        key = (
            config.diarization.backend,
            config.diarization.model,
            config.diarization.device,
        )
        if self._diarization_key == key and self._diarization is not None:
            logging.getLogger(__name__).info("話者分離バックエンドを再利用: %s/%s/%s", *key)
            return self._diarization
        if self._diarization is not None and self._diarization_key is not None:
            self._diarization.unload()
            self._diarization = None
        self._diarization = self._diarization or create_diarization_backend(
            config.diarization.backend,
            self._token_provider,
        )
        self._diarization.load(config.diarization.model, config.diarization.device)
        self._diarization_key = key
        logging.getLogger(__name__).info("話者分離バックエンドをロード: %s/%s/%s", *key)
        return self._diarization

    def close(self) -> None:
        """Release all loaded model resources."""
        if self._asr is not None:
            self._asr.unload()
            self._asr = None
            self._asr_key = None
        if self._diarization is not None:
            self._diarization.unload()
            self._diarization = None
            self._diarization_key = None


def run_pipeline(
    input_path: Path,
    config: Config,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    token_provider: TokenProvider | None = None,
    asr_backend: ASRBackend | None = None,
    diarization_backend: DiarizationBackend | None = None,
    backend_pool: BackendPool | None = None,
    job_store: JobStore | None = None,
    resume: bool = True,
    force: bool = False,
    force_unlock: bool = False,
) -> PipelineOutcome:
    """Run or resume all stages for one audio/video input."""
    _validate_input(input_path)
    if backend_pool is not None and (asr_backend is not None or diarization_backend is not None):
        raise ValueError("backend_pool と個別 backend は同時に指定できません。")

    selected_token_provider = token_provider or default_token_provider()
    selected_store = job_store or JobStore(config.effective_job_dir)
    job = selected_store.open(input_path)
    hashes = stage_config_hashes(config, job.manifest.input.hash)
    owns_pool = backend_pool is None
    pool = backend_pool or BackendPool(
        selected_token_provider,
        asr_backend=asr_backend,
        diarization_backend=diarization_backend,
    )
    executed_stages: list[str] = []
    stage_durations: dict[str, float] = {}
    try:
        job_log_level = "debug" if config.general.log_level == "debug" else "info"
        with job.lock(force=force_unlock), job_log(job.root / "utteran.log", job_log_level):
            logging.getLogger(__name__).info("ジョブ開始: %s", job.manifest.job_id)
            job.reconcile(hashes, force=force or not resume)
            result = _run_stages(
                job,
                input_path,
                config,
                hashes,
                pool,
                selected_token_provider,
                executed_stages,
                stage_durations,
                progress,
                cancel,
            )
            output_paths = [Path(path) for path in job.manifest.stages["export"].artifacts]
            logging.getLogger(__name__).info("ジョブ完了: %s", job.manifest.job_id)
            return PipelineOutcome(
                result=result,
                output_paths=output_paths,
                job_id=job.manifest.job_id,
                executed_stages=tuple(executed_stages),
                stage_durations=stage_durations,
            )
    finally:
        if owns_pool:
            pool.close()


def _run_stages(
    job: Job,
    input_path: Path,
    config: Config,
    hashes: dict[StageName, str],
    pool: BackendPool,
    token_provider: TokenProvider,
    executed_stages: list[str],
    stage_durations: dict[str, float],
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
) -> PipelineResult:
    """Execute pending stages, persisting every transition and artifact."""
    transcription: TranscriptionResult | None = None
    diarization: DiarizationResult | None = None
    result: PipelineResult | None = None

    if not job.is_done("audio", hashes["audio"]):
        _execute_stage(
            job,
            "audio",
            hashes["audio"],
            executed_stages,
            stage_durations,
            cancel,
            lambda: normalize_audio(
                input_path,
                job.audio_path,
                ffmpeg_path=config.ffmpeg.path,
                progress=progress,
                cancel=cancel,
            ),
            lambda path: [cast(Path, path)],
        )
    else:
        _report_skip(progress, "audio")

    if job.is_done("asr", hashes["asr"]):
        transcription = _load_transcription(job)
        _report_skip(progress, "asr")
    if transcription is None:
        transcription = cast(
            TranscriptionResult,
            _execute_stage(
                job,
                "asr",
                hashes["asr"],
                executed_stages,
                stage_durations,
                cancel,
                lambda: _transcribe_asr(pool, config, job.audio_path, progress, cancel),
                lambda value: [
                    job.write_intermediate("asr", cast(TranscriptionResult, value).to_dict())
                ],
            ),
        )

    if job.is_done("diarization", hashes["diarization"]):
        diarization = _load_diarization(job)
        _report_skip(progress, "diarization")
    else:
        if config.diarization.enabled:
            preflight_diarization_backend(
                config.diarization.backend,
                config.diarization.model,
                token_provider,
            )
            diarization = cast(
                DiarizationResult,
                _execute_stage(
                    job,
                    "diarization",
                    hashes["diarization"],
                    executed_stages,
                    stage_durations,
                    cancel,
                    lambda: pool.diarization(config).diarize(
                        job.audio_path,
                        _diarization_options(config),
                        progress,
                        cancel,
                    ),
                    lambda value: [
                        job.write_intermediate(
                            "diarization", cast(DiarizationResult, value).to_dict()
                        )
                    ],
                ),
            )
        else:
            _execute_stage(
                job,
                "diarization",
                hashes["diarization"],
                executed_stages,
                stage_durations,
                cancel,
                lambda: None,
                lambda _value: [job.write_intermediate("diarization", None)],
            )

    if job.is_done("merge", hashes["merge"]):
        result = _load_merged(job)
        _report_skip(progress, "merge")
    if result is None:
        result = cast(
            PipelineResult,
            _execute_stage(
                job,
                "merge",
                hashes["merge"],
                executed_stages,
                stage_durations,
                cancel,
                lambda: _merge_result(input_path, transcription, diarization, config, progress),
                lambda value: [
                    job.write_intermediate("merge", cast(PipelineResult, value).to_dict())
                ],
            ),
        )

    if not job.is_done("export", hashes["export"]):
        _execute_stage(
            job,
            "export",
            hashes["export"],
            executed_stages,
            stage_durations,
            cancel,
            lambda: _export_result(result, config, progress),
            lambda value: cast(list[Path], value),
        )
    else:
        _report_skip(progress, "export")
    return result


def _execute_stage(
    job: Job,
    stage: StageName,
    config_hash: str,
    executed_stages: list[str],
    stage_durations: dict[str, float],
    cancel: CancelToken | None,
    operation: Callable[[], object],
    artifacts: Callable[[object], list[Path]],
) -> object:
    """Persist one pending/running/done/failed state machine transition."""
    _check_cancel(cancel)
    job.start_stage(stage, config_hash)
    executed_stages.append(stage)
    logging.getLogger(__name__).info("ステージ開始: %s", stage)
    started = time.perf_counter()
    try:
        value = operation()
        _check_cancel(cancel)
        paths = artifacts(value)
        job.complete_stage(stage, config_hash, paths)
        elapsed = time.perf_counter() - started
        stage_durations[stage] = elapsed
        logging.getLogger(__name__).info("ステージ完了: %s (%.3f秒)", stage, elapsed)
        return value
    except (CancelledError, KeyboardInterrupt):
        job.interrupt_stage(stage)
        logging.getLogger(__name__).info("ステージ中断: %s", stage)
        raise
    except Exception as exc:
        job.fail_stage(stage, config_hash, mask_secrets(str(exc)))
        logging.getLogger(__name__).error("ステージ失敗: %s: %s", stage, exc)
        raise


def _load_transcription(job: Job) -> TranscriptionResult | None:
    """Restore compatible ASR data or request recomputation."""
    payload = job.read_intermediate("asr")
    return (
        TranscriptionResult.from_dict(cast(dict[str, object], payload))
        if isinstance(payload, dict)
        else None
    )


def _load_diarization(job: Job) -> DiarizationResult | None:
    """Restore compatible diarization data, including the disabled null value."""
    payload = job.read_intermediate("diarization")
    return (
        DiarizationResult.from_dict(cast(dict[str, object], payload))
        if isinstance(payload, dict)
        else None
    )


def _load_merged(job: Job) -> PipelineResult | None:
    """Restore a compatible merged result."""
    payload = job.read_intermediate("merge")
    return (
        PipelineResult.from_dict(cast(dict[str, object], payload))
        if isinstance(payload, dict)
        else None
    )


def _merge_result(
    input_path: Path,
    transcription: TranscriptionResult,
    diarization: DiarizationResult | None,
    config: Config,
    progress: ProgressCallback | None,
) -> PipelineResult:
    """Align speaker turns and create the persistent merged result."""
    if progress is not None:
        progress(ProgressEvent("merge", 0.0, 1.0, "話者を割り当てています"))
    if diarization is None:
        segments = [_copy_segment(segment) for segment in transcription.segments]
    else:
        segments = align_transcription(
            transcription,
            diarization,
            AlignmentOptions(
                max_nearest_distance=config.alignment.max_nearest_distance,
                min_segment_duration=config.alignment.min_segment_duration,
                min_segment_words=config.alignment.min_segment_words,
                merge_gap=config.alignment.merge_gap,
                renumber_speakers=config.alignment.renumber_speakers,
            ),
        )
    if progress is not None:
        progress(ProgressEvent("merge", 1.0, 1.0, "話者割当が完了しました"))
    return PipelineResult(
        input_path=input_path.resolve(),
        transcription=transcription,
        diarization=diarization,
        segments=segments,
        created_at=datetime.now().astimezone().isoformat(),
    )


def _export_result(
    result: PipelineResult,
    config: Config,
    progress: ProgressCallback | None,
) -> list[Path]:
    """Export a merged result using only backend-neutral common models."""
    if progress is not None:
        progress(ProgressEvent("export", 0.0, 1.0, "出力ファイルを生成しています"))
    output_paths = export_all(
        result,
        config.general.output_dir,
        config.output.formats,
        ExportOptions(
            speaker_labels=config.output.speaker_labels,
            show_speaker=config.output.show_speaker,
            srt_bom=config.output.srt_bom,
            newline=config.output.newline,
        ),
    )
    if progress is not None:
        progress(ProgressEvent("export", 1.0, 1.0, "出力が完了しました"))
    return output_paths


def _asr_options(config: Config, backend_name: str | None = None) -> ASROptions:
    """Build backend-neutral ASR options from effective settings."""
    word_timestamps = True
    if (backend_name or config.asr.backend) == "whisper-cpp":
        word_timestamps = config.asr.word_timestamps == "always" or (
            config.asr.word_timestamps == "auto" and config.diarization.enabled
        )
    return ASROptions(
        language=config.asr.language,
        initial_prompt=config.asr.initial_prompt,
        vad_filter=config.asr.vad_filter,
        beam_size=config.asr.beam_size,
        condition_on_previous_text=config.asr.condition_on_previous_text,
        word_timestamps=word_timestamps,
    )


def _transcribe_asr(
    pool: BackendPool,
    config: Config,
    audio_path: Path,
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
) -> TranscriptionResult:
    """Resolve the pooled backend once so reuse logging and options stay accurate."""
    backend = pool.asr(config)
    return backend.transcribe(
        audio_path,
        _asr_options(config, backend.name),
        progress,
        cancel,
    )


def _diarization_options(config: Config) -> DiarizationOptions:
    """Build backend-neutral speaker constraints from zero-as-auto settings."""
    return DiarizationOptions(
        num_speakers=_positive_or_none(config.diarization.num_speakers),
        min_speakers=_positive_or_none(config.diarization.min_speakers),
        max_speakers=_positive_or_none(config.diarization.max_speakers),
    )


def _validate_input(input_path: Path) -> None:
    """Reject absent or non-file inputs before creating a job."""
    if not input_path.exists():
        raise InputFileNotFoundError(f"入力ファイルが見つかりません: {input_path}")
    if not input_path.is_file():
        raise UnsupportedInputError(f"単一の音声または動画ファイルを指定してください: {input_path}")


def _report_skip(progress: ProgressCallback | None, stage: StageName) -> None:
    """Expose resume decisions to CLI and future GUI consumers."""
    if progress is not None:
        progress(ProgressEvent(stage, 1.0, 1.0, f"{stage} は完了済みのためスキップ"))
    logging.getLogger(__name__).info("ステージ再利用: %s", stage)


def _positive_or_none(value: int) -> int | None:
    """Translate TOML's zero-as-auto convention to backend option None."""
    return value if value > 0 else None


def _check_cancel(cancel: CancelToken | None) -> None:
    """Raise promptly at pipeline stage boundaries."""
    if cancel is not None:
        cancel.raise_if_cancelled()


def _copy_segment(segment: Segment) -> Segment:
    """Keep the no-diarization output independent from ASR containers."""
    return Segment(
        start=segment.start,
        end=segment.end,
        text=segment.text,
        words=list(segment.words),
        speaker=segment.speaker,
    )
