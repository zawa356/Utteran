from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from utteran.logging import (
    clean_logs,
    close_runtime_logging,
    configure_logging,
    configure_runtime_logging,
    execution_context,
    job_log,
    mask_secrets,
    register_secret,
    resolve_log_dir,
    structured_event,
    write_raw_subprocess_log,
)


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


def test_log_dir_falls_back_when_install_location_is_not_writable(tmp_path: Path) -> None:
    install = tmp_path / "install"
    fallback = tmp_path / "user"

    selected, preferred, fell_back = resolve_log_dir(
        install_dir=install,
        fallback_dir=fallback,
        writable=lambda path: path == fallback,
    )

    assert selected == fallback.resolve()
    assert preferred == (install / "logs").resolve()
    assert fell_back is True


def test_cli_jsonl_contains_only_explicit_structured_events(tmp_path: Path) -> None:
    runtime = configure_runtime_logging(log_dir=tmp_path, command="test")
    logging.getLogger("test").info("recognized transcript body")
    structured_event("stage_completed", stage="asr", duration_seconds=1.25)
    close_runtime_logging()

    assert runtime.cli_log is not None
    records = [
        json.loads(line) for line in runtime.cli_log.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event"] for record in records] == ["stage_completed"]
    assert "recognized transcript body" not in runtime.cli_log.read_text(encoding="utf-8")


def test_raw_subprocess_output_is_opt_in_and_redacted(tmp_path: Path) -> None:
    configure_runtime_logging(log_dir=tmp_path, raw_enabled=False)
    with execution_context("job-one"):
        assert write_raw_subprocess_log("whisper-cpp", "body hf_rawsecret") is None

    configure_runtime_logging(log_dir=tmp_path, raw_enabled=True)
    with execution_context("job-one"):
        path = write_raw_subprocess_log("whisper-cpp", "body hf_rawsecret")
    close_runtime_logging()

    assert path == tmp_path / "raw" / "job-one" / "whisper-cpp.stderr.log"
    assert path is not None
    content = path.read_text(encoding="utf-8")
    assert "hf_rawsecret" not in content
    assert "hf_****" in content


def test_cleanup_applies_age_and_separate_capacity_limits(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    old = tmp_path / "cli" / "old.jsonl"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    timestamp = (now - timedelta(days=31)).timestamp()
    os.utime(old, (timestamp, timestamp))
    raw_old = tmp_path / "raw" / "job" / "a.log"
    raw_new = tmp_path / "raw" / "job" / "b.log"
    raw_old.parent.mkdir(parents=True)
    raw_old.write_bytes(b"123456")
    raw_new.write_bytes(b"abcdef")
    os.utime(raw_old, (now.timestamp() - 10, now.timestamp() - 10))

    result = clean_logs(
        tmp_path,
        retention_days=30,
        max_bytes=100,
        raw_max_bytes=6,
        now=now,
    )

    assert result.files_deleted == 2
    assert not old.exists()
    assert not raw_old.exists()
    assert raw_new.exists()
