from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import utteran.devices as device_module
from utteran.asr.faster_whisper import FasterWhisperBackend
from utteran.devices import LibraryReport
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
    # Must not depend on whether this machine already has "tiny" downloaded
    # to its real, unmocked model cache - only on the alias resolution path.
    monkeypatch.setattr("utteran.asr.faster_whisper.find_runtime_model", lambda *_a, **_kw: None)
    backend = FasterWhisperBackend()

    backend.load("tiny", "cpu", "auto")

    assert captured["model_id"] == "tiny"
    assert captured["local_files_only"] is True
    assert captured["compute_type"] == "int8"


def test_load_imports_ctranslate2_with_torch_import_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CTranslate2's model_spec unconditionally imports torch; on this project's
    Intel profile that torch build's native DLL init can spend minutes of real
    CPU time (see devices.py::suppress_torch_import). `load()` must shield its
    `from faster_whisper import WhisperModel` with the same stand-in so CPU
    inference never pays that cost."""
    calls: list[str] = []

    class FakeLoader:
        def __init__(self, model_id: str, **_kwargs: object) -> None:
            pass

    from contextlib import contextmanager

    @contextmanager
    def fake_suppress_torch_import() -> Any:
        calls.append("entered")
        yield True
        calls.append("exited")

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeLoader)
    monkeypatch.setattr(
        "utteran.asr.faster_whisper.suppress_torch_import", fake_suppress_torch_import
    )

    FasterWhisperBackend().load("tiny", "cpu", "auto")

    assert calls == ["entered", "exited"]


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
    monkeypatch.setattr(
        "utteran.devices.detect_cuda_libraries",
        lambda: LibraryReport("cudnn", "cublas"),
    )

    # Since Phase 5k, `detect_ctranslate2()` no longer calls CTranslate2
    # in-process - each probe runs in a fresh subprocess via
    # `run_isolated_probe()`, so monkeypatching the `ctranslate2` module
    # directly (the old approach) no longer reaches it. Fake the isolated
    # probe boundary itself instead, matching tests/test_devices.py.
    def fake_run_isolated_probe(
        name: str,
        label: str,
        _timeout_seconds: float,
        *,
        argument: str | None = None,
        command: list[str] | None = None,
    ) -> device_module._ProbeRun:
        outcome = device_module.ProbeOutcome(name, label, "completed", 0.01)
        if name == "ctranslate2_cpu":
            return device_module._ProbeRun(outcome, {"version": "test", "compute_types": ["int8"]})
        if name == "ctranslate2_cuda_count":
            return device_module._ProbeRun(outcome, {"version": "test", "count": 1})
        if name == "nvidia_metadata":
            return device_module._ProbeRun(outcome, {"stdout": ""})
        if name == "ctranslate2_cuda":
            return device_module._ProbeRun(outcome, {"compute_types": ["float16"]})
        raise AssertionError(f"unexpected probe in this test: {name}")

    monkeypatch.setattr(device_module, "run_isolated_probe", fake_run_isolated_probe)
    backend = FasterWhisperBackend()

    backend.load("tiny", "auto", "auto")

    assert attempts == [("cuda", "float16"), ("cpu", "int8")]
    assert backend._device == "cpu"
