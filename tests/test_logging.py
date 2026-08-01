from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from utteran.logging import configure_logging, job_log, mask_secrets, register_secret


def test_mask_secrets_masks_hf_tokens_and_registered_values() -> None:
    register_secret("custom-secret-value")

    masked = mask_secrets("hf_abcdefgh and custom-secret-value")

    assert masked == "hf_**** and ****"


def test_structured_log_never_contains_token(tmp_path: Path) -> None:
    log_path = tmp_path / "utteran.log"
    configure_logging("info", log_path)

    logging.getLogger("test").error("failed with %s", "hf_supersecret")

    content = log_path.read_text(encoding="utf-8")
    assert "hf_supersecret" not in content
    assert json.loads(content)["message"] == "failed with hf_****"


def test_quiet_console_still_records_redacted_job_info(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "utteran.log"
    configure_logging("error")

    with job_log(log_path):
        logging.getLogger("test").info("stage hf_jobsecret")

    content = log_path.read_text(encoding="utf-8")
    assert "hf_jobsecret" not in content
    assert "hf_****" in content
    assert capsys.readouterr().err == ""
