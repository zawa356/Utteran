"""Resumable single-file orchestration over persistent job stages."""

from __future__ import annotations

import logging
import time
import wave
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

from utteran.align import align_transcription_with_statistics, speaker_turn_statistics
from utteran.asr.base import ASRBackend
from utteran.asr.registry import create_asr_backend
from utteran.audio import normalize_audio
from utteran.config import Config, TokenProvider, default_token_provider
from utteran.diarization.base import DiarizationBackend
from utteran.diarization.registry import (
    create_diarization_backend,
    preflight_diarization_backend,
)
from utteran.errors import (
    CancelledError,
    InputFileNotFoundError,
    MemoryBudgetError,
    UnsupportedInputError,
    VramExhaustedError,
)
from utteran.exporters import export_all
from utteran.jobs import STAGES, Job, JobStore, StageName, stage_config_hashes
from utteran.logging import execution_context, job_log, mask_secrets, structured_event
from utteran.memory import (
    GIB,
    CalibrationStore,
    MemoryAssessment,
    MemoryDecision,
    measure_peak,
    plan_diarization_memory,
)
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
        self._provided_diarization = diarization_backend
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
        self._diarization = self._diarization or self._provided_diarization
        self._diarization = self._diarization or create_diarization_backend(
            config.diarization.backend, self._token_provider
        )
        self._diarization.load(config.diarization.model, config.diarization.device)
        self._diarization_key = key
        logging.getLogger(__name__).info("話者分離バックエンドをロード: %s/%s/%s", *key)
        return self._diarization

    def reset_diarization(self) -> None:
        """Unload a failed attempt while allowing an injected backend to be reloaded."""
        if self._diarization is not None:
            self._diarization.unload()
        self._diarization = None
        self._diarization_key = None

    @property
    def provided_diarization_name(self) -> str | None:
        """Expose the injected backend identity for dependency-isolated execution."""
        return None if self._provided_diarization is None else self._provided_diarization.name

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
    calibration_store: CalibrationStore | None = None,
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
    selected_calibration_store = calibration_store or CalibrationStore()
    try:
        job_log_level = "debug" if config.general.log_level == "debug" else "info"
        with (
            job.lock(force=force_unlock),
            job_log(job.root / "utteran.log", job_log_level),
            execution_context(job.manifest.job_id),
        ):
            logging.getLogger(__name__).info("ジョブ開始: %s", job.manifest.job_id)
            structured_event("job_started", job_id=job.manifest.job_id)
            job.reconcile(hashes, force=force or not resume)
            if progress is not None:
                planned = [stage for stage in STAGES if not job.is_done(stage, hashes[stage])]
                progress(
                    ProgressEvent(
                        "job",
                        0.0,
                        event_type="job_resolved",
                        details={
                            "job_id": job.manifest.job_id,
                            "resumed": bool(resume and len(planned) < len(STAGES)),
                            "stages": planned,
                        },
                    )
                )
            result = _run_stages(
                job,
                input_path,
                config,
                hashes,
                pool,
                selected_token_provider,
                executed_stages,
                stage_durations,
                selected_calibration_store,
                progress,
                cancel,
            )
            output_paths = [Path(path) for path in job.manifest.stages["export"].artifacts]
            logging.getLogger(__name__).info("ジョブ完了: %s", job.manifest.job_id)
            structured_event("job_completed", job_id=job.manifest.job_id)
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
    calibration_store: CalibrationStore,
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
            progress,
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
                progress,
                lambda: _transcribe_asr_measured(
                    pool,
                    config,
                    job.audio_path,
                    progress,
                    cancel,
                    calibration_store,
                ),
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
                    progress,
                    lambda: _diarize_with_memory_guard(
                        pool,
                        config,
                        job.audio_path,
                        progress,
                        cancel,
                        calibration_store,
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
                progress,
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
                progress,
                lambda: _merge_result(input_path, transcription, diarization, config, progress),
                lambda value: [
                    job.write_intermediate("merge", cast(PipelineResult, value).to_dict())
                ],
            ),
        )

    if not job.is_done("export", hashes["export"]):
        exported = cast(
            list[Path],
            _execute_stage(
                job,
                "export",
                hashes["export"],
                executed_stages,
                stage_durations,
                cancel,
                progress,
                lambda: _export_result(result, config, progress),
                lambda value: cast(list[Path], value),
            ),
        )
        job.write_presentation(
            output_dir=config.general.output_dir,
            formats=config.output.formats,
            speaker_labels=config.output.speaker_labels,
            outputs=exported,
        )
    else:
        _report_skip(progress, "export")
        _report_outputs(progress, job.manifest.stages["export"].artifacts)
    return result


