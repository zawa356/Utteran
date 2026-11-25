"""WebVTT subtitle exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters._view import render_options, transcript_view
from utteran.exporters.base import Exporter
from utteran.types import ExportOptions, PipelineResult
from utteran_contracts.transcript_export import render_vtt


class VTTExporter(Exporter):
    """Render WebVTT cues."""

    extension: ClassVar[str] = "vtt"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render WebVTT with period millisecond separators."""
        return render_vtt(transcript_view(result), render_options(options))
