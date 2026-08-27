from __future__ import annotations

import json
import runpy
from pathlib import Path

from utteran.types import DiarizationResult, Segment, SpeakerTurn, TranscriptionResult

_STATISTICS = runpy.run_path(Path(__file__).parents[1] / "tools" / "diarization_stats.py")
collect_statistics = _STATISTICS["collect_statistics"]


def test_statistics_tool_returns_counts_without_transcript_text(tmp_path: Path) -> None:
    transcription = TranscriptionResult(
        [Segment(0.0, 10.0, "sensitive transcript")],
        "ja",
        10.0,
        "fake",
        "fake",
        "cpu",
    )
    diarization = DiarizationResult(
        [SpeakerTurn(0.0, 5.0, "A"), SpeakerTurn(5.0, 10.0, "B")],
        [SpeakerTurn(0.0, 5.0, "A"), SpeakerTurn(5.0, 10.0, "B")],
        2,
        "fake",
        "fake",
        "cpu",
    )
    (tmp_path / "asr.json").write_text(
        json.dumps({"schema_version": 1, "result": transcription.to_dict()}), encoding="utf-8"
    )
    (tmp_path / "diarization.json").write_text(
        json.dumps({"schema_version": 1, "result": diarization.to_dict()}), encoding="utf-8"
    )

    result = collect_statistics(tmp_path)

    assert result["exclusive_diarization"]["speaker_change_count"] == 1
    assert result["selected_alignment"]["split_segment_count"] == 1
    assert result["asr_intervals"]["covered_seconds"] == 10.0
    assert result["merged_intervals"]["long_gap_count"] == 0
    assert result["merged_intervals"]["unknown_segment_count"] == 0
    assert "sensitive transcript" not in json.dumps(result)
