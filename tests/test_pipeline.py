from __future__ import annotations

from pathlib import Path

import pytest

from utteran.asr.base import ASRBackend
from utteran.config import Config, TokenProvider
from utteran.diarization.base import DiarizationBackend
from utteran.errors import CancelledError, VramExhaustedError
from utteran.jobs import JobStore
from utteran.memory import CalibrationStore
from utteran.pipeline import run_pipeline
from utteran.types import (
    ASROptions,
    CancelToken,
    DeviceInfo,
    DiarizationOptions,
    DiarizationResult,
    ProgressCallback,
    Segment,
    SpeakerTurn,
    TranscriptionResult,
    Word,
)


class FakeASR(ASRBackend):
    name = "fake-asr"

    def __init__(self) -> None:
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
        self.loaded = (model_id, device, compute_type)

    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        self.transcribe_count += 1
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
        self.unloaded = True


class InterruptOnceASR(FakeASR):
    def __init__(self) -> None:
        super().__init__()
        self.interrupted = False

    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        if not self.interrupted:
            self.interrupted = True
            raise CancelledError
        return super().transcribe(audio_path, options, progress, cancel)


class FakeDiarization(DiarizationBackend):
    name = "fake-diarization"

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def available_devices(cls) -> list[DeviceInfo]:
        return []

    def load(self, model_id: str, device: str) -> None:
        self.loaded = (model_id, device)

    def diarize(
        self,
        audio_path: Path,
        options: DiarizationOptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> DiarizationResult:
        self.options = options
        turn = SpeakerTurn(0.0, 1.0, "BACKEND_LABEL")
        return DiarizationResult([turn], [turn], 1, self.name, "fake", "cpu")

    def unload(self) -> None:
        self.unloaded = True


class OomOnceDiarization(FakeDiarization):
    def __init__(self) -> None:
        self.attempts = 0
        self.loaded_devices: list[str] = []

    def load(self, model_id: str, device: str) -> None:
        super().load(model_id, device)
        self.loaded_devices.append(device)

    def diarize(
        self,
        audio_path: Path,
        options: DiarizationOptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> DiarizationResult:
        self.attempts += 1
        if self.attempts == 1:
            raise VramExhaustedError("synthetic OOM")
        result = super().diarize(audio_path, options, progress, cancel)
        result.device = self.loaded[1]
        return result


class FakeTokenProvider(TokenProvider):
    def get_token(self) -> str | None:
        return "hf_fake_for_test"


def fake_normalize(
    _input_path: Path,
    output_path: Path,
    *,
    ffmpeg_path: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
) -> Path:
    output_path.write_bytes(b"fake wave")
    return output_path


def valid_fake_normalize(
    _input_path: Path,
    output_path: Path,
    **_kwargs: object,
) -> Path:
    import wave

    with wave.open(str(output_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(b"\0\0" * 16_000)
    return output_path


def test_auto_oom_retries_cpu_once_and_records_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "meeting.wav"
    input_path.write_bytes(b"input")
    config = Config.model_validate(
        {
            "general": {"output_dir": tmp_path / "output", "job_dir": tmp_path / "jobs"},
            "diarization": {"device": "auto", "memory_guard": "auto"},
            "output": {"formats": ["json"]},
        }
    )
    backend = OomOnceDiarization()
    monkeypatch.setattr("utteran.pipeline.normalize_audio", valid_fake_normalize)
    monkeypatch.setattr(
        "utteran.pipeline._resolve_diarization_device", lambda _config, _pool: "xpu:0"
    )

    outcome = run_pipeline(
        input_path,
        config,
        token_provider=FakeTokenProvider(),
        asr_backend=FakeASR(),
        diarization_backend=backend,
        calibration_store=CalibrationStore(tmp_path / "memory.json"),
    )

    assert backend.attempts == 2
    assert backend.loaded_devices == ["xpu:0", "cpu"]
    assert outcome.result.diarization is not None
    assert outcome.result.diarization.memory is not None
    assert outcome.result.diarization.memory["oom_retry"] is True
    assert outcome.result.diarization.memory["fallback"]["trigger"] == "oom"
    exported = outcome.output_paths[0].read_text(encoding="utf-8")
    assert '"oom_retry": true' in exported
    assert "synthetic OOM" in (tmp_path / "jobs" / outcome.job_id / "utteran.log").read_text(
        encoding="utf-8"
    )


def test_pipeline_passes_num_speakers_and_exports_all_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "meeting.mp4"
    input_path.write_bytes(b"video")
    config = Config.model_validate(
        {
            "general": {
                "output_dir": tmp_path / "output",
                "job_dir": tmp_path / "jobs",
            },
            "diarization": {"num_speakers": 2},
            "output": {"formats": ["srt", "vtt", "json", "txt", "md"]},
        }
    )
    fake_asr = FakeASR()
    fake_diarization = FakeDiarization()
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    outcome = run_pipeline(
        input_path,
        config,
        token_provider=FakeTokenProvider(),
        asr_backend=fake_asr,
        diarization_backend=fake_diarization,
    )

    assert fake_diarization.options.num_speakers == 2
    assert outcome.result.segments[0].speaker == "SPEAKER_00"
    assert {path.suffix for path in outcome.output_paths} == {
        ".srt",
        ".vtt",
        ".json",
        ".txt",
        ".md",
    }
    assert fake_asr.unloaded and fake_diarization.unloaded


def test_pipeline_can_skip_diarization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    config = Config.model_validate(
        {
            "general": {
                "output_dir": tmp_path / "output",
                "job_dir": tmp_path / "jobs",
            },
            "diarization": {"enabled": False},
            "output": {"formats": ["txt"]},
        }
    )
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    outcome = run_pipeline(input_path, config, asr_backend=FakeASR())

    assert outcome.result.diarization is None
    assert outcome.result.segments[0].speaker is None
    assert outcome.output_paths[0].read_text(encoding="utf-8") == "hello\n"
    assert "ステージ完了" in (tmp_path / "jobs" / outcome.job_id / "utteran.log").read_text(
        encoding="utf-8"
    )


def test_pipeline_resumes_and_output_change_runs_export_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    config = Config.model_validate(
        {
            "general": {
                "output_dir": tmp_path / "output",
                "job_dir": tmp_path / "jobs",
            },
            "diarization": {"enabled": False},
            "output": {"formats": ["txt"]},
        }
    )
    backend = FakeASR()
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    first = run_pipeline(input_path, config, asr_backend=backend)
    second = run_pipeline(input_path, config, asr_backend=backend)
    changed = config.model_copy(deep=True)
    changed.output.formats = ["md"]
    third = run_pipeline(input_path, changed, asr_backend=backend)

    assert first.executed_stages == ("audio", "asr", "diarization", "merge", "export")
    assert tuple(first.stage_durations) == ("audio", "asr", "diarization", "merge", "export")
    assert all(duration >= 0.0 for duration in first.stage_durations.values())
    assert second.executed_stages == ()
    assert second.stage_durations == {}
    assert third.executed_stages == ("export",)
    assert tuple(third.stage_durations) == ("export",)
    assert backend.transcribe_count == 1
    assert third.output_paths[0].suffix == ".md"


def test_pipeline_force_reruns_every_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    config = Config.model_validate(
        {
            "general": {
                "output_dir": tmp_path / "output",
                "job_dir": tmp_path / "jobs",
            },
            "diarization": {"enabled": False},
            "output": {"formats": ["txt"]},
        }
    )
    backend = FakeASR()
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)
    run_pipeline(input_path, config, asr_backend=backend)

    forced = run_pipeline(input_path, config, asr_backend=backend, force=True)

    assert forced.executed_stages == ("audio", "asr", "diarization", "merge", "export")
    assert backend.transcribe_count == 2


def test_interrupted_stage_resumes_after_last_completed_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "audio.wav"
    input_path.write_bytes(b"audio")
    config = Config.model_validate(
        {
            "general": {
                "output_dir": tmp_path / "output",
                "job_dir": tmp_path / "jobs",
            },
            "diarization": {"enabled": False},
            "output": {"formats": ["txt"]},
        }
    )
    backend = InterruptOnceASR()
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    with pytest.raises(CancelledError):
        run_pipeline(input_path, config, asr_backend=backend)
    interrupted_job = JobStore(config.effective_job_dir).open(input_path)
    assert interrupted_job.manifest.stages["audio"].status == "done"
    assert interrupted_job.manifest.stages["asr"].status == "pending"

    resumed = run_pipeline(input_path, config, asr_backend=backend)

    assert resumed.executed_stages == ("asr", "diarization", "merge", "export")
