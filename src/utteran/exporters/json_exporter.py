"""Versioned full-fidelity JSON exporter."""

from __future__ import annotations

import json
from typing import ClassVar

from utteran.exporters.base import Exporter, display_speaker
from utteran.types import ExportOptions, PipelineResult


class JSONExporter(Exporter):
    """Render the design-specified schema version 1 document."""

    extension: ClassVar[str] = "json"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render all segment and word metadata as JSON."""
        diarization = result.diarization
        segments = [
            {
                "start": segment.start,
                "end": segment.end,
                "speaker": display_speaker(segment.speaker, options),
                "text": segment.text,
                "words": [
                    {
                        "start": word.start,
                        "end": word.end,
                        "text": word.text,
                        "probability": word.probability,
                    }
                    for word in segment.words
                ],
            }
            for segment in result.segments
        ]
        speakers = list(
            dict.fromkeys(
                speaker
                for segment in result.segments
                if (speaker := display_speaker(segment.speaker, options)) is not None
            )
        )
        payload = {
            "schema_version": 1,
            "input": {
                "path": str(result.input_path),
                "duration": result.transcription.duration,
            },
            "processing": {
                "asr": {
                    "backend": result.transcription.backend,
                    "model": result.transcription.model_id,
                    "device": result.transcription.device,
                },
                "diarization": (
                    None
                    if diarization is None
                    else {
                        "backend": diarization.backend,
                        "model": diarization.model_id,
                        "device": diarization.device,
                    }
                ),
                "created_at": result.created_at,
            },
            "speakers": speakers,
            "segments": segments,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
