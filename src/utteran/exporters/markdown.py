"""Human-readable Markdown transcript exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters._view import render_options, transcript_view
from utteran.exporters.base import Exporter
from utteran.types import ExportOptions, PipelineResult
from utteran_contracts.transcript_export import render_md


class MarkdownExporter(Exporter):
    """Render processing metadata and a speaker-attributed transcript."""

    extension: ClassVar[str] = "md"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render metadata followed by transcript paragraphs."""
        return render_md(transcript_view(result), render_options(options))
