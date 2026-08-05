from __future__ import annotations

import json
import subprocess
from pathlib import Path

from utteran.errors import ConfigurationError
from utteran.exporters import export_all
from utteran.exporters.json_exporter import JSONExporter
from utteran.exporters.markdown import MarkdownExporter
from utteran.exporters.srt import SRTExporter
from utteran.exporters.text import TextExporter
from utteran.exporters.vtt import VTTExporter
from utteran.types import (
    DiarizationResult,
    ExportOptions,
    PipelineResult,
    Segment,
    SpeakerTurn,
    TranscriptionResult,
    Word,
)


def sample_result(tmp_path: Path) -> PipelineResult:
    segment = Segment(
        3661.234,
        3662.005,
        " hello",
        [Word(3661.234, 3662.005, " hello", 0.9)],
        "SPEAKER_00",
    )
    transcription = TranscriptionResult([segment], "en", 3662.005, "faster-whisper", "tiny", "cpu")
    diarization = DiarizationResult(
        [SpeakerTurn(3661.0, 3663.0, "SPEAKER_00")],
        None,
        1,
        "pyannote",
        "community-1",
        "cpu",
    )
    return PipelineResult(
        tmp_path / "meeting.mp4",
        transcription,
        diarization,
        [segment],
        "2026-08-01T00:00:00+09:00",
    )


def test_srt_and_vtt_timestamp_formats(tmp_path: Path) -> None:
    result = sample_result(tmp_path)
    options = ExportOptions(speaker_labels={"SPEAKER_00": "Alice"})

    srt = SRTExporter().render(result, options)
    vtt = VTTExporter().render(result, options)

    assert "01:01:01,234 --> 01:01:02,005" in srt
    assert "Alice: hello" in srt
    assert vtt.startswith("WEBVTT\n\n01:01:01.234 --> 01:01:02.005")


def test_json_schema_contains_words_and_processing_metadata(tmp_path: Path) -> None:
    result = sample_result(tmp_path)

    payload = json.loads(JSONExporter().render(result, ExportOptions()))

    assert payload["schema_version"] == 1
    assert payload["processing"]["asr"]["backend"] == "faster-whisper"
    assert payload["speakers"] == ["SPEAKER_00"]
    assert payload["segments"][0]["words"][0]["probability"] == 0.9


def test_text_and_markdown_apply_output_only_speaker_labels(tmp_path: Path) -> None:
    result = sample_result(tmp_path)
    options = ExportOptions(speaker_labels={"SPEAKER_00": "田中"})

    assert TextExporter().render(result, options) == "田中: hello\n"
    markdown = MarkdownExporter().render(result, options)
    assert "**田中**: hello" in markdown
    assert "faster-whisper / tiny / cpu" in markdown
    assert result.segments[0].speaker == "SPEAKER_00"


def test_export_all_uses_shared_collision_suffix_and_srt_bom(tmp_path: Path) -> None:
    result = sample_result(tmp_path)
    (tmp_path / "meeting.srt").write_text("existing", encoding="utf-8")

    paths = export_all(
        result,
        tmp_path,
        ["srt", "vtt", "json", "txt", "md"],
        ExportOptions(srt_bom=True, newline="crlf"),
    )

    assert {path.name for path in paths} == {
        "meeting_1.srt",
        "meeting_1.vtt",
        "meeting_1.json",
        "meeting_1.txt",
        "meeting_1.md",
    }
    assert (tmp_path / "meeting_1.srt").read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in (tmp_path / "meeting_1.vtt").read_bytes()


def test_export_all_rejects_git_visible_transcript_directory(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    visible = tmp_path / "public-documents"

    try:
        export_all(sample_result(tmp_path), visible, ["json", "txt", "md"], ExportOptions())
    except ConfigurationError as exc:
        assert ".gitignore" in str(exc)
    else:
        raise AssertionError("Git-visible transcript output was accepted")


def test_export_all_accepts_ignored_transcript_directory(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("/transcripts/\n", encoding="utf-8")

    paths = export_all(
        sample_result(tmp_path), tmp_path / "transcripts", ["json", "txt", "md"], ExportOptions()
    )

    assert {path.suffix for path in paths} == {".json", ".txt", ".md"}
