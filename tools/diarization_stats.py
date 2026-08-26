"""Report content-free diarization/alignment statistics from one utteran job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from utteran.align import align_transcription_with_statistics, speaker_turn_statistics
from utteran.types import AlignmentOptions, DiarizationResult, TranscriptionResult


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
    _selected_segments, selected_alignment = align_transcription_with_statistics(
        transcription, diarization, options
    )
    return {
        "audio_seconds": round(transcription.duration, 6),
        "regular_diarization": speaker_turn_statistics(diarization.turns),
        "exclusive_diarization": (
            None
            if diarization.exclusive_turns is None
            else speaker_turn_statistics(diarization.exclusive_turns)
        ),
        "regular_alignment": regular_alignment,
        "selected_alignment": selected_alignment,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(collect_statistics(args.job_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
