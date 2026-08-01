"""SubRip subtitle exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters.base import Exporter, display_text, format_timestamp
from utteran.types import ExportOptions, PipelineResult


class SRTExporter(Exporter):
    """Render numbered SubRip cues."""

    extension: ClassVar[str] = "srt"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render SRT with comma millisecond separators."""
        cues = [
            "\n".join(
                [
                    str(index),
                    f"{format_timestamp(segment.start, ',')} --> "
                    f"{format_timestamp(segment.end, ',')}",
                    display_text(segment.text, segment.speaker, options),
                ]
            )
            for index, segment in enumerate(result.segments, start=1)
        ]
        return "\n\n".join(cues) + ("\n" if cues else "")
