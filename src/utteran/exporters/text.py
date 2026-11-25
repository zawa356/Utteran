"""Plain text transcript exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters._view import render_options, transcript_view
from utteran.exporters.base import Exporter
from utteran.types import ExportOptions, PipelineResult
from utteran_contracts.transcript_export import render_txt


class TextExporter(Exporter):
    """Render one speaker-attributed segment per line."""

    extension: ClassVar[str] = "txt"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render a compact plain text transcript."""
        return render_txt(transcript_view(result), render_options(options))
