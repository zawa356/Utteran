"""Word-level reconciliation of ASR timestamps and speaker turns."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from itertools import pairwise
from typing import Any

from utteran.types import (
    AlignmentOptions,
    DiarizationResult,
    Segment,
    SpeakerTurn,
    TranscriptionResult,
    Word,
)

UNKNOWN_SPEAKER = "UNKNOWN"


def align_transcription(
    transcription: TranscriptionResult,
    diarization: DiarizationResult,
    options: AlignmentOptions | None = None,
) -> list[Segment]:
    """Assign speakers, split changes, absorb tiny islands, merge, and renumber."""
    segments, _statistics = align_transcription_with_statistics(transcription, diarization, options)
    return segments


def align_transcription_with_statistics(
    transcription: TranscriptionResult,
    diarization: DiarizationResult,
    options: AlignmentOptions | None = None,
) -> tuple[list[Segment], dict[str, Any]]:
    """Align speakers and return content-free diagnostics for every reduction stage."""
    selected = options or AlignmentOptions()
    source_turns = (
        diarization.exclusive_turns
        if diarization.exclusive_turns is not None
        else diarization.turns
    )
    turns = sorted(source_turns, key=lambda turn: (turn.start, turn.end, turn.speaker))

    split_segments: list[Segment] = []
    for segment in transcription.segments:
        split_segments.extend(
            _split_segment_by_speaker(segment, turns, selected.max_nearest_distance)
        )

    absorbed = _absorb_short_speaker_islands(
        split_segments,
        selected.min_segment_duration,
        selected.min_segment_words,
    )
    merged_gaps: list[float] = []
    merged = _merge_adjacent_same_speaker(absorbed, selected.merge_gap, merged_gaps)
    statistics = {
        "source_turns": "exclusive" if diarization.exclusive_turns is not None else "regular",
        "asr_segment_count": len(transcription.segments),
        "asr_segments_with_words": sum(bool(segment.words) for segment in transcription.segments),
        "word_count": sum(len(segment.words) for segment in transcription.segments),
        "word_speaker_change_count": _word_speaker_change_count(split_segments),
        "split_segment_count": len(split_segments),
        "absorbed_segment_count": len(absorbed),
        "absorption_count": (len(split_segments) - len(absorbed)) // 2,
        "merge_input_segment_count": len(absorbed),
        "merged_segment_count": len(merged),
        "merge_count": len(absorbed) - len(merged),
        "merge_gap_seconds": _distribution(merged_gaps),
        "longest_merged_segment_seconds": round(
            max((segment.end - segment.start for segment in merged), default=0.0), 6
        ),
        "speaker_totals_seconds": _segment_speaker_totals(merged),
    }
    if selected.renumber_speakers:
        _renumber_in_appearance_order(merged)
    return merged, statistics


def speaker_turn_statistics(turns: Iterable[SpeakerTurn]) -> dict[str, Any]:
    """Summarize diarization turns without exposing recognized text."""
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    totals: defaultdict[str, float] = defaultdict(float)
    for turn in ordered:
        totals[turn.speaker] += max(0.0, turn.end - turn.start)
    return {
        "turn_count": len(ordered),
        "speaker_count": len(totals),
        "speaker_change_count": sum(
            left.speaker != right.speaker for left, right in pairwise(ordered)
        ),
        "longest_turn_seconds": round(
            max((turn.end - turn.start for turn in ordered), default=0.0), 6
        ),
        "speaker_totals_seconds": {
            speaker: round(duration, 6) for speaker, duration in sorted(totals.items())
        },
    }


def assign_word_speaker(
    word: Word,
    turns: Iterable[SpeakerTurn],
    max_nearest_distance: float = 2.0,
) -> str:
    """Assign one word according to design section 7.1 steps 2 and 3."""
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    if not ordered:
        return UNKNOWN_SPEAKER

    center = (word.start + word.end) / 2.0
    containing = [turn for turn in ordered if turn.start <= center < turn.end]
    if containing:
        return containing[0].speaker

    overlaps = [(_overlap(word.start, word.end, turn.start, turn.end), turn) for turn in ordered]
    greatest_overlap = max(overlap for overlap, _ in overlaps)
    if greatest_overlap > 0.0:
        return next(turn.speaker for overlap, turn in overlaps if overlap == greatest_overlap)

    nearest = min(ordered, key=lambda turn: (_distance(word, turn), turn.start, turn.speaker))
    if _distance(word, nearest) <= max_nearest_distance:
        return nearest.speaker
    return UNKNOWN_SPEAKER


def _split_segment_by_speaker(
    segment: Segment,
    turns: list[SpeakerTurn],
    max_nearest_distance: float,
) -> list[Segment]:
    """Split an ASR segment at each word-level speaker transition."""
    if not segment.words:
        representative = Word(segment.start, segment.end, segment.text)
        return [
            Segment(
                start=segment.start,
                end=segment.end,
                text=segment.text,
                words=[],
                speaker=assign_word_speaker(representative, turns, max_nearest_distance),
            )
        ]

    groups: list[tuple[str, list[Word]]] = []
    for word in segment.words:
        speaker = assign_word_speaker(word, turns, max_nearest_distance)
        if groups and groups[-1][0] == speaker:
            groups[-1][1].append(word)
        else:
            groups.append((speaker, [word]))

    return [
        Segment(
            start=min(word.start for word in words),
            # DTW may give an earlier token a later end than a following token.
            # Keep every word contained even when token ends are not monotonic.
            end=max(word.end for word in words),
            text="".join(word.text for word in words),
            words=list(words),
            speaker=speaker,
        )
        for speaker, words in groups
    ]


def _absorb_short_speaker_islands(
    segments: list[Segment],
    min_duration: float,
    min_words: int,
) -> list[Segment]:
    """Absorb a tiny middle island when both surrounding speakers agree."""
    result = [_copy_segment(segment) for segment in segments]
    index = 1
    while index < len(result) - 1:
        current = result[index]
        # Missing word metadata means the backend could not provide word timestamps;
        # it does not mean that the segment contains zero spoken words.
        is_short = current.end - current.start < min_duration or (
            bool(current.words) and len(current.words) < min_words
        )
        surrounding_speaker = result[index - 1].speaker
        if (
            is_short
            and surrounding_speaker == result[index + 1].speaker
            and surrounding_speaker not in {None, UNKNOWN_SPEAKER}
        ):
            combined = _combine_segments(result[index - 1 : index + 2], surrounding_speaker)
            result[index - 1 : index + 2] = [combined]
            index = max(1, index - 1)
        else:
            index += 1
    return result


def _merge_adjacent_same_speaker(
    segments: list[Segment], max_gap: float, merged_gaps: list[float] | None = None
) -> list[Segment]:
    """Merge consecutive same-speaker segments separated by at most max_gap."""
    merged: list[Segment] = []
    for segment in segments:
        if (
            merged
            and merged[-1].speaker == segment.speaker
            and segment.start - merged[-1].end <= max_gap
        ):
            if merged_gaps is not None:
                merged_gaps.append(max(0.0, segment.start - merged[-1].end))
            merged[-1] = _combine_segments([merged[-1], segment], segment.speaker)
        else:
            merged.append(_copy_segment(segment))
    return merged


def _word_speaker_change_count(segments: Iterable[Segment]) -> int:
    speakers = [segment.speaker for segment in segments if segment.words]
    return sum(left != right for left, right in pairwise(speakers))


def _segment_speaker_totals(segments: Iterable[Segment]) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for segment in segments:
        totals[segment.speaker or UNKNOWN_SPEAKER] += max(0.0, segment.end - segment.start)
    return {speaker: round(duration, 6) for speaker, duration in sorted(totals.items())}


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}

    def percentile(ratio: float) -> float:
        index = round((len(ordered) - 1) * ratio)
        return round(ordered[index], 6)

    return {
        "count": len(ordered),
        "min": round(ordered[0], 6),
        "median": percentile(0.5),
        "p95": percentile(0.95),
        "max": round(ordered[-1], 6),
    }


def _renumber_in_appearance_order(segments: list[Segment]) -> None:
    """Rewrite backend labels as SPEAKER_00... in first-appearance order."""
    labels: dict[str, str] = {}
    for segment in segments:
        if segment.speaker in {None, UNKNOWN_SPEAKER}:
            continue
        assert segment.speaker is not None
        if segment.speaker not in labels:
            labels[segment.speaker] = f"SPEAKER_{len(labels):02d}"
        segment.speaker = labels[segment.speaker]


def _combine_segments(segments: list[Segment], speaker: str | None) -> Segment:
    """Combine ordered segments without discarding text or word metadata."""
    return Segment(
        start=min(segment.start for segment in segments),
        end=max(segment.end for segment in segments),
        text="".join(segment.text for segment in segments),
        words=[word for segment in segments for word in segment.words],
        speaker=speaker,
    )


def _copy_segment(segment: Segment) -> Segment:
    """Copy mutable segment containers while reusing immutable words."""
    return Segment(
        start=segment.start,
        end=segment.end,
        text=segment.text,
        words=list(segment.words),
        speaker=segment.speaker,
    )


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    """Return intersection duration for two time intervals."""
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _distance(word: Word, turn: SpeakerTurn) -> float:
    """Return the gap between intervals, zero when touching or overlapping."""
    if word.end < turn.start:
        return turn.start - word.end
    if turn.end < word.start:
        return word.start - turn.end
    return 0.0
