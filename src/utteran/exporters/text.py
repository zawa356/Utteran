"""Plain text transcript exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters.base import Exporter, display_text
from utteran.types import ExportOptions, PipelineResult


class TextExporter(Exporter):
    """Render one speaker-attributed segment per line."""

    extension: ClassVar[str] = "txt"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render a compact plain text transcript."""
        lines = [
            display_text(segment.text, segment.speaker, options)
            for segment in result.segments
        ]
        return "\n".join(lines) + ("\n" if lines else "")
