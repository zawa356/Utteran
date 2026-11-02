from __future__ import annotations

import json
from pathlib import Path

import pytest

from utteran_contracts.worker import WorkerRequest


@pytest.fixture
def worker_request(tmp_path: Path) -> WorkerRequest:
    settings = json.loads(
        (Path(__file__).parents[1] / "docs/architecture/phase-a/examples.json").read_text(
            encoding="utf-8"
        )
    )["preset"]["settings"]
    settings["asr"]["initial_prompt"] = "PRIVATE_PROMPT_秘密\nsecond line"
    return WorkerRequest(
        request_id="req-test",
        job_id="job-test",
        attempt_id="attempt-test",
        profile="cpu",
        input_path=str(tmp_path / "private.wav"),
        settings=settings,
        context=dict(
            base_config={
                "general": {
                    "output_dir": str(tmp_path / "output"),
                    "job_dir": str(tmp_path / "jobs"),
                }
            },
            asr_backend="faster-whisper",
            asr_model="large-v3-turbo",
            asr_device="cpu",
            diarization_device="cpu",
            compute_type="int8",
        ),
    )
