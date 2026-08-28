from __future__ import annotations

from pathlib import Path

import pytest

from utteran.asr.whisper_cpp import (
    WhisperCppBackend,
    _convert_result,
    _stage_model,
    build_command,
    is_gpu_initialization_failure,
    parse_openvino_ir_status,
    parse_progress,
    summarize_subprocess_error,
)
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
    assert with_words[with_words.index("--max-context") + 1] == "0"
    assert "--entropy-thold" in with_words


def test_vad_and_word_timestamps_are_enabled_together(tmp_path: Path) -> None:
    vad_model = tmp_path / "vad.bin"
    vad_model.write_bytes(b"vad")
    command = build_command(
        Path("whisper-cli"),
        Path("model.bin"),
        Path("audio.wav"),
        Path("out"),
        get_model("whisper-cpp:large-v3-turbo-q5_0"),
        WhisperCppConfig(vad=True, vad_model=vad_model),
        "vulkan",
        ASROptions(word_timestamps=True),
    )

    assert "--vad" in command
    assert "--vad-model" in command
    assert "--dtw" in command
    assert "--no-flash-attn" in command


def test_debug_no_flash_attention_without_dtw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UTTERAN_DEBUG_NO_FLASH_ATTN", "1")
    command = build_command(
        tmp_path / "whisper-cli",
        tmp_path / "model.bin",
        tmp_path / "audio.wav",
        tmp_path / "out",
        get_model("whisper-cpp:large-v3-turbo-q5_0"),
        WhisperCppConfig(variant="vulkan"),
        "vulkan",
        ASROptions(word_timestamps=False),
    )

    assert "--no-flash-attn" in command
    assert "--dtw" not in command


def test_parse_progress_is_injectable() -> None:
    assert parse_progress("whisper_print_progress_callback: progress =  42%") == 42
    assert parse_progress("unrelated") is None


def test_gpu_initialization_failure_patterns_are_bounded() -> None:
    assert is_gpu_initialization_failure("failed to initialize Vulkan device")
    assert is_gpu_initialization_failure("in openvino encoder compile routine: exception")
    assert not is_gpu_initialization_failure("model file has invalid magic")


def test_missing_variant_error_includes_recovery_command() -> None:
    source = (Path(__file__).parents[1] / "src" / "utteran" / "asr" / "whisper_cpp.py").read_text(
        encoding="utf-8"
    )

    assert "`utteran native build --variant {requested}`" in source


def test_auto_gpu_initialization_failure_falls_back_once(tmp_path: Path) -> None:
    vulkan = tmp_path / "vulkan" / "whisper-cli.exe"
    openvino = tmp_path / "openvino" / "whisper-cli.exe"
    for executable in (vulkan, openvino):
        executable.parent.mkdir(parents=True)
        executable.touch()
    backend = WhisperCppBackend(allow_fallback=True)
    backend._variant = "openvino_vulkan"
    backend._backends = {
        "vulkan": {"executable": str(vulkan)},
        "openvino": {"executable": str(openvino)},
    }

    assert backend._fallback_variant("failed to initialize Vulkan device") == "vulkan"
    backend._variant = "vulkan"
    assert backend._fallback_variant("failed to initialize Vulkan device") is None


def test_explicit_whisper_cpp_variant_does_not_fallback(tmp_path: Path) -> None:
    fallback = tmp_path / "vulkan" / "whisper-cli.exe"
    fallback.parent.mkdir(parents=True)
    fallback.touch()
    backend = WhisperCppBackend(WhisperCppConfig(variant="openvino_vulkan"))
    backend._variant = "openvino_vulkan"
    backend._backends = {"vulkan": {"executable": str(fallback)}}

    assert backend._fallback_variant("failed to initialize Vulkan device") is None


def test_openvino_ir_status_parser_is_centralized() -> None:
    assert parse_openvino_ir_status("OpenVINO encoder initialized") is True
    assert parse_openvino_ir_status("loading OpenVINO model from 'model.xml'") is True
    assert parse_openvino_ir_status("in OpenVINO encoder compile routine: error") is False
    assert parse_openvino_ir_status("whisper.cpp unrelated output") is None


def test_error_summary_excludes_possible_recognized_text() -> None:
    detail = summarize_subprocess_error(
        "[00:00] recognized private transcript\nOpenVINO encoder init failed: device missing\n"
    )

    assert "private transcript" not in detail
    assert "OpenVINO encoder init failed" in detail


def test_model_and_openvino_ir_are_staged_together(tmp_path: Path) -> None:
    source_dir = tmp_path / "日本語"
    source_dir.mkdir()
    model = source_dir / "ggml-base-q5_1.bin"
    xml = source_dir / "ggml-base-q5_1-encoder-openvino.xml"
    binary = source_dir / "ggml-base-q5_1-encoder-openvino.bin"
    model.write_bytes(b"model")
    xml.write_text("xml", encoding="utf-8")
    binary.write_bytes(b"ir")

    staged = _stage_model(model, tmp_path / "ascii-stage")

    assert staged.read_bytes() == b"model"
    assert (staged.parent / xml.name).read_text(encoding="utf-8") == "xml"
    assert (staged.parent / binary.name).read_bytes() == b"ir"


