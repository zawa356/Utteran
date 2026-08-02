from __future__ import annotations

from pathlib import Path

import pytest

from utteran.asr.base import ASRBackend
from utteran.batch import discover_inputs, run_batch
from utteran.config import Config
from utteran.errors import AudioDecodeError, ModelNotFoundError
from utteran.jobs import JobStore
from utteran.types import (
    ASROptions,
    CancelToken,
    DeviceInfo,
    ProgressCallback,
    Segment,
    TranscriptionResult,
    Word,
)


class CountingASR(ASRBackend):
    name = "counting"

    def __init__(self, *, fail_marker: bytes | None = None) -> None:
        self.fail_marker = fail_marker
        self.load_count = 0
        self.transcribe_count = 0
        self.unload_count = 0

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def available_devices(cls) -> list[DeviceInfo]:
        return []

    def load(self, model_id: str, device: str, compute_type: str) -> None:
        self.load_count += 1

    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        self.transcribe_count += 1
        if self.fail_marker is not None and self.fail_marker in audio_path.read_bytes():
            raise ModelNotFoundError("test failure")
        word = Word(0.0, 1.0, "hello")
        return TranscriptionResult(
            [Segment(0.0, 1.0, "hello", [word])],
            "en",
            1.0,
            self.name,
            "fake",
            "cpu",
        )

    def unload(self) -> None:
        self.unload_count += 1


class KeyboardInterruptASR(CountingASR):
    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        raise KeyboardInterrupt


def fake_normalize(
    input_path: Path,
    output_path: Path,
    *,
    ffmpeg_path: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
) -> Path:
    output_path.write_bytes(input_path.name.encode())
    return output_path


def _config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "general": {
                "output_dir": tmp_path / "output",
                "job_dir": tmp_path / "jobs",
            },
            "diarization": {"enabled": False},
            "output": {"formats": ["txt"]},
        }
    )


def test_discover_inputs_is_stable_recursive_and_glob_adjustable(tmp_path: Path) -> None:
    (tmp_path / "B.wav").write_bytes(b"")
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.bin").write_bytes(b"")

    assert [path.name for path in discover_inputs(tmp_path)] == ["a.mp4", "B.wav"]
    assert [path.name for path in discover_inputs(tmp_path, recursive=True)] == [
        "a.mp4",
        "B.wav",
    ]
    assert discover_inputs(
        tmp_path,
        recursive=True,
        include=("*.bin",),
    ) == [nested / "c.bin"]
    assert discover_inputs(
        tmp_path,
        include=("*.wav", "*.mp4"),
        exclude=("B.*",),
    ) == [tmp_path / "a.mp4"]


def test_batch_reuses_one_loaded_backend_for_all_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "a.wav").write_bytes(b"a")
    (tmp_path / "b.wav").write_bytes(b"b")
    backend = CountingASR()
    caplog.set_level("INFO")
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    summary = run_batch(tmp_path, _config(tmp_path), asr_backend=backend)

    assert summary.success_count == 2
    assert summary.exit_code == 0
    assert backend.load_count == 1
    assert backend.transcribe_count == 2
    assert backend.unload_count == 1
    assert sum("ASRバックエンドをロード" in record.message for record in caplog.records) == 1
    assert sum("ASRバックエンドを再利用" in record.message for record in caplog.records) == 1


def test_batch_continues_after_failure_and_returns_partial_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "good.wav").write_bytes(b"good")
    (tmp_path / "bad.wav").write_bytes(b"bad")
    backend = CountingASR(fail_marker=b"bad.wav")
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    summary = run_batch(tmp_path, _config(tmp_path), asr_backend=backend)

    assert summary.success_count == 1
    assert summary.failed_count == 1
    assert summary.exit_code == 5
    assert [item.path.name for item in summary.items if item.status == "failed"] == ["bad.wav"]


def test_batch_counts_decode_failure_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "bad.wav").write_bytes(b"bad")
    (tmp_path / "good.wav").write_bytes(b"good")

    def normalize_with_decode_failure(
        input_path: Path,
        output_path: Path,
        *,
        ffmpeg_path: Path | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> Path:
        if input_path.name == "bad.wav":
            raise AudioDecodeError("decode failed")
        return fake_normalize(
            input_path,
            output_path,
            ffmpeg_path=ffmpeg_path,
            progress=progress,
            cancel=cancel,
        )

    monkeypatch.setattr("utteran.pipeline.normalize_audio", normalize_with_decode_failure)

    summary = run_batch(tmp_path, _config(tmp_path), asr_backend=CountingASR())

    assert summary.success_count == 1
    assert summary.failed_count == 1
    assert summary.skipped_count == 0
    assert summary.exit_code == 5


def test_batch_all_failures_return_general_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "bad.wav").write_bytes(b"bad")
    backend = CountingASR(fail_marker=b"bad.wav")
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    summary = run_batch(tmp_path, _config(tmp_path), asr_backend=backend)

    assert summary.failed_count == 1
    assert summary.exit_code == 1


def test_batch_keyboard_interrupt_leaves_resumable_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    config = _config(tmp_path)
    backend = KeyboardInterruptASR()
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    with pytest.raises(KeyboardInterrupt):
        run_batch(tmp_path, config, asr_backend=backend)

    job = JobStore(config.effective_job_dir).open(input_path)
    assert job.manifest.stages["audio"].status == "done"
    assert job.manifest.stages["asr"].status == "pending"
    assert backend.unload_count == 1
