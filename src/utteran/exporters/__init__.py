"""Transcription result exporters and registry."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from utteran.exporters.base import Exporter
from utteran.types import ExportOptions, PipelineResult


def create_exporter(format_name: str) -> Exporter:
    """Instantiate an exporter without exposing format modules to the pipeline."""
    if format_name == "srt":
        from utteran.exporters.srt import SRTExporter

        return SRTExporter()
    if format_name == "vtt":
        from utteran.exporters.vtt import VTTExporter

        return VTTExporter()
    if format_name == "json":
        from utteran.exporters.json_exporter import JSONExporter

        return JSONExporter()
    if format_name == "txt":
        from utteran.exporters.text import TextExporter

        return TextExporter()
    if format_name == "md":
        from utteran.exporters.markdown import MarkdownExporter

        return MarkdownExporter()
    raise ValueError(f"未対応の出力形式です: {format_name}")


def export_all(
    result: PipelineResult,
    output_dir: Path,
    formats: Sequence[str],
    options: ExportOptions,
) -> list[Path]:
    """Write all requested formats using one collision-free filename stem."""
    exporters = [create_exporter(name) for name in dict.fromkeys(formats)]
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _available_stem(output_dir, result.input_path.stem, exporters)
    paths: list[Path] = []
    for exporter in exporters:
        path = output_dir / f"{stem}.{exporter.extension}"
        exporter.write(result, path, options)
        paths.append(path)
    return paths


def _available_stem(output_dir: Path, base_stem: str, exporters: list[Exporter]) -> str:
    """Choose a shared stem whose requested output paths do not exist."""
    index = 0
    while True:
        candidate = base_stem if index == 0 else f"{base_stem}_{index}"
        if all(not (output_dir / f"{candidate}.{item.extension}").exists() for item in exporters):
            return candidate
        index += 1
