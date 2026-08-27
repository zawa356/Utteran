"""Reference-based speaker diarization quality metrics.

The evaluator intentionally operates on timestamps and opaque speaker labels.  It never
logs or returns transcript text, so it can also be used with confidential recordings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import pairwise, permutations
from pathlib import Path
from typing import Any

from utteran.align import UNKNOWN_SPEAKER
from utteran.types import Segment, SpeakerTurn, Word


@dataclass(frozen=True)
class ReferenceWord:
    """A timestamped reference word and its expected speaker."""

    start: float
    end: float
    speaker: str
    is_acknowledgement: bool = False


@dataclass(frozen=True)
class DiarizationGroundTruth:
    """Exclusive reference speaker turns and timestamped reference words."""

    duration: float
    turns: tuple[SpeakerTurn, ...]
    words: tuple[ReferenceWord, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DiarizationGroundTruth:
        """Validate and load the versioned ground-truth JSON representation."""
        if payload.get("schema_version") != 1:
            raise ValueError("unsupported diarization ground-truth schema")
        duration = float(payload["duration"])
        turns = tuple(
            SpeakerTurn(float(turn["start"]), float(turn["end"]), str(turn["speaker"]))
            for turn in payload.get("turns", [])
        )
        words = tuple(
            ReferenceWord(
                float(word["start"]),
                float(word["end"]),
                str(word["speaker"]),
                bool(word.get("is_acknowledgement", False)),
            )
            for word in payload.get("words", [])
        )
        _validate_intervals(duration, turns, "turn")
        _validate_intervals(duration, words, "word")
        return cls(duration, turns, words)

    @classmethod
    def load(cls, path: Path) -> DiarizationGroundTruth:
        """Load UTF-8 JSON ground truth from *path*."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("diarization ground truth must be a JSON object")
        return cls.from_dict(payload)


def evaluate_diarization(
    reference: DiarizationGroundTruth,
    hypothesis: list[Segment],
    *,
    short_turn_seconds: float = 0.5,
) -> dict[str, Any]:
    """Compare speaker-attributed segments with an exclusive reference timeline."""
    hypothesis_turns = _segments_to_turns(hypothesis, reference.duration)
    mapping = _optimal_speaker_mapping(reference.turns, hypothesis_turns)
    der = _der_components(reference.duration, reference.turns, hypothesis_turns, mapping)
    reference_boundaries = _speaker_boundaries(reference.turns)
    hypothesis_boundaries = _speaker_boundaries(hypothesis_turns, mapping)
    boundary_errors = [
        min(
            (abs(boundary - candidate) for candidate in hypothesis_boundaries),
            default=reference.duration,
        )
        for boundary in reference_boundaries
    ]
    word_labels = [
        _mapped_speaker_at((word.start + word.end) / 2.0, hypothesis_turns, mapping)
        for word in reference.words
    ]
    acknowledgement_indexes = [
        index for index, word in enumerate(reference.words) if word.is_acknowledgement
    ]
    acknowledgement_matches = sum(
        word_labels[index] == reference.words[index].speaker for index in acknowledgement_indexes
    )
    hypothesis_speech = sum(turn.end - turn.start for turn in hypothesis_turns)
    unknown_seconds = sum(
        turn.end - turn.start
        for turn in hypothesis_turns
        if turn.speaker in {UNKNOWN_SPEAKER, "", None}
    )
    metrics: dict[str, Any] = {
        **der,
        "speaker_mapping": dict(sorted(mapping.items())),
        "reference_boundary_count": len(reference_boundaries),
        "hypothesis_boundary_count": len(hypothesis_boundaries),
        "boundary_error_seconds": _distribution(boundary_errors),
        "mid_word_speaker_boundary_count": _mid_word_boundary_count(
            reference.words, hypothesis_boundaries
        ),
        "short_speaker_turn_count": _short_speaker_turn_count(hypothesis_turns, short_turn_seconds),
        "single_word_speaker_turn_count": _single_word_turn_count(reference.words, word_labels),
        "unknown_ratio": round(unknown_seconds / hypothesis_speech, 6)
        if hypothesis_speech
        else 0.0,
        "acknowledgement_count": len(acknowledgement_indexes),
        "acknowledgement_retained_count": acknowledgement_matches,
        "acknowledgement_retained_ratio": round(
            acknowledgement_matches / len(acknowledgement_indexes), 6
        )
        if acknowledgement_indexes
        else 1.0,
    }
    return metrics


