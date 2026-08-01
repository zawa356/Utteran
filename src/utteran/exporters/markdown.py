"""Human-readable Markdown transcript exporter."""

from __future__ import annotations

from typing import ClassVar

from utteran.exporters.base import Exporter, display_speaker
from utteran.types import ExportOptions, PipelineResult


class MarkdownExporter(Exporter):
    """Render processing metadata and a speaker-attributed transcript."""

    extension: ClassVar[str] = "md"

    def render(self, result: PipelineResult, options: ExportOptions) -> str:
        """Render metadata followed by transcript paragraphs."""
        transcription = result.transcription
        diarization = result.diarization
        lines = [
            f"# {result.input_path.stem}",
            "",
            "## メタ情報",
            "",
            f"- 入力: `{result.input_path}`",
            f"- 長さ: {transcription.duration:.3f} 秒",
            f"- 作成日時: {result.created_at}",
            f"- ASR: {transcription.backend} / {transcription.model_id} / {transcription.device}",
        ]
        if diarization is None:
            lines.append("- 話者分離: 無効")
        else:
            lines.append(
                f"- 話者分離: {diarization.backend} / {diarization.model_id} / {diarization.device}"
            )
        lines.extend(["", "## 文字起こし", ""])
        for segment in result.segments:
            text = segment.text.strip()
            label = display_speaker(segment.speaker, options)
            if options.show_speaker and label:
                lines.append(f"**{label}**: {text}")
            else:
                lines.append(text)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
