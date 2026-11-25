"""Shared exporter interface.

Formatting lives in ``utteran_contracts.transcript_export`` (shared with the E-C
re-export of the GUI, which must not import core); the helpers are re-exported here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from utteran.exporters._view import render_options
from utteran.types import ExportOptions, PipelineResult
from utteran_contracts.transcript_export import encode, format_timestamp

__all__ = ["Exporter", "display_speaker", "display_text", "format_timestamp"]


class Exporter(ABC):
    """Render and write one output format."""

    extension: ClassVar[str]

    @abstractmethod
    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render a complete Unicode document."""

    def write(self, result: PipelineResult, path: Path, options: ExportOptions) -> None:
        """Write UTF-8 using the configured line ending and optional SRT BOM."""
        path.write_bytes(
            encode(self.extension, self.render(result, options), render_options(options))
        )


def display_speaker(speaker: str | None, options: ExportOptions) -> str | None:
    """Apply output-only speaker label renaming."""
    if speaker is None:
        return None
    return options.speaker_labels.get(speaker, speaker)


def display_text(text: str, speaker: str | None, options: ExportOptions) -> str:
    """Render one plain speaker-prefixed transcript line."""
    clean_text = text.strip()
    label = display_speaker(speaker, options)
    if options.show_speaker and label:
        return f"{label}: {clean_text}"
    return clean_text
