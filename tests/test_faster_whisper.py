from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import utteran.devices as device_module
from utteran.asr.faster_whisper import FasterWhisperBackend
from utteran.devices import FasterWhisperSelection, LibraryReport
from utteran.errors import BackendUnavailableError, ModelNotFoundError, VramExhaustedError
from utteran.logging import close_runtime_logging, configure_runtime_logging
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


# Phase bugfix-k: translated errors used to be raised `from None` with nothing
# logged, so no one could tell *why* faster-whisper inference failed. These tests
# pin the diagnostic contract with injected exceptions - no real model needed.


class ExplodingWhisperModel:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def transcribe(self, _path: str, **_options: object) -> tuple[list[Any], Any]:
        raise self._error


def _loaded_backend(error: Exception) -> FasterWhisperBackend:
    backend = FasterWhisperBackend()
    backend._model = ExplodingWhisperModel(error)
    backend._model_id = "fake-model"
    backend._device = "cuda:0"
    return backend


def _runtime_app_log(tmp_path: Path, level: str = "info") -> Path:
    runtime = configure_runtime_logging(level=level, log_dir=tmp_path / "logs", command="run")
    return runtime.log_dir / "app.log"


def _diagnostic_records(app_log: Path) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in app_log.read_text(encoding="utf-8").splitlines()]
    return [record for record in records if record.get("event") == "backend_exception"]


