"""WebVTT subtitle exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters.base import Exporter, display_text, format_timestamp
from utteran.types import ExportOptions, PipelineResult


class VTTExporter(Exporter):
    """Render WebVTT cues."""

    extension: ClassVar[str] = "vtt"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render WebVTT with period millisecond separators."""
        cues = [
            "\n".join(
                [
                    f"{format_timestamp(segment.start, '.')} --> "
                    f"{format_timestamp(segment.end, '.')}",
                    display_text(segment.text, segment.speaker, options),
                ]
            )
            for segment in result.segments
        ]
        body = "\n\n".join(cues)
        return f"WEBVTT\n\n{body}" + ("\n" if body else "")