def _execute_stage(
    job: Job,
    stage: StageName,
    config_hash: str,
    executed_stages: list[str],
    stage_durations: dict[str, float],
    cancel: CancelToken | None,
    progress: ProgressCallback | None,
    operation: Callable[[], object],
    artifacts: Callable[[object], list[Path]],
) -> object:
    """Persist one pending/running/done/failed state machine transition."""
    _check_cancel(cancel)
    job.start_stage(stage, config_hash)
    executed_stages.append(stage)
    logging.getLogger(__name__).info("ステージ開始: %s", stage)
    if progress is not None:
        progress(ProgressEvent(stage, 0.0, event_type="stage_start"))
    started = time.perf_counter()
    try:
        value = operation()
        _check_cancel(cancel)
        paths = artifacts(value)
        job.complete_stage(stage, config_hash, paths)
        elapsed = time.perf_counter() - started
        stage_durations[stage] = elapsed
        logging.getLogger(__name__).info("ステージ完了: %s (%.3f秒)", stage, elapsed)
        structured_event("stage_completed", stage=stage, duration_seconds=elapsed)
        if progress is not None:
            progress(
                ProgressEvent(
                    stage,
                    1.0,
                    1.0,
                    event_type="stage_done",
                    duration_seconds=elapsed,
                )
            )
            if stage == "export":
                for path in paths:
                    progress(
                        ProgressEvent(
                            stage,
                            1.0,
                            1.0,
                            event_type="output_written",
                            details={"path": str(path), "format": path.suffix.lstrip(".")},
                        )
                    )
        return value
    except (CancelledError, KeyboardInterrupt):
        job.interrupt_stage(stage)
        logging.getLogger(__name__).info("ステージ中断: %s", stage)
        structured_event("stage_interrupted", stage=stage)
        raise
    except Exception as exc:
        job.fail_stage(stage, config_hash, mask_secrets(str(exc)))
        logging.getLogger(__name__).error("ステージ失敗: %s: %s", stage, exc)
        structured_event(
            "stage_error",
            level=logging.ERROR,
            stage=stage,
            error_class=type(exc).__name__,
        )
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
        structured_event(
            "diarization_statistics",
            regular=speaker_turn_statistics(diarization.turns),
            exclusive=(
                None
                if diarization.exclusive_turns is None
                else speaker_turn_statistics(diarization.exclusive_turns)
            ),
        )
        segments, alignment_statistics = align_transcription_with_statistics(
            transcription,
            diarization,
            AlignmentOptions(
                max_nearest_distance=config.alignment.max_nearest_distance,
                min_segment_duration=config.alignment.min_segment_duration,
                min_segment_words=config.alignment.min_segment_words,
                speaker_switch_penalty=config.alignment.speaker_switch_penalty,
                silence_switch_threshold=config.alignment.silence_switch_threshold,
                min_clear_turn_duration=config.alignment.min_clear_turn_duration,
                max_same_speaker_bridge_gap=config.alignment.max_same_speaker_bridge_gap,
                unknown_emission_score=config.alignment.unknown_emission_score,
                min_unknown_duration=config.alignment.min_unknown_duration,
                min_unknown_characters=config.alignment.min_unknown_characters,
                max_unsupported_fragment_duration=(
                    config.alignment.max_unsupported_fragment_duration
                ),
                max_unsupported_fragment_characters=(
                    config.alignment.max_unsupported_fragment_characters
                ),
                min_fragment_speaker_overlap=config.alignment.min_fragment_speaker_overlap,
                merge_gap=config.alignment.merge_gap,
                renumber_speakers=config.alignment.renumber_speakers,
            ),
        )
        structured_event("alignment_statistics", **alignment_statistics)
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


