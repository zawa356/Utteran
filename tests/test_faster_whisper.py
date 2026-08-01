from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from utteran.asr.faster_whisper import FasterWhisperBackend
from utteran.errors import ModelNotFoundError
from utteran.types import ASROptions, ProgressEvent


class FakeWhisperModel:
    def transcribe(self, _path: str, **_options: object) -> tuple[list[Any], Any]:
        word = SimpleNamespace(start=0.1, end=0.4, word=" hello", probability=0.95)
        segment = SimpleNamespace(start=0.1, end=0.5, text=" hello", words=[word])
        info = SimpleNamespace(duration=1.0, language="en")
        return [segment], info


def test_transcribe_converts_backend_objects_and_reports_progress(tmp_path: Path) -> None:
    backend = FasterWhisperBackend()
    backend._model = FakeWhisperModel()
    backend._model_id = "fake-model"
    backend._device = "cpu"
    events: list[ProgressEvent] = []

    result = backend.transcribe(tmp_path / "audio.wav", ASROptions(), events.append)

    assert result.backend == "faster-whisper"
    assert result.segments[0].words[0].text == " hello"
    assert result.segments[0].words[0].probability == 0.95
    assert events[0].stage == "asr"
    assert events[-1].completed == events[-1].total == 1.0


def test_load_uses_local_cache_only_for_model_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeLoader:
        def __init__(self, model_id: str, **kwargs: object) -> None:
            captured["model_id"] = model_id
            captured.update(kwargs)

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeLoader)
    backend = FasterWhisperBackend()

    backend.load("tiny", "cpu", "auto")

    assert captured["model_id"] == "tiny"
    assert captured["local_files_only"] is True
    assert captured["compute_type"] == "int8"


def test_model_load_failure_is_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingLoader:
        def __init__(self, _model_id: str, **_kwargs: object) -> None:
            raise ValueError("not cached")

    monkeypatch.setattr("faster_whisper.WhisperModel", FailingLoader)

    with pytest.raises(ModelNotFoundError, match="暗黙にダウンロードしません"):
        FasterWhisperBackend().load("missing-model", "cpu", "int8")


def test_auto_device_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[tuple[str, str]] = []

    class CudaFailingLoader:
        def __init__(self, _model_id: str, **kwargs: object) -> None:
            device = str(kwargs["device"])
            compute_type = str(kwargs["compute_type"])
            attempts.append((device, compute_type))
            if device == "cuda":
                raise ValueError("float16 is unavailable")

    monkeypatch.setattr("faster_whisper.WhisperModel", CudaFailingLoader)
    monkeypatch.setattr("ctranslate2.get_cuda_device_count", lambda: 1)
    backend = FasterWhisperBackend()

    backend.load("tiny", "auto", "auto")

    assert attempts == [("cuda", "float16"), ("cpu", "int8")]
    assert backend._device == "cpu"
