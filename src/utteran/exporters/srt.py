"""SubRip subtitle exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters._view import render_options, transcript_view
from utteran.exporters.base import Exporter
from utteran.types import ExportOptions, PipelineResult
from utteran_contracts.transcript_export import render_srt


class SRTExporter(Exporter):
    """Render numbered SubRip cues."""

    extension: ClassVar[str] = "srt"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render SRT with comma millisecond separators."""
        return render_srt(transcript_view(result), render_options(options))
