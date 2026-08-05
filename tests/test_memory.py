from __future__ import annotations

import json
from pathlib import Path

import pytest

from utteran.memory import (
    CALIBRATION_MIN_POINTS,
    GIB,
    CalibrationPoint,
    CalibrationStore,
    MemoryReadings,
    PeakModel,
    PeakMonitor,
    assess_memory,
    calculate_budget,
    fit_calibration,
)


def test_budget_uses_cuda_free_vram_and_small_default_margin() -> None:
    budget = calculate_budget(
        "cuda:0",
        readings=MemoryReadings(system_available_bytes=100 * GIB, cuda_free_bytes=8 * GIB),
    )
    assert budget.raw_bytes == 8 * GIB
    assert budget.usable_bytes == pytest.approx(7.2 * GIB)
    assert budget.safety_margin == 0.10


def test_budget_uses_smaller_xpu_limit_and_available_ram() -> None:
    budget = calculate_budget(
        "xpu:0",
        readings=MemoryReadings(system_available_bytes=12 * GIB, xpu_limit_bytes=16 * GIB),
    )
    assert budget.raw_bytes == 12 * GIB
    assert budget.usable_bytes == pytest.approx(8.4 * GIB)
    assert budget.safety_margin == 0.30


def test_budget_uses_available_ram_for_cpu_and_custom_margin() -> None:
    budget = calculate_budget(
        "cpu", safety_margin=0.25, readings=MemoryReadings(system_available_bytes=10 * GIB)
    )
    assert budget.raw_bytes == 10 * GIB
    assert budget.usable_bytes == pytest.approx(7.5 * GIB)


def test_missing_reading_is_unknown_not_sufficient() -> None:
    budget = calculate_budget("cuda:0", readings=MemoryReadings())
    result = assess_memory(PeakModel(2.0, 0.1, "test", 1), 10, budget)
    assert budget.usable_bytes is None
    assert result.status == "unknown"


@pytest.mark.parametrize(
    ("usable_gib", "expected"),
    [(10.0, "safe"), (6.0, "danger"), (4.9, "impossible")],
)
def test_assessment_boundaries(usable_gib: float, expected: str) -> None:
    budget = calculate_budget(
        "cpu",
        safety_margin=0.01,
        readings=MemoryReadings(system_available_bytes=round(usable_gib / 0.99 * GIB)),
    )
    result = assess_memory(PeakModel(5.0, 0.05, "test", 3), 10, budget)
    assert result.status == expected


def _point(minutes: float, gib: float) -> CalibrationPoint:
    return CalibrationPoint("diarization", "pyannote", "cpu", minutes, round(gib * GIB), "now")


def test_calibration_requires_three_points_and_rejects_outlier() -> None:
    assert CALIBRATION_MIN_POINTS == 3
    assert fit_calibration([_point(10, 2.1), _point(20, 2.2)]) is None
    model = fit_calibration(
        [
            _point(10, 2.1),
            _point(20, 2.2),
            _point(30, 2.3),
            _point(40, 2.4),
            _point(25, 20.0),
        ]
    )
    assert model is not None
    assert model.source == "local-calibration"
    assert model.sample_count == 4
    assert model.base_gib == pytest.approx(2.0, abs=0.01)
    assert model.gib_per_minute == pytest.approx(0.01, abs=0.001)


def test_store_contains_no_media_name_or_path(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    store = CalibrationStore(path)
    store.record("diarization", "pyannote", "cpu", 12.5, 3 * GIB, measured_at="now")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert set(payload["points"][0]) == {
        "stage",
        "backend",
        "device_kind",
        "audio_minutes",
        "peak_bytes",
        "measured_at",
    }
    assert store.reset()
    assert not path.exists()


def test_store_prefers_local_fit_after_enough_points(tmp_path: Path) -> None:
    store = CalibrationStore(tmp_path / "memory.json")
    assert store.model("diarization", "pyannote", "cpu").source == "phase3d-r5"  # type: ignore[union-attr]
    for minutes in (10.0, 20.0, 30.0):
        store.record("diarization", "pyannote", "cpu", minutes, round((1 + minutes * 0.02) * GIB))
    model = store.model("diarization", "pyannote", "cpu")
    assert model is not None
    assert model.source == "local-calibration"


def test_peak_monitor_keeps_maximum_sample() -> None:
    values = iter([10, 30, 20])
    monitor = PeakMonitor(lambda _pid: next(values, 20))
    monitor._sample()
    monitor._sample()
    monitor._sample()
    assert monitor.peak_bytes == 30
