from __future__ import annotations

import wave
from pathlib import Path

import pytest

from utteran.benchmark import (
    SHORT_BENCHMARK_WARNING,
    BenchmarkSample,
    aggregate,
    apply_variant,
    benchmark_warning,
    parse_durations,
    prepared_audio_lengths,
    wav_duration,
)


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
