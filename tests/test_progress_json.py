from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from utteran.batch import run_batch
from utteran.config import Config
from utteran.progress import JsonProgressReporter
from utteran.types import PipelineOutcome, PipelineResult, ProgressEvent, TranscriptionResult


def test_json_progress_is_one_line_utf8_versioned_and_redacted() -> None:
    stream = io.StringIO()
    reporter = JsonProgressReporter(stream)

    reporter(ProgressEvent("asr", 1.0, 2.0, "日本語\n進捗 hf_supersecret"))
    reporter.error(RuntimeError("failed hf_anothersecret"), 1)
    reporter.done(1)

    lines = stream.getvalue().splitlines()
    assert len(lines) == 3
    payloads = [json.loads(line) for line in lines]
    assert payloads[0]["schema_version"] == 1
    assert payloads[0]["event"] == "progress"
    assert payloads[0]["ratio"] == 0.5
    assert payloads[0]["message"] == "日本語\n進捗 hf_****"
    assert payloads[-1]["event"] == "done"
    assert "hf_supersecret" not in stream.getvalue()
    assert "hf_anothersecret" not in stream.getvalue()


def test_json_progress_lifecycle_contract_does_not_serialize_results() -> None:
    stream = io.StringIO()
    reporter = JsonProgressReporter(stream)
    reporter(
        ProgressEvent(
            "job",
            0,
            event_type="job_resolved",
            details={"job_id": "abc", "resumed": True, "stages": ["export"]},
        )
    )
    reporter(ProgressEvent("export", 0, event_type="stage_start"))
    reporter(
        ProgressEvent(
            "export",
            1,
            1,
            event_type="output_written",
            details={"path": "result.json", "format": "json"},
        )
    )
    reporter(ProgressEvent("export", 1, 1, event_type="stage_done", skipped=False))

    payloads = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [payload["event"] for payload in payloads] == [
        "job_resolved",
        "stage_start",
        "output_written",
        "stage_done",
    ]
    assert all("segments" not in payload and "text" not in payload for payload in payloads)


def test_batch_reports_file_boundaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = tmp_path / "a.wav"
    second = tmp_path / "b.wav"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    config = Config.model_validate({"general": {"output_dir": tmp_path / "outside"}})
    events: list[ProgressEvent] = []

    result = PipelineResult(
        input_path=first,
        transcription=TranscriptionResult([], "ja", 1, "fake", "fake", "cpu"),
        diarization=None,
        segments=[],
        created_at="now",
    )

    def fake_run(path: Path, *_args: object, **_kwargs: object) -> PipelineOutcome:
        return PipelineOutcome(result, [path.with_suffix(".json")], "job", ("export",))

    monkeypatch.setattr("utteran.batch.run_pipeline", fake_run)
    summary = run_batch(tmp_path, config, progress=events.append)

    assert summary.success_count == 2
    assert [event.event_type for event in events] == [
        "file_start",
        "file_done",
        "file_start",
        "file_done",
    ]
    assert events[-1].details["file_index"] == 2
