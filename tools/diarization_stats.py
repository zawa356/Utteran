"""Report content-free diarization/alignment statistics from one utteran job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from utteran.align import align_transcription_with_statistics, speaker_turn_statistics
from utteran.types import AlignmentOptions, DiarizationResult, Segment, TranscriptionResult


def _interval_statistics(
    segments: list[Segment], audio_seconds: float, *, long_gap_seconds: float = 10.0
) -> dict[str, Any]:
    intervals: list[list[float]] = []
    for segment in sorted(segments, key=lambda item: (item.start, item.end)):
        if not intervals or segment.start > intervals[-1][1]:
            intervals.append([segment.start, segment.end])
        else:
            intervals[-1][1] = max(intervals[-1][1], segment.end)
    gaps: list[float] = []
    cursor = 0.0
    for start, end in intervals:
        if start > cursor:
            gaps.append(start - cursor)
        cursor = max(cursor, end)
    if audio_seconds > cursor:
        gaps.append(audio_seconds - cursor)
    long_gaps = [gap for gap in gaps if gap >= long_gap_seconds]
    unknown = [segment for segment in segments if segment.speaker == "UNKNOWN"]
    return {
        "segment_count": len(segments),
        "covered_seconds": round(sum(end - start for start, end in intervals), 6),
        "coverage_ratio": round(
            sum(end - start for start, end in intervals) / max(audio_seconds, 1e-9), 6
        ),
        "long_gap_count": len(long_gaps),
        "long_gap_seconds": round(sum(long_gaps), 6),
        "shorter_than_0_5_seconds_count": sum(
            segment.end - segment.start < 0.5 for segment in segments
        ),
        "unknown_segment_count": len(unknown),
        "unknown_seconds": round(
            sum(max(0.0, segment.end - segment.start) for segment in unknown), 6
        ),
    }


def _read_result(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise ValueError(f"invalid utteran intermediate: {path.name}")
    return result


def collect_statistics(job_dir: Path) -> dict[str, Any]:
    """Calculate stage-by-stage counts without returning recognized text."""
    transcription = TranscriptionResult.from_dict(_read_result(job_dir / "asr.json"))
    diarization = DiarizationResult.from_dict(_read_result(job_dir / "diarization.json"))
    options = AlignmentOptions()
    regular_result = DiarizationResult(
        turns=diarization.turns,
        exclusive_turns=None,
        num_speakers=diarization.num_speakers,
        backend=diarization.backend,
        model_id=diarization.model_id,
        device=diarization.device,
        memory=diarization.memory,
    )
    _regular_segments, regular_alignment = align_transcription_with_statistics(
        transcription, regular_result, options
    )
    selected_segments, selected_alignment = align_transcription_with_statistics(
        transcription, diarization, options
    )
    return {
        "audio_seconds": round(transcription.duration, 6),
        "asr_intervals": _interval_statistics(transcription.segments, transcription.duration),
        "regular_diarization": speaker_turn_statistics(diarization.turns),
        "exclusive_diarization": (
            None
            if diarization.exclusive_turns is None
            else speaker_turn_statistics(diarization.exclusive_turns)
        ),
        "regular_alignment": regular_alignment,
        "selected_alignment": selected_alignment,
        "merged_intervals": _interval_statistics(selected_segments, transcription.duration),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(collect_statistics(args.job_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
