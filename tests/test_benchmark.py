from __future__ import annotations

import wave
from pathlib import Path

import pytest

from utteran.benchmark import (
    SCORE_DISCLAIMER,
    SHORT_BENCHMARK_WARNING,
    BenchmarkMeasurement,
    BenchmarkResult,
    BenchmarkSample,
    accuracy_score,
    aggregate,
    apply_target,
    apply_variant,
    benchmark_warning,
    character_error_rate,
    estimated_minutes_for_hour,
    load_run,
    markdown_report,
    new_run_payload,
    parse_durations,
    prepared_audio_lengths,
    resolve_target,
    save_run,
    speed_score,
    wav_duration,
)
from utteran.config import Config


def test_aggregate_uses_medians_and_peak() -> None:
    result = aggregate(
        "vulkan",
        [BenchmarkSample(1, 9, 100), BenchmarkSample(2, 18, 300), BenchmarkSample(1, 14, 200)],
        100,
    )
    assert result.median_total_seconds == 15
    assert result.median_load_seconds == 1
    assert result.realtime_factor == 100 / 15
    assert result.peak_ram_bytes == 300
    assert result.speed_score == 667
    assert result.hour_minutes == 9


def test_scores_use_rtf_and_japanese_cer() -> None:
    assert speed_score(7.8) == 780
    assert estimated_minutes_for_hour(7.8) == 8
    cer = character_error_rate("今日は、会議です。", "今日は会議でした")
    assert cer == 2 / 7
    assert accuracy_score(cer) == 71


def test_target_registry_rejects_invalid_combinations() -> None:
    target = resolve_target("whisper-cpp", "vulkan", "large-v3-turbo-q5_0")
    assert target.backend == "whisper-cpp"
    assert target.device == "vulkan"
    assert target.quantization == "q5_0"
    assert target.baseline is True
    with pytest.raises(ValueError, match="無効な組み合わせ"):
        resolve_target("faster-whisper", "vulkan", "large-v3-turbo")


def test_apply_variant_preserves_unrelated_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[general]\nlog_level = "debug"\n\n[asr.whisper_cpp]\nvariant = "cpu"\nthreads = 2\n',
        encoding="utf-8",
    )
    apply_variant(path, "vulkan", 900.0)
    text = path.read_text(encoding="utf-8")
    assert 'variant = "vulkan"' in text
    assert "benchmark_duration_seconds = 900.000" in text
    assert 'log_level = "debug"' in text
    assert "threads = 2" in text


def test_apply_target_records_provenance_and_remains_loadable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    target = resolve_target("faster-whisper", "cpu", "large-v3-turbo")
    apply_target(path, target, 180.0, "2026-08-31T00:00:00+00:00")
    text = path.read_text(encoding="utf-8")
    assert 'backend = "faster-whisper"' in text
    assert 'target = "faster-whisper/cpu/large-v3-turbo"' in text
    assert Config.load(config_path=path).asr.device == "cpu"


def test_parse_durations_supports_multiple_lengths_and_full() -> None:
    assert parse_durations("180,900,full,180", 1200.0) == (180.0, 900.0, 1200.0)
    with pytest.raises(ValueError, match="超えています"):
        parse_durations("1201", 1200.0)


def test_short_benchmark_warning_records_phase3d_reversal() -> None:
    assert benchmark_warning(180.0) == SHORT_BENCHMARK_WARNING
    assert "順位逆転" in SHORT_BENCHMARK_WARNING
    assert benchmark_warning(900.0) is None


def test_prepared_audio_lengths_creates_prefix_and_reuses_full_audio(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(100)
        audio.writeframes(b"\0\0" * 1000)

    with prepared_audio_lengths(source, (3.0, 10.0)) as prepared:
        assert len(prepared) == 2
        assert prepared[0][0] == 3.0
        assert wav_duration(prepared[0][1]) == 3.0
        assert prepared[1] == (10.0, source)
        temporary = prepared[0][1]
        assert temporary.is_file()
    assert not temporary.exists()


def test_result_save_load_and_markdown_are_transcript_free(tmp_path: Path) -> None:
    target = resolve_target("faster-whisper", "cpu", "large-v3-turbo")
    result = BenchmarkResult(
        "faster-whisper",
        (BenchmarkSample(1.0, 9.0),),
        10.0,
        1.0,
        6.0,
        None,
        target,
        600,
        94,
        0.06,
        False,
    )
    measurement = BenchmarkMeasurement(60.0, benchmark_warning(60.0), (result,))
    payload = new_run_payload("standard", 60.0, {"utteran_version": "test"}, ())
    payload["status"] = "interrupted"
    payload["measurements"] = [measurement.as_dict()]
    path = tmp_path / "benchmark.json"
    save_run(path, payload)
    restored = load_run(path)
    assert restored["status"] == "interrupted"
    assert restored["measurements"][0]["results"][0]["speed_score"] == 600
    markdown = markdown_report(restored)
    assert "約10分" in markdown
    assert SCORE_DISCLAIMER in markdown
    assert "transcript" not in path.read_text(encoding="utf-8")
