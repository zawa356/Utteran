"""Versioned full-fidelity JSON exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters._view import render_options, transcript_view
from utteran.exporters.base import Exporter
from utteran.types import ExportOptions, PipelineResult
from utteran_contracts.transcript_export import render_json


class JSONExporter(Exporter):
    """Render the design-specified schema version 1 document."""

    extension: ClassVar[str] = "json"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render all segment and word metadata as JSON."""
        return render_json(transcript_view(result), render_options(options))
