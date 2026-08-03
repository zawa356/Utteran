from __future__ import annotations

from pathlib import Path

from utteran.asr.whisper_cpp import _convert_result, build_command, parse_progress
from utteran.config import WhisperCppConfig
from utteran.models.catalog import get_model
from utteran.types import ASROptions


def test_build_command_enables_dtw_only_when_words_requested(tmp_path: Path) -> None:
    entry = get_model("whisper-cpp:large-v3-turbo-q5_0")
    base = (tmp_path / "whisper-cli", tmp_path / "model.bin", tmp_path / "audio.wav")
    with_words = build_command(
        *base,
        tmp_path / "out",
        entry,
        WhisperCppConfig(variant="openvino_vulkan"),
        "openvino_vulkan",
        ASROptions(language=None, word_timestamps=True),
    )
    without_words = build_command(
        *base,
        tmp_path / "out",
        entry,
        WhisperCppConfig(variant="vulkan"),
        "vulkan",
        ASROptions(word_timestamps=False),
    )

    assert with_words[with_words.index("-l") + 1] == "auto"
    assert with_words[-3:] == ["--dtw", "large.v3.turbo", "--no-flash-attn"]
    assert "-oved" in with_words and "--device" in with_words
    assert "--dtw" not in without_words and "--no-flash-attn" not in without_words


def test_parse_progress_is_injectable() -> None:
    assert parse_progress("whisper_print_progress_callback: progress =  42%") == 42
    assert parse_progress("unrelated") is None


def test_convert_result_discards_words_when_dtw_was_silently_disabled() -> None:
    entry = get_model("whisper-cpp:base")
    data = {
        "result": {"language": "ja"},
        "transcription": [
            {
                "offsets": {"from": 0, "to": 1000},
                "text": " test",
                "tokens": [
                    {"text": " test", "t_dtw": -1, "offsets": {"from": 0, "to": 1000}, "p": 0.9}
                ],
            }
        ],
    }

    result = _convert_result(data, entry, "cpu", True)

    assert result.segments[0].words == []
    assert result.language == "ja"