def segments_from_dict(payload: dict[str, Any]) -> list[Segment]:
    """Load only timestamp and speaker fields from an exported utteran JSON object."""
    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        raise ValueError("hypothesis JSON must contain a segments array")
    segments: list[Segment] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            raise ValueError("hypothesis segment must be a JSON object")
        words = [
            Word(float(word["start"]), float(word["end"]), "")
            for word in raw.get("words", [])
            if isinstance(word, dict)
        ]
        segments.append(
            Segment(
                float(raw["start"]),
                float(raw["end"]),
                "",
                words,
                None if raw.get("speaker") is None else str(raw["speaker"]),
            )
        )
    return segments


def _validate_intervals(
    duration: float, intervals: tuple[SpeakerTurn, ...] | tuple[ReferenceWord, ...], name: str
) -> None:
    if duration <= 0.0:
        raise ValueError("ground-truth duration must be positive")
    for interval in intervals:
        if interval.start < 0.0 or interval.start >= interval.end or interval.end > duration:
            raise ValueError(f"invalid {name} interval: {interval.start}-{interval.end}")


def _segments_to_turns(segments: list[Segment], duration: float) -> list[SpeakerTurn]:
    turns = [
        SpeakerTurn(max(0.0, segment.start), min(duration, segment.end), segment.speaker or "")
        for segment in segments
        if segment.start < segment.end and segment.end > 0.0 and segment.start < duration
    ]
    return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))


def _known_speakers(turns: tuple[SpeakerTurn, ...] | list[SpeakerTurn]) -> list[str]:
    return sorted({turn.speaker for turn in turns if turn.speaker not in {UNKNOWN_SPEAKER, ""}})


def _optimal_speaker_mapping(
    reference: tuple[SpeakerTurn, ...], hypothesis: list[SpeakerTurn]
) -> dict[str, str]:
    reference_speakers = _known_speakers(reference)
    hypothesis_speakers = _known_speakers(hypothesis)
    weights = {
        (hypothesis_speaker, reference_speaker): sum(
            _overlap(
                hypothesis_turn.start, hypothesis_turn.end, reference_turn.start, reference_turn.end
            )
            for hypothesis_turn in hypothesis
            for reference_turn in reference
            if hypothesis_turn.speaker == hypothesis_speaker
            and reference_turn.speaker == reference_speaker
        )
        for hypothesis_speaker in hypothesis_speakers
        for reference_speaker in reference_speakers
    }
    if not reference_speakers or not hypothesis_speakers:
        return {}
    if max(len(reference_speakers), len(hypothesis_speakers)) > 8:
        return _greedy_mapping(reference_speakers, hypothesis_speakers, weights)

    best_score = -1.0
    best_mapping: dict[str, str] = {}
    if len(hypothesis_speakers) <= len(reference_speakers):
        for assignment in permutations(reference_speakers, len(hypothesis_speakers)):
            candidate = dict(zip(hypothesis_speakers, assignment, strict=True))
            score = sum(weights[(speaker, candidate[speaker])] for speaker in hypothesis_speakers)
            if score > best_score:
                best_score, best_mapping = score, candidate
    else:
        for selected_hypothesis in permutations(hypothesis_speakers, len(reference_speakers)):
            candidate = dict(zip(selected_hypothesis, reference_speakers, strict=True))
            score = sum(
                weights[(speaker, reference_label)]
                for speaker, reference_label in candidate.items()
            )
            if score > best_score:
                best_score, best_mapping = score, candidate
    return best_mapping


def _greedy_mapping(
    reference_speakers: list[str],
    hypothesis_speakers: list[str],
    weights: dict[tuple[str, str], float],
) -> dict[str, str]:
    pairs = sorted(
        (
            (weights[(hypothesis, reference)], hypothesis, reference)
            for hypothesis in hypothesis_speakers
            for reference in reference_speakers
        ),
        reverse=True,
    )
    mapping: dict[str, str] = {}
    used_references: set[str] = set()
    for _score, hypothesis, reference in pairs:
        if hypothesis not in mapping and reference not in used_references:
            mapping[hypothesis] = reference
            used_references.add(reference)
    return mapping


