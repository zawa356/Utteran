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
_EMISSION_SCALE_SECONDS = 0.3
_CLEAR_OVERLAP_MARGIN = 0.15
_SILENCE_PENALTY_SCALE = 0.15
_CLEAR_BOUNDARY_PENALTY_SCALE = 0.2
_UNKNOWN_TRANSITION_SCALE = 0.5


def align_transcription(
    transcription: TranscriptionResult,
    diarization: DiarizationResult,
    options: AlignmentOptions | None = None,
) -> list[Segment]:
    """Assign a globally optimal speaker sequence, split, merge, and renumber."""
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

    all_words = [word for segment in transcription.segments for word in segment.words]
    assigned_speakers = _assign_word_speaker_sequence(all_words, turns, selected)
    assignment_index = 0
    split_segments: list[Segment] = []
    for segment in transcription.segments:
        if segment.words:
            segment_speakers = assigned_speakers[
                assignment_index : assignment_index + len(segment.words)
            ]
            assignment_index += len(segment.words)
            split_segments.extend(_split_segment_by_speaker(segment, segment_speakers))
        else:
            representative = Word(segment.start, segment.end, segment.text)
            split_segments.append(
                Segment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    words=[],
                    speaker=assign_word_speaker(representative, turns),
                )
            )

    merged_gaps: list[float] = []
    merged = _merge_adjacent_same_speaker(split_segments, selected.merge_gap, merged_gaps)
    statistics = {
        "assignment_method": "viterbi",
        "source_turns": "exclusive" if diarization.exclusive_turns is not None else "regular",
        "asr_segment_count": len(transcription.segments),
        "asr_segments_with_words": sum(bool(segment.words) for segment in transcription.segments),
        "word_count": sum(len(segment.words) for segment in transcription.segments),
        "word_speaker_change_count": _word_speaker_change_count(split_segments),
        "split_segment_count": len(split_segments),
        # Compatibility fields: duration/word-count island absorption was removed because
        # it deleted legitimate short acknowledgements after sequence optimization.
        "absorbed_segment_count": len(split_segments),
        "absorption_count": 0,
        "merge_input_segment_count": len(split_segments),
        "merged_segment_count": len(merged),
        "merge_count": len(split_segments) - len(merged),
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
    """Assign one isolated word by overlap, without a nearest-speaker fallback."""
    del max_nearest_distance  # compatibility argument; nearest assignment is intentionally off
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

    return UNKNOWN_SPEAKER


def _assign_word_speaker_sequence(
    words: list[Word], turns: list[SpeakerTurn], options: AlignmentOptions
) -> list[str]:
    """Use Viterbi dynamic programming to select one speaker for every word."""
    if not words:
        return []
    known_speakers = sorted({turn.speaker for turn in turns})
    if not known_speakers:
        return [UNKNOWN_SPEAKER] * len(words)
    states = [*known_speakers, UNKNOWN_SPEAKER]
    emissions = [_emission_scores(word, turns, states, options) for word in words]
    scores = dict(emissions[0])
    backpointers: list[dict[str, str]] = []
    for index in range(1, len(words)):
        next_scores: dict[str, float] = {}
        previous_for_state: dict[str, str] = {}
        for state in states:
            candidates = [
                (
                    scores[previous]
                    - _transition_penalty(
                        words[index - 1],
                        words[index],
                        previous,
                        state,
                        turns,
                        emissions[index - 1],
                        emissions[index],
                        options,
                    )
                    + emissions[index][state],
                    previous,
                )
                for previous in states
            ]
            best_score, best_previous = max(candidates, key=lambda item: (item[0], item[1]))
            next_scores[state] = best_score
            previous_for_state[state] = best_previous
        scores = next_scores
        backpointers.append(previous_for_state)

    selected = max(states, key=lambda state: (scores[state], state))
    sequence = [selected]
    for pointers in reversed(backpointers):
        selected = pointers[selected]
        sequence.append(selected)
    sequence.reverse()
    return sequence


def _emission_scores(
    word: Word,
    turns: list[SpeakerTurn],
    states: list[str],
    options: AlignmentOptions,
) -> dict[str, float]:
    overlaps = {
        speaker: min(
            max(0.0, word.end - word.start),
            sum(
                _overlap(word.start, word.end, turn.start, turn.end)
                for turn in turns
                if turn.speaker == speaker
            ),
        )
        for speaker in states
        if speaker != UNKNOWN_SPEAKER
    }
    maximum_overlap = max(overlaps.values(), default=0.0)
    scores = {
        speaker: min(1.0, overlap / _EMISSION_SCALE_SECONDS)
        for speaker, overlap in overlaps.items()
    }
    if maximum_overlap == 0.0:
        bridged = _same_speaker_bridge(word, turns, options.max_same_speaker_bridge_gap)
        if bridged is not None:
            scores[bridged] = options.unknown_emission_score + 0.05
        scores[UNKNOWN_SPEAKER] = options.unknown_emission_score
    else:
        scores[UNKNOWN_SPEAKER] = -options.unknown_emission_score
    return scores


def _same_speaker_bridge(word: Word, turns: list[SpeakerTurn], max_gap: float) -> str | None:
    previous = [turn for turn in turns if turn.end <= word.start]
    following = [turn for turn in turns if turn.start >= word.end]
    if not previous or not following:
        return None
    left = max(previous, key=lambda turn: (turn.end, turn.start, turn.speaker))
    right = min(following, key=lambda turn: (turn.start, turn.end, turn.speaker))
    if (
        left.speaker == right.speaker
        and word.start - left.end <= max_gap
        and right.start - word.end <= max_gap
    ):
        return left.speaker
    return None


def _transition_penalty(
    previous_word: Word,
    word: Word,
    previous_speaker: str,
    speaker: str,
    turns: list[SpeakerTurn],
    previous_emissions: dict[str, float],
    emissions: dict[str, float],
    options: AlignmentOptions,
) -> float:
    if previous_speaker == speaker:
        return 0.0
    penalty = options.speaker_switch_penalty
    gap = max(0.0, word.start - previous_word.end)
    if gap >= options.silence_switch_threshold:
        penalty *= _SILENCE_PENALTY_SCALE
    elif _is_clear_supported_switch(
        previous_word,
        word,
        previous_speaker,
        speaker,
        turns,
        previous_emissions,
        emissions,
        options.min_clear_turn_duration,
    ):
        penalty *= _CLEAR_BOUNDARY_PENALTY_SCALE
    if UNKNOWN_SPEAKER in {previous_speaker, speaker}:
        penalty *= _UNKNOWN_TRANSITION_SCALE
    return penalty


def _is_clear_supported_switch(
    previous_word: Word,
    word: Word,
    previous_speaker: str,
    speaker: str,
    turns: list[SpeakerTurn],
    previous_emissions: dict[str, float],
    emissions: dict[str, float],
    min_turn_duration: float,
) -> bool:
    if UNKNOWN_SPEAKER in {previous_speaker, speaker}:
        return False
    previous_margin = previous_emissions[previous_speaker] - previous_emissions[speaker]
    current_margin = emissions[speaker] - emissions[previous_speaker]
    return (
        previous_margin >= _CLEAR_OVERLAP_MARGIN
        and current_margin >= _CLEAR_OVERLAP_MARGIN
        and _supporting_turn_duration(previous_word, previous_speaker, turns) >= min_turn_duration
        and _supporting_turn_duration(word, speaker, turns) >= min_turn_duration
    )


def _supporting_turn_duration(word: Word, speaker: str, turns: list[SpeakerTurn]) -> float:
    return max(
        (
            turn.end - turn.start
            for turn in turns
            if turn.speaker == speaker
            and _overlap(word.start, word.end, turn.start, turn.end) > 0.0
        ),
        default=0.0,
    )


def _split_segment_by_speaker(
    segment: Segment,
    speakers: list[str],
) -> list[Segment]:
    """Split an ASR segment at each word-level speaker transition."""
    if len(speakers) != len(segment.words):
        raise ValueError("word speaker sequence length does not match segment words")

    groups: list[tuple[str, list[Word]]] = []
    for word, speaker in zip(segment.words, speakers, strict=True):
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
