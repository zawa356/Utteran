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


def test_no_containing_turn_uses_overlap_then_unknown_without_nearest() -> None:
    turns = [SpeakerTurn(1.0, 2.0, "SPEAKER")]

    assert assign_word_speaker(Word(0.0, 1.5, "overlap"), turns) == "SPEAKER"
    assert assign_word_speaker(Word(2.2, 2.3, "near"), turns) == UNKNOWN_SPEAKER
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
        diarization([SpeakerTurn(0.0, 0.45, "FIRST"), SpeakerTurn(0.45, 1.0, "SECOND")]),
        AlignmentOptions(min_segment_duration=0.0, min_segment_words=0),
    )

    assert [segment.text for segment in result] == ["A", "B"]
    assert [segment.speaker for segment in result] == ["SPEAKER_00", "SPEAKER_01"]


def test_split_segment_contains_non_monotonic_word_end_times() -> None:
    words = [Word(0.1, 1.2, "A"), Word(0.0, 1.0, "B")]
    result = align_transcription(
        transcription(Segment(0.0, 1.2, "AB", words)),
        diarization([SpeakerTurn(0.0, 2.0, "SPEAKER")]),
        AlignmentOptions(min_segment_duration=0.0, min_segment_words=0),
    )

    assert (result[0].start, result[0].end) == (0.0, 1.2)


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


def test_viterbi_smooths_two_rapid_spurious_speakers_inside_continuous_speech() -> None:
    words = [
        Word(0.0, 0.4, "ま"),
        Word(0.4, 0.6, "と"),
        Word(0.6, 0.8, "め"),
        Word(0.8, 1.2, "て"),
    ]
    result = align_transcription(
        transcription(Segment(0.0, 1.2, "まとめて", words)),
        diarization(
            [
                SpeakerTurn(0.0, 0.4, "MAIN"),
                SpeakerTurn(0.4, 0.6, "NOISE_A"),
                SpeakerTurn(0.6, 0.8, "NOISE_B"),
                SpeakerTurn(0.8, 1.2, "MAIN"),
            ]
        ),
    )

    assert len(result) == 1
    assert result[0].text == "まとめて"
    assert result[0].speaker == "SPEAKER_00"


def test_viterbi_preserves_short_acknowledgement_separated_by_silence() -> None:
    words = [
        Word(0.2, 0.8, "質問"),
        Word(1.42, 1.7, "はい"),
        Word(2.2, 2.8, "続行"),
    ]
    result = align_transcription(
        transcription(Segment(0.0, 3.0, "質問はい続行", words)),
        diarization(
            [
                SpeakerTurn(0.0, 1.0, "MAIN"),
                SpeakerTurn(1.4, 1.75, "ACK"),
                SpeakerTurn(2.1, 3.0, "MAIN"),
            ]
        ),
    )

    assert [segment.text for segment in result] == ["質問", "はい", "続行"]
    assert [segment.speaker for segment in result] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "SPEAKER_00",
    ]


def test_viterbi_penalizes_unknown_transitions_like_known_speaker_transitions() -> None:
    words = [Word(0.0, 0.4, "A"), Word(0.4, 0.5, "x"), Word(0.5, 0.9, "B")]
    result = align_transcription(
        transcription(Segment(0.0, 0.9, "AxB", words)),
        diarization([SpeakerTurn(0.0, 0.4, "MAIN"), SpeakerTurn(0.5, 0.9, "MAIN")]),
    )

    assert len(result) == 1
    assert result[0].speaker == "SPEAKER_00"


def test_short_unknown_is_absorbed_by_longer_known_neighbour() -> None:
    words = [Word(0.0, 1.0, "long"), Word(1.0, 1.2, "x"), Word(1.2, 1.5, "short")]
    result = align_transcription(
        transcription(Segment(0.0, 1.5, "longxshort", words)),
        diarization([SpeakerTurn(0.0, 1.0, "LEFT"), SpeakerTurn(1.2, 1.5, "RIGHT")]),
        AlignmentOptions(speaker_switch_penalty=0.0),
    )

    assert [segment.speaker for segment in result] == ["SPEAKER_00", "SPEAKER_01"]
    assert result[0].text == "longx"


def test_viterbi_preserves_dense_changes_supported_by_clear_turns() -> None:
    words = [
        Word(0.1, 0.7, "A"),
        Word(0.85, 1.35, "B"),
        Word(1.5, 2.0, "C"),
    ]
    result = align_transcription(
        transcription(Segment(0.0, 2.1, "ABC", words)),
        diarization(
            [
                SpeakerTurn(0.0, 0.8, "FIRST"),
                SpeakerTurn(0.8, 1.45, "SECOND"),
                SpeakerTurn(1.45, 2.1, "THIRD"),
            ]
        ),
    )

    assert [segment.speaker for segment in result] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "SPEAKER_02",
    ]


def test_viterbi_bridges_only_a_short_gap_between_the_same_speaker() -> None:
    words = [
        Word(0.5, 0.9, "A"),
        Word(1.02, 1.18, "gap"),
        Word(1.3, 1.8, "B"),
    ]
    same_speaker = align_transcription(
        transcription(Segment(0.0, 2.0, "AgapB", words)),
        diarization([SpeakerTurn(0.0, 1.0, "MAIN"), SpeakerTurn(1.2, 2.0, "MAIN")]),
    )
    different_speakers = align_transcription(
        transcription(Segment(0.0, 2.0, "AgapB", words)),
        diarization([SpeakerTurn(0.0, 1.0, "FIRST"), SpeakerTurn(1.2, 2.0, "SECOND")]),
        AlignmentOptions(min_unknown_duration=0.0, min_unknown_characters=0),
    )

    assert [segment.speaker for segment in same_speaker] == ["SPEAKER_00"]
    assert [segment.speaker for segment in different_speakers] == ["SPEAKER_00", "SPEAKER_01"]


def test_missing_word_timestamps_do_not_make_a_long_segment_a_short_island() -> None:
    result = align_transcription(
        transcription(
            Segment(0.0, 2.0, "A"),
            Segment(2.0, 12.0, "B"),
            Segment(12.0, 14.0, "C"),
        ),
        diarization(
            [
                SpeakerTurn(0.0, 2.0, "MAIN"),
                SpeakerTurn(2.0, 12.0, "ISLAND"),
                SpeakerTurn(12.0, 14.0, "MAIN"),
            ]
        ),
    )

    assert len(result) == 3
    assert [segment.speaker for segment in result] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "SPEAKER_00",
    ]


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
