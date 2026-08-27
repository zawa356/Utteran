from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from utteran.cli import app
from utteran.diarization_quality import (
    DiarizationGroundTruth,
    evaluate_diarization,
    segments_from_dict,
)
from utteran.types import Segment

FIXTURES = Path(__file__).parent / "fixtures" / "diarization_quality"
runner = CliRunner()


def _load_hypothesis(name: str) -> list[Segment]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return segments_from_dict(payload)


def test_reference_evaluator_measures_reported_failure_patterns() -> None:
    reference = DiarizationGroundTruth.load(FIXTURES / "meeting_ground_truth.json")

    metrics = evaluate_diarization(reference, _load_hypothesis("meeting_naive_hypothesis.json"))

    assert metrics["speaker_mapping"] == {
        "SPEAKER_02": "REF_B",
        "SPEAKER_07": "REF_A",
        "SPEAKER_11": "REF_C",
    }
    assert metrics["speaker_confusion_seconds"] == pytest.approx(0.45)
    assert metrics["false_alarm_seconds"] == pytest.approx(1.5)
    assert metrics["diarization_error_rate"] == pytest.approx(1.95 / 14.15, abs=1e-6)
    assert metrics["mid_word_speaker_boundary_count"] == 3
    assert metrics["short_speaker_turn_count"] == 3
    assert metrics["single_word_speaker_turn_count"] == 2
    assert metrics["unknown_ratio"] == 0.0
    assert metrics["acknowledgement_retained_ratio"] == 1.0
    assert metrics["boundary_error_seconds"] == {
        "count": 5,
        "mean": 0.0,
        "p95": 0.0,
        "max": 0.0,
    }


def test_perfect_timeline_has_zero_der_and_preserves_acknowledgement() -> None:
    reference = DiarizationGroundTruth.load(FIXTURES / "meeting_ground_truth.json")
    hypothesis = [
        Segment(turn.start, turn.end, "", speaker=f"HYP_{turn.speaker}") for turn in reference.turns
    ]

    metrics = evaluate_diarization(reference, hypothesis)

    assert metrics["diarization_error_rate"] == 0.0
    assert metrics["mid_word_speaker_boundary_count"] == 0
    assert metrics["acknowledgement_retained_ratio"] == 1.0


def test_unknown_is_counted_as_missed_speech_and_reported_by_duration() -> None:
    reference = DiarizationGroundTruth.load(FIXTURES / "meeting_ground_truth.json")
    hypothesis = [Segment(0.5, 4.0, "", speaker="UNKNOWN")]

    metrics = evaluate_diarization(reference, hypothesis)

    assert metrics["miss_seconds"] == pytest.approx(14.15)
    assert metrics["unknown_ratio"] == 1.0


def test_eval_cli_applies_machine_readable_quality_gates(tmp_path: Path) -> None:
    output = tmp_path / "metrics.json"
    result = runner.invoke(
        app,
        [
            "eval",
            str(FIXTURES / "meeting_ground_truth.json"),
            str(FIXTURES / "meeting_expected_hypothesis.json"),
            "--output",
            str(output),
            "--max-der",
            "0",
            "--max-mid-word-boundaries",
            "0",
            "--max-unknown-ratio",
            "0.2",
            "--min-acknowledgement-retention",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["diarization_error_rate"] == 0.0


def test_eval_cli_rejects_the_naive_boundary_sequence() -> None:
    result = runner.invoke(
        app,
        [
            "eval",
            str(FIXTURES / "meeting_ground_truth.json"),
            str(FIXTURES / "meeting_naive_hypothesis.json"),
            "--max-mid-word-boundaries",
            "0",
        ],
    )

    assert result.exit_code == 1
    assert "mid_word_speaker_boundary_count" in result.stdout