def _der_components(
    duration: float,
    reference: tuple[SpeakerTurn, ...],
    hypothesis: list[SpeakerTurn],
    mapping: dict[str, str],
) -> dict[str, float]:
    boundaries = sorted(
        {0.0, duration}
        | {value for turn in reference for value in (turn.start, turn.end)}
        | {value for turn in hypothesis for value in (turn.start, turn.end)}
    )
    reference_seconds = miss = false_alarm = confusion = 0.0
    for start, end in pairwise(boundaries):
        midpoint = (start + end) / 2.0
        reference_speaker = _speaker_at(midpoint, reference)
        hypothesis_speaker = _mapped_speaker_at(midpoint, hypothesis, mapping)
        length = end - start
        if reference_speaker is not None:
            reference_seconds += length
        if reference_speaker is not None and hypothesis_speaker is None:
            miss += length
        elif reference_speaker is None and hypothesis_speaker is not None:
            false_alarm += length
        elif (
            reference_speaker is not None
            and hypothesis_speaker is not None
            and reference_speaker != hypothesis_speaker
        ):
            confusion += length
    error = miss + false_alarm + confusion
    return {
        "reference_speech_seconds": round(reference_seconds, 6),
        "miss_seconds": round(miss, 6),
        "false_alarm_seconds": round(false_alarm, 6),
        "speaker_confusion_seconds": round(confusion, 6),
        "diarization_error_rate": round(error / reference_seconds, 6) if reference_seconds else 0.0,
    }


def _speaker_at(timestamp: float, turns: tuple[SpeakerTurn, ...] | list[SpeakerTurn]) -> str | None:
    for turn in turns:
        if turn.start <= timestamp < turn.end and turn.speaker not in {UNKNOWN_SPEAKER, ""}:
            return turn.speaker
    return None


def _mapped_speaker_at(
    timestamp: float, turns: list[SpeakerTurn], mapping: dict[str, str]
) -> str | None:
    speaker = _speaker_at(timestamp, turns)
    if speaker is None:
        return None
    return mapping.get(speaker, f"__UNMAPPED__{speaker}")


def _speaker_boundaries(
    turns: tuple[SpeakerTurn, ...] | list[SpeakerTurn],
    mapping: dict[str, str] | None = None,
) -> list[float]:
    boundaries: list[float] = []
    for left, right in pairwise(turns):
        left_speaker = (
            left.speaker
            if mapping is None
            else mapping.get(left.speaker, f"__UNMAPPED__{left.speaker}")
        )
        right_speaker = (
            right.speaker
            if mapping is None
            else mapping.get(right.speaker, f"__UNMAPPED__{right.speaker}")
        )
        if left_speaker and right_speaker and left_speaker != right_speaker:
            boundaries.append((left.end + right.start) / 2.0)
    return boundaries


def _mid_word_boundary_count(words: tuple[ReferenceWord, ...], boundaries: list[float]) -> int:
    return sum(word.start < boundary < word.end for word in words for boundary in boundaries)


def _short_speaker_turn_count(turns: list[SpeakerTurn], threshold: float) -> int:
    return sum(
        turn.end - turn.start < threshold
        and turn.speaker not in {UNKNOWN_SPEAKER, ""}
        and index > 0
        and index < len(turns) - 1
        and turns[index - 1].speaker != turn.speaker
        and turns[index + 1].speaker != turn.speaker
        for index, turn in enumerate(turns)
    )


def _single_word_turn_count(words: tuple[ReferenceWord, ...], labels: list[str | None]) -> int:
    return sum(
        labels[index] is not None
        and labels[index - 1] == labels[index + 1]
        and labels[index] != labels[index - 1]
        for index in range(1, len(words) - 1)
    )


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = round((len(ordered) - 1) * 0.95)
    return {
        "count": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 6),
        "p95": round(ordered[p95_index], 6),
        "max": round(ordered[-1], 6),
    }


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))
