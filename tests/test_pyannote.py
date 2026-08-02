from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from utteran.config import TokenProvider
from utteran.diarization.pyannote import PyannoteBackend, _torch_cuda_usable
from utteran.errors import HuggingFaceTokenMissingError
from utteran.types import DiarizationOptions, ProgressEvent


class EmptyTokenProvider(TokenProvider):
    def get_token(self) -> str | None:
        return None


class FakeAnnotation:
    def __init__(self, values: list[tuple[float, float, str]]) -> None:
        self._values = values

    def itertracks(self, *, yield_label: bool) -> list[tuple[Any, None, str]]:
        assert yield_label
        return [
            (SimpleNamespace(start=start, end=end), None, speaker)
            for start, end, speaker in self._values
        ]


class FakePipeline:
    def __call__(self, _audio: object, *, hook: Any, **options: object) -> Any:
        assert options == {"num_speakers": 2}
        hook(
            "segmentation",
            None,
            file={"uri": "in-memory-audio"},
            total=2,
            completed=1,
        )
        regular = FakeAnnotation([(0.0, 1.0, "SPEAKER_01"), (0.8, 2.0, "SPEAKER_00")])
        exclusive = FakeAnnotation([(0.0, 0.9, "SPEAKER_01"), (0.9, 2.0, "SPEAKER_00")])
        return SimpleNamespace(
            speaker_diarization=regular,
            exclusive_speaker_diarization=exclusive,
        )


class FakeCuda:
    def __init__(self) -> None:
        self.synchronized: list[int] = []

    def is_available(self) -> bool:
        return True

    def device_count(self) -> int:
        return 1

    def synchronize(self, index: int) -> None:
        self.synchronized.append(index)


class FakeCudaTensor:
    def __add__(self, _value: object) -> FakeCudaTensor:
        return self

    def cpu(self) -> FakeCudaTensor:
        return self

    def item(self) -> float:
        return 2.0


class FakeTorch:
    def __init__(self, *, fail_kernel: bool = False) -> None:
        self.cuda = FakeCuda()
        self.fail_kernel = fail_kernel

    def ones(self, _size: int, *, device: str) -> FakeCudaTensor:
        assert device == "cuda:0"
        if self.fail_kernel:
            raise RuntimeError("no kernel image is available")
        return FakeCudaTensor()


def test_remote_model_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(PyannoteBackend, "is_available", classmethod(lambda _cls: True))
    monkeypatch.setattr(
        "utteran.diarization.pyannote.find_runtime_model", lambda *_args, **_kwargs: None
    )
    backend = PyannoteBackend(EmptyTokenProvider())

    with pytest.raises(HuggingFaceTokenMissingError, match="settings/tokens"):
        backend.load("pyannote/speaker-diarization-community-1", "cpu")


def test_diarize_converts_both_annotation_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = PyannoteBackend(EmptyTokenProvider())
    backend._pipeline = FakePipeline()
    backend._model_id = "fake-model"
    backend._device = "cpu"
    monkeypatch.setattr("utteran.diarization.pyannote._load_pcm_waveform", lambda _path: {})
    events: list[ProgressEvent] = []

    result = backend.diarize(
        tmp_path / "audio.wav",
        DiarizationOptions(num_speakers=2),
        events.append,
    )

    assert result.num_speakers == 2
    assert len(result.turns) == 2
    assert result.exclusive_turns is not None
    assert result.exclusive_turns[0].end == 0.9
    assert any(event.message == "segmentation" for event in events)


def test_torch_cuda_probe_executes_and_synchronizes_a_kernel() -> None:
    torch = FakeTorch()

    assert _torch_cuda_usable(torch, 0)
    assert torch.cuda.synchronized == [0]


def test_torch_cuda_probe_rejects_allocation_without_compatible_kernel() -> None:
    assert not _torch_cuda_usable(FakeTorch(fail_kernel=True), 0)
