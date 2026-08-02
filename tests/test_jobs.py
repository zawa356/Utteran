from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from utteran.config import Config
from utteran.errors import JobLockedError
from utteran.jobs import (
    JobLock,
    JobStore,
    _process_exists,
    canonical_json,
    fingerprint_input,
    job_id_from_input_hash,
    stage_config_hashes,
)


def _input(tmp_path: Path) -> Path:
    path = tmp_path / "meeting.mp4"
    path.write_bytes(b"media-data")
    return path


def test_job_id_depends_only_on_input_fingerprint(tmp_path: Path) -> None:
    input_path = _input(tmp_path)
    fingerprint = fingerprint_input(input_path)

    first = job_id_from_input_hash(fingerprint.hash)
    second = job_id_from_input_hash(fingerprint.hash)

    assert first == second
    assert len(first) == 16


def test_canonical_json_is_order_independent_and_fixes_float_precision() -> None:
    left = canonical_json({"z": 0.3, "a": {"y": 2, "x": 1}})
    right = canonical_json({"a": {"x": 1, "y": 2}, "z": 0.3000000000001})

    assert left == right
    assert '"0.300000000000"' in left


def test_stage_hashes_invalidate_only_affected_dependencies() -> None:
    base = Config()
    hashes = stage_config_hashes(base, "input-hash")

    output_changed = base.model_copy(deep=True)
    output_changed.output.formats = ["txt"]
    output_hashes = stage_config_hashes(output_changed, "input-hash")
    assert {stage for stage in hashes if hashes[stage] != output_hashes[stage]} == {"export"}

    destination_changed = base.model_copy(deep=True)
    destination_changed.general.output_dir = Path("another-output")
    destination_hashes = stage_config_hashes(destination_changed, "input-hash")
    assert {stage for stage in hashes if hashes[stage] != destination_hashes[stage]} == {"export"}

    asr_changed = base.model_copy(deep=True)
    asr_changed.asr.beam_size = 7
    asr_hashes = stage_config_hashes(asr_changed, "input-hash")
    assert {stage for stage in hashes if hashes[stage] != asr_hashes[stage]} == {
        "asr",
        "merge",
        "export",
    }


def test_manifest_recovers_running_stage_as_pending(tmp_path: Path) -> None:
    input_path = _input(tmp_path)
    store = JobStore(tmp_path / "jobs")
    job = store.open(input_path)
    hashes = stage_config_hashes(Config(), job.manifest.input.hash)
    job.start_stage("audio", hashes["audio"])

    reopened = store.open(input_path)

    assert reopened.manifest.stages["audio"].status == "pending"


def test_reconcile_preserves_independent_diarization(tmp_path: Path) -> None:
    input_path = _input(tmp_path)
    store = JobStore(tmp_path / "jobs")
    job = store.open(input_path)
    config = Config()
    hashes = stage_config_hashes(config, job.manifest.input.hash)

    job.audio_path.write_bytes(b"wav")
    job.complete_stage("audio", hashes["audio"], [job.audio_path])
    asr_path = job.write_intermediate("asr", {"segments": []})
    job.complete_stage("asr", hashes["asr"], [asr_path])
    diarization_path = job.write_intermediate("diarization", None)
    job.complete_stage("diarization", hashes["diarization"], [diarization_path])
    merged_path = job.write_intermediate("merge", {"segments": []})
    job.complete_stage("merge", hashes["merge"], [merged_path])
    output = tmp_path / "result.txt"
    output.write_text("result", encoding="utf-8")
    job.complete_stage("export", hashes["export"], [output])

    changed = config.model_copy(deep=True)
    changed.asr.beam_size = 8
    changed_hashes = stage_config_hashes(changed, job.manifest.input.hash)
    job.reconcile(changed_hashes)

    assert job.manifest.stages["audio"].status == "done"
    assert job.manifest.stages["diarization"].status == "done"
    assert job.manifest.stages["asr"].status == "pending"
    assert job.manifest.stages["merge"].status == "pending"
    assert job.manifest.stages["export"].status == "pending"


def test_corrupt_manifest_is_preserved_and_recreated(tmp_path: Path) -> None:
    input_path = _input(tmp_path)
    store = JobStore(tmp_path / "jobs")
    job = store.open(input_path)
    job.manifest_path.write_text("not-json", encoding="utf-8")

    with pytest.warns(UserWarning, match="新規作成"):
        recovered = store.open(input_path)

    assert recovered.manifest.job_id == job.manifest.job_id
    assert list(job.root.glob("manifest.corrupt.*.json"))


def test_intermediate_schema_mismatch_is_not_reused(tmp_path: Path) -> None:
    job = JobStore(tmp_path / "jobs").open(_input(tmp_path))
    path = job.intermediate_path("asr")
    path.write_text(json.dumps({"schema_version": 999, "result": {}}), encoding="utf-8")

    with pytest.warns(UserWarning, match="スキーマが古い"):
        assert job.read_intermediate("asr") is None


def test_lock_rejects_live_owner_and_recovers_stale_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / ".lock"
    first = JobLock(lock_path)
    first.acquire()
    try:
        with pytest.raises(JobLockedError, match=str(os.getpid())):
            JobLock(lock_path).acquire()
    finally:
        first.release()

    lock_path.write_text(
        json.dumps({"pid": 123456, "started_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("utteran.jobs._process_exists", lambda _pid: False)
    with JobLock(lock_path):
        assert lock_path.is_file()
    assert not lock_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows PID liveness regression")
def test_windows_process_probe_detects_live_and_finished_child() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _process_exists(process.pid)
    finally:
        process.terminate()
        process.wait(timeout=10)

    assert not _process_exists(process.pid)


def test_job_list_and_failed_clean_selection(tmp_path: Path) -> None:
    job = JobStore(tmp_path / "jobs").open(_input(tmp_path))
    job.fail_stage("audio", "hash", "decode failed")
    store = JobStore(tmp_path / "jobs")

    summaries = store.list_jobs()
    failed = store.clean_candidates(failed=True)

    assert summaries[0].status == "failed"
    assert [item.job_id for item in failed] == [job.manifest.job_id]


def test_corrupt_job_is_visible_and_cleanable(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs")
    job = store.open(_input(tmp_path))
    job.manifest_path.write_text("broken", encoding="utf-8")

    summaries = store.list_jobs()
    failed = store.clean_candidates(failed=True)

    assert summaries[0].status == "corrupt"
    assert failed[0].job_id == job.manifest.job_id
