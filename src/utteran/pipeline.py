"""Single-file Phase 1 transcription orchestration."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

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
from utteran.errors import InputFileNotFoundError, UnsupportedInputError
from utteran.exporters import export_all
from utteran.types import (
    AlignmentOptions,
    ASROptions,
    CancelToken,
    DiarizationOptions,
    ExportOptions,
    PipelineOutcome,
    PipelineResult,
    ProgressCallback,
    ProgressEvent,
    Segment,
)


def run_pipeline(
    input_path: Path,
    config: Config,
    *,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
    token_provider: TokenProvider | None = None,
    asr_backend: ASRBackend | None = None,
    diarization_backend: DiarizationBackend | None = None,
) -> PipelineOutcome:
    """Normalize, transcribe, diarize, align, and export one media file."""
    if not input_path.exists():
        raise InputFileNotFoundError(f"入力ファイルが見つかりません: {input_path}")
    if not input_path.is_file():
        raise UnsupportedInputError(f"単一の音声または動画ファイルを指定してください: {input_path}")

    selected_token_provider = token_provider or default_token_provider()
    if config.diarization.enabled:
        preflight_diarization_backend(
            config.diarization.backend,
            config.diarization.model,
            selected_token_provider,
        )
    _check_cancel(cancel)

    with tempfile.TemporaryDirectory(prefix="utteran-") as temporary_directory:
        normalized_audio = Path(temporary_directory) / "audio.wav"
        normalize_audio(
            input_path,
            normalized_audio,
            ffmpeg_path=config.ffmpeg.path,
            progress=progress,
            cancel=cancel,
        )

        selected_asr = asr_backend or create_asr_backend(config.asr.backend)
        try:
            selected_asr.load(config.asr.model, config.asr.device, config.asr.compute_type)
            transcription = selected_asr.transcribe(
                normalized_audio,
                ASROptions(
                    language=config.asr.language,
                    initial_prompt=config.asr.initial_prompt,
                    vad_filter=config.asr.vad_filter,
                    beam_size=config.asr.beam_size,
                    condition_on_previous_text=config.asr.condition_on_previous_text,
                    word_timestamps=True,
                ),
                progress,
                cancel,
            )
        finally:
            selected_asr.unload()

        diarization = None
        if config.diarization.enabled:
            selected_diarization = diarization_backend or create_diarization_backend(
                config.diarization.backend,
                selected_token_provider,
            )
            try:
                selected_diarization.load(
                    config.diarization.model,
                    config.diarization.device,
                )
                diarization = selected_diarization.diarize(
                    normalized_audio,
                    DiarizationOptions(
                        num_speakers=_positive_or_none(config.diarization.num_speakers),
                        min_speakers=_positive_or_none(config.diarization.min_speakers),
                        max_speakers=_positive_or_none(config.diarization.max_speakers),
                    ),
                    progress,
                    cancel,
                )
            finally:
                selected_diarization.unload()

    _check_cancel(cancel)
    if diarization is None:
        segments = [_copy_segment(segment) for segment in transcription.segments]
    else:
        if progress is not None:
            progress(ProgressEvent("align", 0.0, 1.0, "話者を割り当てています"))
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
            progress(ProgressEvent("align", 1.0, 1.0, "話者割当が完了しました"))

    result = PipelineResult(
        input_path=input_path,
        transcription=transcription,
        diarization=diarization,
        segments=segments,
        created_at=datetime.now().astimezone().isoformat(),
    )
    _check_cancel(cancel)
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
    return PipelineOutcome(result=result, output_paths=output_paths)


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
