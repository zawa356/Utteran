from __future__ import annotations

import logging
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from utteran.asr.openvino_genai import (
    OpenVINOGenAIBackend,
    degraded_word_statistics,
)
from utteran.asr.registry import NON_SPACE_LANGUAGES, create_asr_backend
from utteran.config import Config
from utteran.errors import CancelledError, ConfigurationError
from utteran.models.catalog import get_model
from utteran.types import ASROptions, CancelToken, ProgressEvent, Segment, Word


def _wav(path: Path, seconds: float = 0.1) -> None:
    samples = [0] * round(16_000 * seconds)
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _loaded_backend(result: object, *, diarization: bool = False) -> OpenVINOGenAIBackend:
    backend = OpenVINOGenAIBackend(diarization_enabled=diarization)
    backend._entry = get_model("openvino-genai:large-v3-turbo-int8")
    backend._device = "cpu"
    backend._pipeline = SimpleNamespace(generate=lambda *_args, **_kwargs: result)
    return backend


def test_is_available_returns_false_when_import_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)

    assert OpenVINOGenAIBackend.is_available() is False


def test_fake_pipeline_converts_chunks_and_words_to_common_types(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _wav(audio)
    result = SimpleNamespace(
        language="en",
        texts=[" hello world"],
        chunks=[SimpleNamespace(start_ts=0.0, end_ts=0.1, text=" hello world")],
        words=[
            SimpleNamespace(start_ts=0.0, end_ts=0.04, word=" hello"),
            SimpleNamespace(start_ts=0.04, end_ts=0.09, word=" world"),
        ],
    )
    backend = _loaded_backend(result)
    events: list[ProgressEvent] = []

    converted = backend.transcribe(audio, ASROptions(language="en"), events.append)

    assert converted.backend == "openvino-genai"
    assert converted.model_id == "large-v3-turbo-int8"
    assert converted.language == "en"
    assert converted.segments[0].text == " hello world"
    assert [word.text for word in converted.segments[0].words] == [" hello", " world"]
    assert events[0].completed == 0.0 and events[-1].completed == 1.0


def test_cancel_is_honored_before_native_generation(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _wav(audio)
    token = CancelToken()
    token.cancel()
    backend = _loaded_backend(SimpleNamespace())

    with pytest.raises(CancelledError):
        backend.transcribe(audio, ASROptions(), cancel=token)


def test_cancel_is_honored_after_native_generation(tmp_path: Path) -> None:
    audio = tmp_path / "normalized.wav"
    _wav(audio)
    token = CancelToken()
    backend = _loaded_backend(SimpleNamespace())
    backend._pipeline = SimpleNamespace(generate=lambda *_args, **_kwargs: token.cancel())

    with pytest.raises(CancelledError):
        backend.transcribe(audio, ASROptions(), cancel=token)


def test_explicit_registry_selection_has_no_fallback_backend() -> None:
    backend = create_asr_backend("openvino-genai")

    assert type(backend) is OpenVINOGenAIBackend
    assert backend.name == "openvino-genai"


def test_degraded_words_are_logged_and_retained(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    audio = tmp_path / "normalized.wav"
    _wav(audio)
    long_text = "x" * 35
    result = SimpleNamespace(
        language="en",
        texts=[long_text],
        chunks=[SimpleNamespace(start_ts=0.0, end_ts=0.1, text=long_text)],
        words=[SimpleNamespace(start_ts=0.0, end_ts=0.1, word=long_text)],
    )
    backend = _loaded_backend(result, diarization=True)
    events: list[ProgressEvent] = []

    with caplog.at_level(logging.WARNING):
        converted = backend.transcribe(audio, ASROptions(), events.append)

    assert converted.segments[0].words[0].text == long_text
    assert "話者割当" in caplog.text
    warning = next(event for event in events if event.event_type == "warning")
    assert warning.details["degraded_count"] == 1
    assert warning.details["discarded_count"] == 0


def test_degraded_statistics_detects_fused_segment_without_discarding() -> None:
    text = "a" * 30
    segment = Segment(0.0, 1.0, text, [Word(0.0, 1.0, text)])

    statistics = degraded_word_statistics([segment])

    assert statistics == {
        "word_count": 1,
        "degraded_count": 1,
        "fused_segment_count": 1,
        "average_degraded_characters": 30.0,
        "discarded_count": 0,
    }


@pytest.mark.parametrize("language", [None, *sorted(NON_SPACE_LANGUAGES)])
def test_diarization_rejects_auto_and_every_non_space_language(language: str | None) -> None:
    config = Config.model_validate(
        {
            "asr": {"backend": "openvino-genai", "language": language},
            "diarization": {"enabled": True},
        }
    )

    with pytest.raises(ConfigurationError, match="whisper-cpp"):
        create_asr_backend("openvino-genai", config)


def test_english_diarization_and_japanese_without_diarization_are_allowed() -> None:
    english = Config.model_validate(
        {
            "asr": {"backend": "openvino-genai", "language": "en"},
            "diarization": {"enabled": True},
        }
    )
    japanese_no_diarization = Config.model_validate(
        {
            "asr": {"backend": "openvino-genai", "language": "ja"},
            "diarization": {"enabled": False},
        }
    )

    assert isinstance(create_asr_backend("openvino-genai", english), OpenVINOGenAIBackend)
    assert isinstance(
        create_asr_backend("openvino-genai", japanese_no_diarization), OpenVINOGenAIBackend
    )