def test_inference_failure_logs_original_exception_and_tells_user_where(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app_log = _runtime_app_log(tmp_path)
    original = RuntimeError("cuDNN failed with status CUDNN_STATUS_EXECUTION_FAILED")
    try:
        with pytest.raises(BackendUnavailableError) as raised:
            _loaded_backend(original).transcribe(tmp_path / "audio.wav", ASROptions())
    finally:
        close_runtime_logging()

    message = str(raised.value)
    assert message.startswith("faster-whisper の推論に失敗しました。")
    assert "原因: RuntimeError: cuDNN failed with status CUDNN_STATUS_EXECUTION_FAILED" in message
    assert str(app_log) in message
    assert raised.value.__cause__ is original
    # The user-facing text stays one short paragraph, never a traceback.
    assert "Traceback" not in message
    assert "\n" not in message

    detail = _diagnostic_records(app_log)
    assert len(detail) == 1
    assert detail[0]["level"] == "error"
    assert detail[0]["backend"] == "faster-whisper"
    assert detail[0]["phase"] == "推論"
    assert detail[0]["error_class"] == "RuntimeError"
    assert "Traceback (most recent call last)" in detail[0]["message"]
    assert "CUDNN_STATUS_EXECUTION_FAILED" in detail[0]["message"]
    # Not verbose: the traceback must not reach the console.
    assert "Traceback" not in capsys.readouterr().err


def test_inference_failure_traceback_reaches_console_only_with_verbose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _runtime_app_log(tmp_path, level="debug")
    try:
        with pytest.raises(BackendUnavailableError):
            _loaded_backend(RuntimeError("boom")).transcribe(tmp_path / "a.wav", ASROptions())
    finally:
        close_runtime_logging()

    err = capsys.readouterr().err
    assert "faster-whisper 推論の例外: RuntimeError: boom" in err
    assert "Traceback (most recent call last)" in err


def test_inference_vram_exhaustion_is_still_classified(tmp_path: Path) -> None:
    _runtime_app_log(tmp_path)
    try:
        with pytest.raises(VramExhaustedError, match="VRAM が不足しました") as raised:
            _loaded_backend(RuntimeError("CUDA failed with error out of memory")).transcribe(
                tmp_path / "audio.wav", ASROptions()
            )
    finally:
        close_runtime_logging()
    assert "原因: RuntimeError: CUDA failed with error out of memory" in str(raised.value)


def test_inference_diagnostics_never_record_prompt_terms_home_or_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Glossary terms (proper nouns) in the initial prompt, the user's home
    directory, and token-shaped values must not survive into the log or the
    user message, even when the backend exception quotes them."""
    fake_home = tmp_path / "Users" / "山田太郎"
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: fake_home))
    app_log = _runtime_app_log(tmp_path)
    leaked_path = fake_home / "x.onnx"
    error = ValueError(f"cannot tokenize 前澤研究所 near {leaked_path} token hf_abcdefghijkl")
    try:
        with pytest.raises(BackendUnavailableError) as raised:
            _loaded_backend(error).transcribe(
                tmp_path / "audio.wav",
                ASROptions(initial_prompt="前澤研究所、ウッテラン"),
            )
    finally:
        close_runtime_logging()

    for text in (str(raised.value), app_log.read_text(encoding="utf-8")):
        assert "前澤研究所" not in text
        assert "山田太郎" not in text
        assert "hf_abcdefghijkl" not in text
    assert "<redacted>" in str(raised.value)
    assert "~" in str(raised.value)


def test_model_load_failure_logs_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenLoader:
        def __init__(self, _model_id: str, **_kwargs: object) -> None:
            raise RuntimeError("Unsupported compute type int8 for this model")

    monkeypatch.setattr("faster_whisper.WhisperModel", BrokenLoader)
    app_log = _runtime_app_log(tmp_path)
    try:
        with pytest.raises(BackendUnavailableError, match="モデルを初期化できません") as raised:
            FasterWhisperBackend().load("tiny", "cpu", "int8")
    finally:
        close_runtime_logging()

    assert "原因: RuntimeError: Unsupported compute type int8" in str(raised.value)
    assert isinstance(raised.value.__cause__, RuntimeError)
    detail = _diagnostic_records(app_log)
    assert [record["phase"] for record in detail] == ["モデル読み込み"]
    assert "Unsupported compute type int8" in detail[0]["message"]


def test_model_missing_is_still_classified_and_keeps_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingLoader:
        def __init__(self, _model_id: str, **_kwargs: object) -> None:
            raise ValueError("not cached")

    monkeypatch.setattr("faster_whisper.WhisperModel", FailingLoader)
    _runtime_app_log(tmp_path)
    try:
        with pytest.raises(ModelNotFoundError, match="暗黙にダウンロードしません") as raised:
            FasterWhisperBackend().load("missing-model", "cpu", "int8")
    finally:
        close_runtime_logging()
    assert "原因: ValueError: not cached" in str(raised.value)


def test_cuda_fallback_records_why_cuda_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The auto-device CUDA->CPU fallback must keep working, and the CUDA
    exception that triggered it is now logged instead of discarded."""

    class CudaFailingLoader:
        def __init__(self, _model_id: str, **kwargs: object) -> None:
            if kwargs["device"] == "cuda":
                raise RuntimeError("CUDA driver version is insufficient")

    def fake_select(device: str, _compute_type: str) -> FasterWhisperSelection:
        if device == "auto":
            return FasterWhisperSelection("cuda", 0, "float16")
        return FasterWhisperSelection("cpu", 0, "int8")

    monkeypatch.setattr("faster_whisper.WhisperModel", CudaFailingLoader)
    monkeypatch.setattr("utteran.asr.faster_whisper.select_faster_whisper_device", fake_select)
    app_log = _runtime_app_log(tmp_path)
    backend = FasterWhisperBackend()
    try:
        backend.load("tiny", "auto", "auto")
    finally:
        close_runtime_logging()

    assert backend._device == "cpu"
    detail = _diagnostic_records(app_log)
    assert [record["phase"] for record in detail] == ["CUDA 初期化"]
    assert detail[0]["level"] == "warning"
    assert "CUDA driver version is insufficient" in detail[0]["message"]
    assert backend._device == "cpu"


@pytest.mark.parametrize(("device", "expected_calls"), [("cuda", 1), ("cpu", 0)])
def test_load_preloads_cublas_only_for_cuda(
    monkeypatch: pytest.MonkeyPatch, device: str, expected_calls: int
) -> None:
    """bugfix-k root cause: CUDA inference needs cuBLAS already in the process."""
    calls: list[str] = []

    class FakeLoader:
        def __init__(self, _model_id: str, **_kwargs: object) -> None:
            pass

    def fake_select(requested: str, _compute_type: str) -> FasterWhisperSelection:
        if requested == "cuda":
            return FasterWhisperSelection("cuda", 0, "int8_float32")
        return FasterWhisperSelection("cpu", 0, "int8")

    monkeypatch.setattr("faster_whisper.WhisperModel", FakeLoader)
    monkeypatch.setattr("utteran.asr.faster_whisper.select_faster_whisper_device", fake_select)
    monkeypatch.setattr(
        "utteran.asr.faster_whisper.preload_ctranslate2_cuda_libraries",
        lambda: calls.append("preload") or ("cublas64_12.dll",),
    )

    FasterWhisperBackend().load("tiny", device, "auto")

    assert len(calls) == expected_calls
