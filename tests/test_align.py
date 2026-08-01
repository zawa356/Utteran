from __future__ import annotations

from utteran.align import UNKNOWN_SPEAKER, align_transcription, assign_word_speaker
from utteran.types import (
    AlignmentOptions,
    DiarizationResult,
    Segment,
    SpeakerTurn,
    TranscriptionResult,
    Word,
)


def transcription(*segments: Segment) -> TranscriptionResult:
    return TranscriptionResult(list(segments), "ja", 10.0, "fake", "fake", "cpu")


def diarization(
    turns: list[SpeakerTurn], exclusive: list[SpeakerTurn] | None = None
) -> DiarizationResult:
    return DiarizationResult(turns, exclusive, 2, "fake", "fake", "cpu")


def test_exclusive_turns_take_priority() -> None:
    word = Word(0.0, 1.0, "語")
    result = align_transcription(
        transcription(Segment(0.0, 1.0, "語", [word])),
        diarization(
            [SpeakerTurn(0.0, 1.0, "REGULAR")],
            [SpeakerTurn(0.0, 1.0, "EXCLUSIVE")],
        ),
    )

    assert result[0].speaker == "SPEAKER_00"


def test_overlapping_regular_turns_prefer_earlier_start() -> None:
    word = Word(1.2, 1.4, "word")
    turns = [
        SpeakerTurn(1.0, 2.0, "LATER"),
        SpeakerTurn(0.0, 1.5, "EARLIER"),
    ]

    assert assign_word_speaker(word, turns) == "EARLIER"


def test_no_containing_turn_uses_overlap_then_nearest_then_unknown() -> None:
    turns = [SpeakerTurn(1.0, 2.0, "SPEAKER")]

    assert assign_word_speaker(Word(0.0, 1.5, "overlap"), turns) == "SPEAKER"
    assert assign_word_speaker(Word(2.2, 2.3, "near"), turns) == "SPEAKER"
    assert assign_word_speaker(Word(4.1, 4.2, "far"), turns) == UNKNOWN_SPEAKER


def test_segment_splits_on_speaker_change() -> None:
    words = [Word(0.0, 0.4, "A"), Word(0.5, 0.9, "B")]
    result = align_transcription(
        transcription(Segment(0.0, 0.9, "AB", words)),
        diarization(
            [SpeakerTurn(0.0, 0.45, "FIRST"), SpeakerTurn(0.45, 1.0, "SECOND")],
            [],
        ),
        AlignmentOptions(min_segment_duration=0.0, min_segment_words=0),
    )

    assert [segment.speaker for segment in result] == [UNKNOWN_SPEAKER]


def test_segment_splits_using_regular_turns_when_exclusive_is_none() -> None:
    words = [Word(0.0, 0.4, "A"), Word(0.5, 0.9, "B")]
    result = align_transcription(
        transcription(Segment(0.0, 0.9, "AB", words)),
        diarization(
            [SpeakerTurn(0.0, 0.45, "FIRST"), SpeakerTurn(0.45, 1.0, "SECOND")]
        ),
        AlignmentOptions(min_segment_duration=0.0, min_segment_words=0),
    )

    assert [segment.text for segment in result] == ["A", "B"]
    assert [segment.speaker for segment in result] == ["SPEAKER_00", "SPEAKER_01"]


def test_extremely_short_speaker_island_is_absorbed() -> None:
    words = [
        Word(0.0, 0.4, "A"),
        Word(0.4, 0.5, "x"),
        Word(0.5, 1.0, "B"),
    ]
    result = align_transcription(
        transcription(Segment(0.0, 1.0, "AxB", words)),
        diarization(
            [
                SpeakerTurn(0.0, 0.4, "MAIN"),
                SpeakerTurn(0.4, 0.5, "ISLAND"),
                SpeakerTurn(0.5, 1.0, "MAIN"),
            ]
        ),
    )

    assert len(result) == 1
    assert result[0].speaker == "SPEAKER_00"
    assert result[0].text == "AxB"


def test_same_speaker_segments_merge_within_gap_and_renumber_by_appearance() -> None:
    result = align_transcription(
        transcription(
            Segment(0.0, 0.5, "A", [Word(0.0, 0.5, "A")]),
            Segment(0.8, 1.2, "B", [Word(0.8, 1.2, "B")]),
        ),
        diarization([SpeakerTurn(0.0, 2.0, "BACKEND_42")]),
        AlignmentOptions(min_segment_duration=0.0, min_segment_words=0),
    )

    assert len(result) == 1
    assert result[0].text == "AB"
    assert result[0].speaker == "SPEAKER_00"
