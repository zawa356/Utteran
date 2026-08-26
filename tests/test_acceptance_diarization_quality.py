from __future__ import annotations

import runpy
from pathlib import Path

_VALIDATE = runpy.run_path(Path(__file__).parents[1] / "tools" / "acceptance" / "validate.py")
MAX_LONGEST_OUTPUT_SEGMENT_RATIO = _VALIDATE["MAX_LONGEST_OUTPUT_SEGMENT_RATIO"]
MIN_OUTPUT_SEGMENTS_PER_MINUTE = _VALIDATE["MIN_OUTPUT_SEGMENTS_PER_MINUTE"]
diarization_granularity_statistics = _VALIDATE["diarization_granularity_statistics"]


def _payload(duration: float, segment_count: int, longest: float) -> dict[str, object]:
    step = duration / segment_count
    segments = [
        {
            "start": index * step,
            "end": (index + 1) * step,
            "speaker": f"SPEAKER_{index % 2:02d}",
        }
        for index in range(segment_count)
    ]
    segments[0]["end"] = longest
    return {"input": {"duration": duration}, "segments": segments}


def test_reported_long_recording_is_rejected_as_too_coarse() -> None:
    stats = diarization_granularity_statistics(_payload(6972.13, 18, 3671.75))

    assert stats["segments_per_minute"] < MIN_OUTPUT_SEGMENTS_PER_MINUTE
    assert stats["longest_segment_ratio"] > MAX_LONGEST_OUTPUT_SEGMENT_RATIO


def test_recomputed_long_recording_passes_granularity_thresholds() -> None:
    stats = diarization_granularity_statistics(_payload(6972.13, 388, 199.57))

    assert stats["segments_per_minute"] >= MIN_OUTPUT_SEGMENTS_PER_MINUTE
    assert stats["longest_segment_ratio"] <= MAX_LONGEST_OUTPUT_SEGMENT_RATIO
