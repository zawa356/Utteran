"""Word-level reconciliation of ASR timestamps and speaker turns."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from itertools import pairwise
from typing import Any, Literal, cast

from utteran.japanese_boundaries import japanese_morpheme_boundaries
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
_UNKNOWN_TRANSITION_SCALE = 1.0


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
    snap_statistics = _empty_snap_statistics(transcription.language, selected.boundary_snap_unit)
    fallback_original_seconds = 0.0
    fallback_retained_seconds = 0.0
    fallback_trimmed_seconds = 0.0
    fallback_trimmed_count = 0
    fallback_speech_envelope_count = 0
    fallback_character_cap_count = 0
    for segment in transcription.segments:
        if segment.words:
            segment_speakers = assigned_speakers[
                assignment_index : assignment_index + len(segment.words)
            ]
            assignment_index += len(segment.words)
            _snap_segment_speaker_boundaries(
                segment,
                segment_speakers,
                transcription.language,
                selected,
                snap_statistics,
            )
            split_segments.extend(_split_segment_by_speaker(segment, segment_speakers))
        else:
            representative = Word(segment.start, segment.end, segment.text)
            speaker = assign_word_speaker(representative, turns)
            retained_start, retained_end, timing_method = _fallback_segment_bounds(
                segment, turns, selected
            )
            original_duration = max(0.0, segment.end - segment.start)
            retained_duration = max(0.0, retained_end - retained_start)
            trimmed = max(0.0, original_duration - retained_duration)
            fallback_original_seconds += original_duration
            fallback_retained_seconds += retained_duration
            fallback_trimmed_seconds += trimmed
            fallback_trimmed_count += int(trimmed > 0.0)
            fallback_speech_envelope_count += int(timing_method == "speech_envelope")
            fallback_character_cap_count += int(timing_method == "character_cap")
            split_segments.append(
                Segment(
                    start=retained_start,
                    end=retained_end,
                    text=segment.text,
                    words=[],
                    speaker=speaker,
                    speaker_confidence="low",
                )
            )

    moved_time_values = cast(list[float], snap_statistics.pop("_moved_times", []))
    snap_statistics["moved_time_seconds"] = _distribution(moved_time_values)
    unknown_absorption_count = _absorb_short_unknown_segments(split_segments, selected)
    fragment_absorption_count = _absorb_unsupported_fragments(split_segments, turns, selected)
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
        "unknown_absorption_count": unknown_absorption_count,
        "unsupported_fragment_absorption_count": fragment_absorption_count,
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
        "boundary_snapping": snap_statistics,
        "fallback_timing": {
            "segment_count": sum(not segment.words for segment in transcription.segments),
            "trimmed_segment_count": fallback_trimmed_count,
            "original_seconds": round(fallback_original_seconds, 6),
            "retained_seconds": round(fallback_retained_seconds, 6),
            "trimmed_seconds": round(fallback_trimmed_seconds, 6),
            "characters_per_second": selected.fallback_characters_per_second,
            "padding_seconds": selected.fallback_duration_padding,
            "minimum_seconds": selected.fallback_min_duration,
            "speech_envelope_count": fallback_speech_envelope_count,
            "character_cap_count": fallback_character_cap_count,
            "detected_speech_coverage": _detected_speech_coverage(merged, turns),
        },
    }
    if selected.renumber_speakers:
        _renumber_in_appearance_order(merged)
    return merged, statistics


def _empty_snap_statistics(language: str, unit: str) -> dict[str, Any]:
    return {
        "language": language,
        "enabled": False,
        "analyzer_available": None,
        "unit": unit,
        "candidate_boundary_count": 0,
        "snapped_boundary_count": 0,
        "moved_character_count": 0,
        "moved_time_seconds": _distribution([]),
        "unit_comparison": {
            "A": {"eligible": 0, "distance_characters": 0},
            "B": {"eligible": 0, "distance_characters": 0},
        },
    }


def _snap_segment_speaker_boundaries(
    segment: Segment,
    speakers: list[str],
    language: str,
    options: AlignmentOptions,
    statistics: dict[str, Any],
) -> None:
    """Move Japanese speaker changes to nearby Sudachi character boundaries."""
    if not options.boundary_snap_enabled or not language.lower().startswith("ja"):
        return
    offsets = [0]
    for word in segment.words:
        offsets.append(offsets[-1] + len(word.text))
    text = "".join(word.text for word in segment.words)
    if not _contains_japanese_script(text):
        return
    statistics["enabled"] = True
    units: tuple[Literal["A", "B"], ...] = ("A", "B")
    boundaries_by_unit = {unit: japanese_morpheme_boundaries(text, unit) for unit in units}
    selected_boundaries = boundaries_by_unit[options.boundary_snap_unit]
    statistics["analyzer_available"] = selected_boundaries is not None
    if selected_boundaries is None:
        return
    index_by_offset = {offset: index for index, offset in enumerate(offsets)}
    moved_times = cast(list[float], statistics.setdefault("_moved_times", []))
    original_changes = [
        index for index in range(1, len(speakers)) if speakers[index - 1] != speakers[index]
    ]
    for index in original_changes:
        if speakers[index - 1] == speakers[index]:
            continue
        gap = max(0.0, segment.words[index].start - segment.words[index - 1].end)
        if gap > options.boundary_snap_max_gap:
            continue
        statistics["candidate_boundary_count"] += 1
        current_offset = offsets[index]
        candidates_by_unit: dict[str, int | None] = {}
        for unit in units:
            boundaries = boundaries_by_unit[unit]
            target = _nearest_boundary_offset(
                current_offset,
                boundaries,
                index_by_offset,
                options.boundary_snap_max_characters,
            )
            candidates_by_unit[unit] = target
            if target is not None and target != current_offset:
                comparison = cast(dict[str, dict[str, int]], statistics["unit_comparison"])
                comparison[unit]["eligible"] += 1
                comparison[unit]["distance_characters"] += abs(target - current_offset)
        target_offset = candidates_by_unit[options.boundary_snap_unit]
        if target_offset is None or target_offset == current_offset:
            continue
        target_index = index_by_offset[target_offset]
        left_speaker, right_speaker = speakers[index - 1], speakers[index]
        if target_index < index:
            speakers[target_index:index] = [right_speaker] * (index - target_index)
        else:
            speakers[index:target_index] = [left_speaker] * (target_index - index)
        old_time = _word_boundary_time(segment.words, index)
        new_time = _word_boundary_time(segment.words, target_index)
        moved_times.append(abs(new_time - old_time))
        statistics["snapped_boundary_count"] += 1
        statistics["moved_character_count"] += abs(target_offset - current_offset)


def _nearest_boundary_offset(
    current: int,
    boundaries: set[int] | None,
    index_by_offset: dict[int, int],
    max_distance: int,
) -> int | None:
    if boundaries is None:
        return None
    candidates = [
        boundary
        for boundary in boundaries
        if boundary in index_by_offset and abs(boundary - current) <= max_distance
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda boundary: (abs(boundary - current), boundary))


def _word_boundary_time(words: list[Word], index: int) -> float:
    if index <= 0:
        return words[0].start
    if index >= len(words):
        return words[-1].end
    return (words[index - 1].end + words[index].start) / 2.0


def _fallback_segment_bounds(
    segment: Segment, turns: list[SpeakerTurn], options: AlignmentOptions
) -> tuple[float, float, str]:
    overlaps = [
        turn for turn in turns if _overlap(segment.start, segment.end, turn.start, turn.end) > 0.0
    ]
    if overlaps:
        return (
            max(segment.start, min(turn.start for turn in overlaps)),
            min(segment.end, max(turn.end for turn in overlaps)),
            "speech_envelope",
        )
    character_count = len(
        "".join(character for character in segment.text if not character.isspace())
    )
    estimated = character_count / options.fallback_characters_per_second
    allowed = max(options.fallback_min_duration, estimated + options.fallback_duration_padding)
    return segment.start, min(segment.end, segment.start + allowed), "character_cap"


def _detected_speech_coverage(segments: list[Segment], turns: list[SpeakerTurn]) -> float:
    speech_intervals = _union_intervals((turn.start, turn.end) for turn in turns)
    segment_intervals = _union_intervals((segment.start, segment.end) for segment in segments)
    speech_seconds = sum(end - start for start, end in speech_intervals)
    if speech_seconds == 0.0:
        return 1.0
    covered = sum(
        _overlap(segment_start, segment_end, speech_start, speech_end)
        for segment_start, segment_end in segment_intervals
        for speech_start, speech_end in speech_intervals
    )
    return round(covered / speech_seconds, 6)


def _union_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _contains_japanese_script(text: str) -> bool:
    return any(
        0x3040 <= ord(character) <= 0x30FF or 0x3400 <= ord(character) <= 0x9FFF
        for character in text
    )


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
            # A segment without words is a deliberate timing fallback.  Its ASR offsets
            # are the only trustworthy boundary, so do not erase that boundary by merging.
            and merged[-1].words
            and segment.words
            and segment.start - merged[-1].end <= max_gap
        ):
            if merged_gaps is not None:
                merged_gaps.append(max(0.0, segment.start - merged[-1].end))
            merged[-1] = _combine_segments([merged[-1], segment], segment.speaker)
        else:
            merged.append(_copy_segment(segment))
    return merged


def _absorb_short_unknown_segments(segments: list[Segment], options: AlignmentOptions) -> int:
    """Relabel acoustically unusable UNKNOWN islands to the longer known neighbour."""
    absorbed = 0
    for index, segment in enumerate(segments):
        if segment.speaker != UNKNOWN_SPEAKER:
            continue
        duration = max(0.0, segment.end - segment.start)
        if (
            duration >= options.min_unknown_duration
            and len(segment.text.strip()) >= options.min_unknown_characters
        ):
            continue
        neighbours = [
            candidate
            for candidate in (
                segments[index - 1] if index > 0 else None,
                segments[index + 1] if index + 1 < len(segments) else None,
            )
            if candidate is not None and candidate.speaker not in {None, UNKNOWN_SPEAKER}
        ]
        if not neighbours:
            continue
        selected = max(
            neighbours,
            key=lambda candidate: (
                candidate.end - candidate.start,
                candidate is segments[index - 1],
            ),
        )
        segment.speaker = selected.speaker
        absorbed += 1
    return absorbed


def _absorb_unsupported_fragments(
    segments: list[Segment], turns: list[SpeakerTurn], options: AlignmentOptions
) -> int:
    """Absorb tiny boundary fragments only when their assigned speaker has no support."""
    absorbed = 0
    index = 0
    while index < len(segments):
        segment = segments[index]
        duration = max(0.0, segment.end - segment.start)
        character_count = len(segment.text.strip())
        speaker_overlap = sum(
            _overlap(segment.start, segment.end, turn.start, turn.end)
            for turn in turns
            if turn.speaker == segment.speaker
        )
        if (
            segment.speaker in {None, UNKNOWN_SPEAKER}
            or duration >= options.max_unsupported_fragment_duration
            or character_count > options.max_unsupported_fragment_characters
            or speaker_overlap >= options.min_fragment_speaker_overlap
        ):
            index += 1
            continue
        neighbours = [
            candidate_index
            for candidate_index in (index - 1, index + 1)
            if 0 <= candidate_index < len(segments)
            and segments[candidate_index].speaker not in {None, UNKNOWN_SPEAKER}
        ]
        if not neighbours:
            index += 1
            continue
        selected_index = max(
            neighbours,
            key=lambda candidate_index: (
                segments[candidate_index].end - segments[candidate_index].start,
                candidate_index < index,
            ),
        )
        neighbour = segments[selected_index]
        ordered = [neighbour, segment] if selected_index < index else [segment, neighbour]
        segments[selected_index] = _combine_segments(ordered, neighbour.speaker)
        segments.pop(index)
        absorbed += 1
        if selected_index < index:
            index = max(0, selected_index)
    return absorbed


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
        speaker_confidence=(
            "low" if any(segment.speaker_confidence == "low" for segment in segments) else "high"
        ),
    )


def _copy_segment(segment: Segment) -> Segment:
    """Copy mutable segment containers while reusing immutable words."""
    return Segment(
        start=segment.start,
        end=segment.end,
        text=segment.text,
        words=list(segment.words),
        speaker=segment.speaker,
        speaker_confidence=segment.speaker_confidence,
    )


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    """Return intersection duration for two time intervals."""
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))
