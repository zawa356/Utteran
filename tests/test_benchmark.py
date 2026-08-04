from __future__ import annotations

from pathlib import Path

from utteran.benchmark import BenchmarkSample, aggregate, apply_variant


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
    apply_variant(path, "vulkan")
    text = path.read_text(encoding="utf-8")
    assert 'variant = "vulkan"' in text
    assert 'log_level = "debug"' in text
    assert "threads = 2" in text