def _transcribe_asr_measured(
    pool: BackendPool,
    config: Config,
    audio_path: Path,
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
    calibration_store: CalibrationStore,
) -> TranscriptionResult:
    """Record a successful ASR stage separately from diarization."""
    started = time.perf_counter()
    with measure_peak() as monitor:
        result = _transcribe_asr(pool, config, audio_path, progress, cancel)
    elapsed = time.perf_counter() - started
    minutes = _wav_minutes(audio_path)
    audio_seconds = minutes * 60.0
    structured_event(
        "asr_timing",
        backend=result.backend,
        model=result.model_id,
        device=result.device,
        duration_seconds=elapsed,
        audio_seconds=audio_seconds,
        realtime_factor=(elapsed / audio_seconds if audio_seconds > 0 else None),
    )
    if monitor.peak_bytes is not None and minutes > 0:
        calibration_store.record(
            "asr",
            result.backend,
            result.device,
            minutes,
            monitor.peak_bytes,
        )
    return result


def _diarize_with_memory_guard(
    pool: BackendPool,
    config: Config,
    audio_path: Path,
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
    calibration_store: CalibrationStore,
) -> DiarizationResult:
    """Apply preflight policy and one auto-only CPU retry for runtime OOM."""
    requested = config.diarization.device
    selected = _resolve_diarization_device(config, pool)
    minutes = _wav_minutes(audio_path)
    backend_name = pool.provided_diarization_name or config.diarization.backend
    decision = plan_diarization_memory(
        guard=config.diarization.memory_guard,
        requested_device=requested,
        selected_device=selected,
        backend=backend_name,
        audio_minutes=minutes,
        safety_margin=config.diarization.memory_safety_margin,
        store=calibration_store,
    )
    _report_memory_decision(decision, config.diarization.memory_guard, progress)
    effective = config.model_copy(deep=True)
    effective.diarization.device = decision.effective_device
    fallback = (
        None
        if decision.fallback_reason is None
        else {
            "from": decision.selected_device,
            "to": decision.effective_device,
            "reason": decision.fallback_reason,
            "trigger": "preflight",
        }
    )
    try:
        result, peak = _run_diarization_attempt(
            pool, effective, audio_path, progress, cancel, calibration_store, minutes
        )
        oom_retry = False
    except VramExhaustedError as exc:
        may_retry = (
            config.diarization.memory_guard in {"auto", "off"}
            and requested == "auto"
            and decision.effective_device.startswith(("cuda", "xpu"))
        )
        if not may_retry:
            raise VramExhaustedError(
                f"{exc} 明示deviceまたはmemory_guard={config.diarization.memory_guard}のため"
                "自動退避は行いません。"
            ) from None
        logging.getLogger(__name__).warning(
            "話者分離OOMを捕捉したためCPUへ1回だけ再試行します: %s", exc
        )
        if progress is not None:
            progress(
                ProgressEvent(
                    "diarization",
                    0.0,
                    None,
                    "メモリ不足のためCPUで再試行します",
                    event_type="warning",
                )
            )
        pool.reset_diarization()
        effective.diarization.device = "cpu"
        result, peak = _run_diarization_attempt(
            pool,
            effective,
            audio_path,
            progress,
            cancel,
            calibration_store,
            minutes,
            record_calibration=False,
        )
        fallback = {
            "from": decision.effective_device,
            "to": "cpu",
            "reason": str(exc),
            "trigger": "oom",
        }
        oom_retry = True
    result.memory = {
        "guard": config.diarization.memory_guard,
        "requested_device": requested,
        "selected_device": selected,
        "effective_device": result.device,
        "assessment": _assessment_dict(decision.assessment),
        "fallback": fallback,
        "oom_retry": oom_retry,
        "peak_working_set_bytes": peak,
    }
    return result


