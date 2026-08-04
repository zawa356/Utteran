from __future__ import annotations

from pathlib import Path

from utteran.asr.whisper_cpp import (
    WhisperCppBackend,
    _convert_result,
    _stage_model,
    build_command,
    is_gpu_initialization_failure,
    parse_progress,
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
    assert backend._fallback_variant("failed to initialize Vulkan device") == "openvino"


def test_explicit_whisper_cpp_variant_does_not_fallback(tmp_path: Path) -> None:
    fallback = tmp_path / "vulkan" / "whisper-cli.exe"
    fallback.parent.mkdir(parents=True)
    fallback.touch()
    backend = WhisperCppBackend(allow_fallback=False)
    backend._variant = "openvino_vulkan"
    backend._backends = {"vulkan": {"executable": str(fallback)}}

    assert backend._fallback_variant("failed to initialize Vulkan device") is None


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
