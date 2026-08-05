"""Transcription result exporters and registry."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from utteran.errors import ConfigurationError
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
    _ensure_git_ignored_output(output_dir, [item.extension for item in exporters])
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _available_stem(output_dir, result.input_path.stem, exporters)
    paths: list[Path] = []
    for exporter in exporters:
        path = output_dir / f"{stem}.{exporter.extension}"
        exporter.write(result, path, options)
        paths.append(path)
    return paths


def _ensure_git_ignored_output(output_dir: Path, extensions: Sequence[str]) -> None:
    """Reject transcript destinations visible to Git inside a source checkout."""
    resolved = output_dir.resolve()
    repository = next(
        (parent for parent in (resolved, *resolved.parents) if (parent / ".git").exists()), None
    )
    if repository is None:
        return
    probes = [resolved / f"private-transcript.{extension}" for extension in extensions]
    for probe in probes:
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", str(probe)],
                cwd=repository,
                check=False,
                capture_output=True,
            )
        except OSError as exc:
            raise ConfigurationError("Git repository内の出力先の安全性を確認できません。") from exc
        if result.returncode != 0:
            raise ConfigurationError(
                "Git repository内では、.gitignoreの対象であるoutput、"
                "transcripts、またはutteran-output directoryを出力先に指定してください。"
            )


def _available_stem(output_dir: Path, base_stem: str, exporters: list[Exporter]) -> str:
    """Choose a shared stem whose requested output paths do not exist."""
    index = 0
    while True:
        candidate = base_stem if index == 0 else f"{base_stem}_{index}"
        if all(not (output_dir / f"{candidate}.{item.extension}").exists() for item in exporters):
            return candidate
        index += 1