def test_vad_model_is_staged_through_ascii_safe_temporary_path(tmp_path: Path) -> None:
    vad = tmp_path / "日本語" / "ggml-silero-v6.2.0.bin"
    vad.parent.mkdir()
    vad.write_bytes(b"vad")
    settings = WhisperCppConfig(variant="vulkan", vad=True, vad_model=vad)
    backend = WhisperCppBackend(settings)
    backend._entry = get_model("whisper-cpp:large-v3-turbo-q5_0")
    backend._model_path = tmp_path / "model.bin"
    backend._model_path.write_bytes(b"model")

    staged = _stage_model(vad, tmp_path / "ascii-vad")

    assert staged.read_bytes() == b"vad"
    assert all(ord(char) < 128 for char in str(staged.relative_to(tmp_path)))


def test_convert_result_keeps_offset_words_as_low_confidence_when_dtw_is_disabled() -> None:
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

    assert len(result.segments[0].words) == 1
    assert result.segments[0].speaker_confidence == "low"
    assert result.language == "ja"


def test_convert_result_discards_zero_length_segments_and_words() -> None:
    entry = get_model("whisper-cpp:base")
    data = {
        "result": {"language": "ja"},
        "transcription": [
            {
                "offsets": {"from": 500, "to": 500},
                "text": "ignored",
                "tokens": [],
            },
            {
                "offsets": {"from": 1000, "to": 2000},
                "text": "kept",
                "tokens": [
                    {
                        "text": " word",
                        "t_dtw": 100,
                        "offsets": {"from": 1000, "to": 1000},
                        "p": 0.9,
                    }
                ],
            },
        ],
    }

    result = _convert_result(data, entry, "cpu", True)

    assert len(result.segments) == 1
    assert (result.segments[0].start, result.segments[0].end) == (1.0, 2.0)
    assert result.segments[0].words == []


def test_convert_result_keeps_segment_but_discards_abnormally_long_word_timing() -> None:
    entry = get_model("whisper-cpp:base")
    data = {
        "result": {"language": "ja"},
        "transcription": [
            {
                "offsets": {"from": 899_820, "to": 965_960},
                "text": "synthetic invalid timing",
                "tokens": [
                    {
                        "text": " token",
                        "t_dtw": 65_582,
                        "offsets": {"from": 899_820, "to": 965_960},
                        "p": 0.9,
                    }
                ],
            }
        ],
    }

    result = _convert_result(data, entry, "cpu", True, max_word_duration_seconds=3.0)

    assert len(result.segments) == 1
    assert (result.segments[0].start, result.segments[0].end) == (899.82, 965.96)
    assert result.segments[0].text == "synthetic invalid timing"
    assert result.segments[0].words == []


def test_convert_result_keeps_segment_but_discards_word_timing_collapsed_to_one_edge() -> None:
    entry = get_model("whisper-cpp:base")
    data = {
        "result": {"language": "ja"},
        "transcription": [
            {
                "offsets": {"from": 989_080, "to": 1_006_470},
                "text": "synthetic collapsed timing",
                "tokens": [
                    {
                        "text": " ok",
                        "t_dtw": 100_450,
                        "offsets": {"from": 1_004_490, "to": 1_004_570},
                        "p": 0.9,
                    }
                ],
            }
        ],
    }

    result = _convert_result(data, entry, "cpu", True, max_word_duration_seconds=3.0)

    assert len(result.segments) == 1
    assert (result.segments[0].start, result.segments[0].end) == (1004.49, 1004.57)
    assert result.segments[0].text == "synthetic collapsed timing"
    assert len(result.segments[0].words) == 1


def test_convert_result_discards_only_long_words_and_marks_partial_timing_low() -> None:
    entry = get_model("whisper-cpp:base")
    data = {
        "result": {"language": "ja"},
        "transcription": [
            {
                "offsets": {"from": 0, "to": 10_000},
                "text": " bad good",
                "tokens": [
                    {"text": " bad", "t_dtw": 1, "offsets": {"from": 0, "to": 9000}, "p": 0.5},
                    {
                        "text": " good",
                        "t_dtw": 900,
                        "offsets": {"from": 9000, "to": 10_000},
                        "p": 0.9,
                    },
                ],
            }
        ],
    }

    result = _convert_result(data, entry, "cpu", True, max_word_duration_seconds=3.0)

    assert [word.text for word in result.segments[0].words] == ["good"]
    assert (result.segments[0].start, result.segments[0].end) == (9.0, 10.0)
    assert result.segments[0].text == " bad good"
    assert result.segments[0].speaker_confidence == "low"


def test_convert_result_limits_identical_consecutive_segments_to_ten() -> None:
    entry = get_model("whisper-cpp:base")
    transcription = [
        {
            "offsets": {"from": index * 1000, "to": (index + 1) * 1000},
            "text": " repeated ",
            "tokens": [],
        }
        for index in range(12)
    ]

    result = _convert_result(
        {"result": {"language": "ja"}, "transcription": transcription},
        entry,
        "cpu",
        False,
    )

    assert len(result.segments) == 10
    assert [segment.start for segment in result.segments] == [float(index) for index in range(10)]


def test_convert_result_repetition_guard_can_be_disabled() -> None:
    entry = get_model("whisper-cpp:base")
    transcription = [
        {"offsets": {"from": i * 1000, "to": (i + 1) * 1000}, "text": " yes", "tokens": []}
        for i in range(7)
    ]
    result = _convert_result(
        {"result": {"language": "ja"}, "transcription": transcription},
        entry,
        "cpu",
        False,
        repetition_limit=0,
    )
    assert len(result.segments) == 7
