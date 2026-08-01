from __future__ import annotations

from pathlib import Path

import pytest

from utteran.asr.base import ASRBackend
from utteran.config import Config, TokenProvider
from utteran.diarization.base import DiarizationBackend
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

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def available_devices(cls) -> list[DeviceInfo]:
        return []

    def load(self, model_id: str, device: str, compute_type: str) -> None:
        self.loaded = (model_id, device, compute_type)

    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
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
        self.unloaded = True


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


def test_pipeline_passes_num_speakers_and_exports_all_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "meeting.mp4"
    input_path.write_bytes(b"video")
    config = Config.model_validate(
        {
            "general": {"output_dir": tmp_path / "output"},
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
            "general": {"output_dir": tmp_path / "output"},
            "diarization": {"enabled": False},
            "output": {"formats": ["txt"]},
        }
    )
    monkeypatch.setattr("utteran.pipeline.normalize_audio", fake_normalize)

    outcome = run_pipeline(input_path, config, asr_backend=FakeASR())

    assert outcome.result.diarization is None
    assert outcome.result.segments[0].speaker is None
    assert outcome.output_paths[0].read_text(encoding="utf-8") == "hello\n"
