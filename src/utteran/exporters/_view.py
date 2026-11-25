"""PipelineResult -> the shared pure read model (``utteran_contracts.transcript_export``)."""

from __future__ import annotations

from utteran.types import ExportOptions, PipelineResult
from utteran_contracts.transcript_export import (
    DiarizationView,
    EngineView,
    RenderOptions,
    SegmentView,
    TranscriptView,
    WordView,
)


def transcript_view(result: PipelineResult) -> TranscriptView:
    """Only the values the exporters render; no coercion of numbers."""
    transcription = result.transcription
    diarization = result.diarization
    return TranscriptView(
        input_path=str(result.input_path),
        duration=transcription.duration,
        asr=EngineView(transcription.backend, transcription.model_id, transcription.device),
        diarization=(
            None
            if diarization is None
            else DiarizationView(
                diarization.backend, diarization.model_id, diarization.device, diarization.memory
            )
        ),
        created_at=result.created_at,
        segments=tuple(
            SegmentView(
                start=segment.start,
                end=segment.end,
                text=segment.text,
                speaker=segment.speaker,
                speaker_confidence=segment.speaker_confidence,
                words=tuple(
                    WordView(word.start, word.end, word.text, word.probability)
                    for word in segment.words
                ),
            )
            for segment in result.segments
        ),
    )


def render_options(options: ExportOptions) -> RenderOptions:
    return RenderOptions(
        speaker_labels=options.speaker_labels,
        show_speaker=options.show_speaker,
        srt_bom=options.srt_bom,
        newline=options.newline,
    )