def _run_diarization_attempt(
    pool: BackendPool,
    config: Config,
    audio_path: Path,
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
    calibration_store: CalibrationStore,
    minutes: float,
    *,
    record_calibration: bool = True,
) -> tuple[DiarizationResult, int | None]:
    with measure_peak() as monitor:
        result = pool.diarization(config).diarize(
            audio_path, _diarization_options(config), progress, cancel
        )
    peak = monitor.peak_bytes
    if record_calibration and peak is not None and minutes > 0:
        calibration_store.record("diarization", result.backend, result.device, minutes, peak)
    return result, peak


def _resolve_diarization_device(config: Config, pool: BackendPool) -> str:
    if config.diarization.device != "auto":
        return config.diarization.device
    if pool.provided_diarization_name is not None:
        return "cpu"
    if config.diarization.backend == "pyannote":
        from utteran.diarization.pyannote import PyannoteBackend

        return PyannoteBackend.resolve_device("auto")
    return "cpu"


def _report_memory_decision(
    decision: MemoryDecision,
    guard: str,
    progress: ProgressCallback | None,
) -> None:
    assessment = decision.assessment
    if assessment is None:
        return
    detail = _format_assessment(assessment)
    if decision.fallback_reason is not None:
        message = f"話者分離deviceを {decision.selected_device} から CPU へ退避: {detail}"
        logging.getLogger(__name__).warning(message)
        if progress is not None:
            progress(ProgressEvent("diarization", 0.0, None, message, event_type="warning"))
        return
    if assessment.status == "impossible":
        raise MemoryBudgetError(
            f"話者分離を開始できません: {detail}。"
            "CPUなどメモリ効率のよいdeviceへ切り替える、--no-diarizationで話者分離を省略する、"
            "または音声を短いファイルへ分けてください。"
        )
    if assessment.status in {"danger", "unknown"}:
        message = f"話者分離メモリ警告 (memory_guard={guard}): {detail}"
        logging.getLogger(__name__).warning(message)
        if progress is not None:
            progress(ProgressEvent("diarization", 0.0, None, message, event_type="warning"))


def _format_assessment(assessment: MemoryAssessment) -> str:
    estimate = (
        "不明"
        if assessment.estimate_bytes is None
        else f"{assessment.estimate_bytes / GIB:.2f} GiB"
    )
    budget = (
        "不明"
        if assessment.budget.usable_bytes is None
        else f"{assessment.budget.usable_bytes / GIB:.2f} GiB"
    )
    return f"判定={assessment.status}, 推定={estimate}, 予算={budget}, 理由={assessment.reason}"


def _assessment_dict(assessment: MemoryAssessment | None) -> dict[str, object] | None:
    if assessment is None:
        return None
    return {
        "status": assessment.status,
        "estimate_bytes": assessment.estimate_bytes,
        "base_bytes": assessment.base_bytes,
        "budget_bytes": assessment.budget.usable_bytes,
        "safety_margin": assessment.budget.safety_margin,
        "budget_source": assessment.budget.source,
        "model_source": None if assessment.model is None else assessment.model.source,
        "reason": assessment.reason,
    }


def _wav_minutes(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getnframes() / wav.getframerate() / 60.0
    except (EOFError, OSError, wave.Error, ZeroDivisionError):
        return 0.0


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
        progress(
            ProgressEvent(
                stage,
                1.0,
                1.0,
                f"{stage} は完了済みのためスキップ",
                event_type="stage_done",
                skipped=True,
            )
        )
    logging.getLogger(__name__).info("ステージ再利用: %s", stage)


def _report_outputs(progress: ProgressCallback | None, paths: list[str]) -> None:
    """Report reusable exports so GUI consumers can still present completed files."""
    if progress is None:
        return
    for raw_path in paths:
        path = Path(raw_path)
        progress(
            ProgressEvent(
                "export",
                1.0,
                1.0,
                event_type="output_written",
                details={"path": str(path), "format": path.suffix.lstrip(".")},
            )
        )


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
