from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _scenario_namespace() -> dict[str, Any]:
    return runpy.run_path(Path(__file__).parents[1] / "tools" / "acceptance" / "scenarios.py")


def test_security_token_scan_ignores_config_key_but_finds_token_values() -> None:
    namespace = _scenario_namespace()
    token_bytes_pattern = namespace["TOKEN_BYTES_PATTERN"]
    assert token_bytes_pattern.search(b'hf_token = "dummy-token-value"') is None
    assert token_bytes_pattern.search(b"value=hf_acceptanceDummyToken123456") is not None


def test_security_scan_accepts_an_individual_file(tmp_path: Path) -> None:
    namespace = _scenario_namespace()
    validate_security_scan = namespace["validate_security_scan"]
    report = tmp_path / "report.md"
    report.write_text("token-free acceptance report", encoding="utf-8")

    validate_security_scan(Path(__file__).parents[1], (report,))


def test_backend_reuse_checks_only_each_jobs_latest_run(tmp_path: Path) -> None:
    namespace = _scenario_namespace()
    validate_backend_reuse = namespace["validate_backend_reuse"]
    assert callable(validate_backend_reuse)
    job_a = tmp_path / "a"
    job_a.mkdir()
    old = [
        {"message": "ジョブ開始: old"},
        {"message": "ASRバックエンドをロード"},
    ]
    latest_a = [
        {"message": "ジョブ開始: latest-a"},
        {"message": "ASRバックエンドをロード"},
    ]
    latest_b = [
        {"message": "ジョブ開始: latest-b"},
        {"message": "ASRバックエンドを再利用"},
    ]
    (job_a / "utteran.log").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in old + latest_a + latest_b)
        + "\n",
        encoding="utf-8",
    )
    validate_backend_reuse.__globals__["_find_job"] = lambda _jobs, _input_path: job_a

    validate_backend_reuse(tmp_path, (Path("a"), Path("b")))


def test_postflight_accepts_environment_skips_and_no_retained_cuda_job(tmp_path: Path) -> None:
    namespace = _scenario_namespace()
    validate_postflight = namespace["validate_postflight"]
    assert callable(validate_postflight)
    results = tmp_path / "results.jsonl"
    records = [
        {
            "id": f"G{index}-1",
            "group": f"G{index}",
            "result": "skip" if index == 13 else "pass",
        }
        for index in range(14)
    ]
    results.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
    jobs = tmp_path / "jobs"
    testdata = tmp_path / "testdata"
    jobs.mkdir()
    testdata.mkdir()

    validate_postflight(results, jobs, tmp_path / "missing.mp4", testdata)


def test_interrupt_log_probe_handles_force_run_replacing_a_longer_log(tmp_path: Path) -> None:
    probe = _scenario_namespace()["_new_log_contains"]
    log = tmp_path / "utteran.log"
    log.write_bytes(b"old log" * 100)
    old_size = log.stat().st_size
    log.write_bytes(b"Processing audio with duration 03:00")

    assert probe(log, old_size, b"Processing audio with duration") is True


@pytest.mark.skipif(os.name != "nt", reason="Windows console control regression")
def test_ctrl_c_is_confined_to_the_child_console() -> None:
    probe = """
import sys
import time
from tools.acceptance.scenarios import _interrupt, _start

child = _start([
    sys.executable,
    "-c",
    "import time;\\ntry: time.sleep(60)\\nexcept KeyboardInterrupt: raise SystemExit(130)",
])
time.sleep(1)
exit_code, _stdout, _stderr = _interrupt(child)
raise SystemExit(0 if exit_code == 130 else 1)
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
