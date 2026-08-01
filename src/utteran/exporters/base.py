"""Shared exporter interface and presentation helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from utteran.types import ExportOptions, PipelineResult


class Exporter(ABC):
    """Render and write one output format."""

    extension: ClassVar[str]

    @abstractmethod
    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render a complete Unicode document."""

    def write(self, result: PipelineResult, path: Path, options: ExportOptions) -> None:
        """Write UTF-8 using the configured line ending and optional SRT BOM."""
        content = self.render(result, options).replace("\r\n", "\n")
        if options.newline == "crlf":
            content = content.replace("\n", "\r\n")
        encoding = "utf-8-sig" if self.extension == "srt" and options.srt_bom else "utf-8"
        path.write_text(content, encoding=encoding, newline="")


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


def format_timestamp(seconds: float, decimal_separator: str) -> str:
    """Format a non-negative timestamp with millisecond rollover."""
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{decimal_separator}{millis:03d}"
