from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from utteran_contracts.worker import WorkerRequest

# Variables that select where the product reads or writes user data, models and logs,
# or which distribution/token mode it assumes. A developer or acceptance shell may have
# them set (e.g. a real acceptance UTTERAN_DATA_ROOT); tests must never inherit them, or
# they write settings/job records into that root and fail on its contents. Tests that
# need one set it themselves with monkeypatch. Test-control flags (for example
# UTTERAN_UI2_REQUIRE_BUILD, UTTERAN_ACCEPTANCE_*) are deliberately not listed.
_AMBIENT_LOCATION_ENV = frozenset(
    {
        "UTTERAN_DATA_ROOT",
        "UTTERAN_DISTRIBUTION",
        "UTTERAN_TOKEN_MODE",
        "UTTERAN_MODEL_DIR",
        "UTTERAN_PROJECT_ROOT",
        "UTTERAN_GUI_CHILD_LOG_DIR",
    }
)
# Core Config (pydantic BaseSettings, env prefix UTTERAN_, nested delimiter "__").
_AMBIENT_CORE_SETTING = re.compile(r"^UTTERAN_[A-Z0-9]+__", re.IGNORECASE)


@pytest.fixture(autouse=True)
def _isolate_from_ambient_utteran_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.upper() in _AMBIENT_LOCATION_ENV or _AMBIENT_CORE_SETTING.match(key):
            monkeypatch.delenv(key, raising=False)


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
            asr_model_dir=str(tmp_path / "models" / "large-v3-turbo"),
            asr_device="cpu",
            diarization_device="cpu",
            compute_type="int8",
        ),
    )
