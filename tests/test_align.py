from __future__ import annotations

from pytest import MonkeyPatch

from utteran.align import (
    UNKNOWN_SPEAKER,
    align_transcription,
    align_transcription_with_statistics,
    assign_word_speaker,
)
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


def test_identical_timestamp_group_is_never_split_between_speakers(
    monkeypatch: MonkeyPatch,
) -> None:
    words = [
        Word(0.0, 0.4, "前"),
        Word(0.4, 0.8, "同"),
        Word(0.4, 0.8, "時"),
        Word(0.8, 1.2, "後"),
    ]
    monkeypatch.setattr(
        "utteran.align._assign_word_speaker_sequence",
        lambda _words, _turns, _options: ["LEFT", "LEFT", "RIGHT", "RIGHT"],
    )

    result, statistics = align_transcription_with_statistics(
        transcription(Segment(0.0, 1.2, "前同時後", words)),
        diarization([]),
        AlignmentOptions(
            boundary_snap_enabled=False,
            max_unsupported_fragment_duration=0.0,
        ),
    )

    assert [segment.text for segment in result] == ["前同時", "後"]
    assert result[0].end <= result[1].start
    assert statistics["boundary_snapping"]["protected_identical_timestamp_group_count"] == 1


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


def test_segment_timing_fallback_does_not_merge_across_asr_boundary() -> None:
    result = align_transcription(
        transcription(
            Segment(0.0, 5.0, "fallback", []),
            Segment(5.0, 6.0, "timed", [Word(5.0, 6.0, "timed")]),
        ),
        diarization([SpeakerTurn(0.0, 6.0, "MAIN")]),
    )

    assert [segment.text for segment in result] == ["fallback", "timed"]
    assert [segment.speaker for segment in result] == ["SPEAKER_00", "SPEAKER_00"]


def test_japanese_boundary_snaps_by_character_position_and_updates_time() -> None:
    text = "ありがとうございました"
    words = [
        Word(index * 0.1, (index + 1) * 0.1, character) for index, character in enumerate(text)
    ]
    result, statistics = align_transcription_with_statistics(
        transcription(Segment(0.0, 1.1, text, words)),
        diarization([SpeakerTurn(0.0, 0.1, "NOISE"), SpeakerTurn(0.1, 1.1, "RESPONSE")]),
        AlignmentOptions(
            speaker_switch_penalty=0.0,
            max_unsupported_fragment_duration=0.0,
        ),
    )

    assert [(segment.start, segment.end, segment.text) for segment in result] == [(0.0, 1.1, text)]
    assert statistics["boundary_snapping"]["snapped_boundary_count"] == 1
    assert statistics["boundary_snapping"]["moved_character_count"] == 1
    assert statistics["boundary_snapping"]["moved_time_seconds"]["max"] == 0.1


def test_non_japanese_language_bypasses_boundary_analyzer(monkeypatch: MonkeyPatch) -> None:
    def fail_if_called(text: str, unit: str) -> set[int]:
        raise AssertionError((text, unit))

    monkeypatch.setattr("utteran.align.japanese_morpheme_boundaries", fail_if_called)
    source = TranscriptionResult(
        [Segment(0.0, 1.0, "hello", [Word(0.0, 0.2, "h"), Word(0.2, 1.0, "ello")])],
        "en",
        1.0,
        "fake",
        "fake",
        "cpu",
    )

    _result, statistics = align_transcription_with_statistics(
        source,
        diarization([SpeakerTurn(0.0, 0.2, "A"), SpeakerTurn(0.2, 1.0, "B")]),
        AlignmentOptions(speaker_switch_penalty=0.0),
    )

    assert statistics["boundary_snapping"]["enabled"] is False


def test_missing_boundary_analyzer_keeps_previous_assignment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("utteran.align.japanese_morpheme_boundaries", lambda _text, _unit: None)
    words = [Word(0.0, 0.2, "了"), Word(0.2, 1.0, "解")]

    result, statistics = align_transcription_with_statistics(
        transcription(Segment(0.0, 1.0, "了解", words)),
        diarization([SpeakerTurn(0.0, 0.2, "A"), SpeakerTurn(0.2, 1.0, "B")]),
        AlignmentOptions(speaker_switch_penalty=0.0),
    )

    assert [segment.text for segment in result] == ["了", "解"]
    assert statistics["boundary_snapping"]["analyzer_available"] is False


def test_missing_word_timing_is_capped_and_marked_low_confidence() -> None:
    result, statistics = align_transcription_with_statistics(
        transcription(Segment(10.0, 76.16, "十文字の発話です。", [])),
        diarization([]),
    )

    assert result[0].end < 14.0
    assert result[0].speaker_confidence == "low"
    assert statistics["fallback_timing"]["trimmed_segment_count"] == 1
    assert statistics["fallback_timing"]["trimmed_seconds"] > 60.0
    assert statistics["fallback_timing"]["character_cap_count"] == 1


def test_missing_word_timing_prefers_detected_speech_envelope() -> None:
    result, statistics = align_transcription_with_statistics(
        transcription(Segment(10.0, 76.16, "短い発話", [])),
        diarization([SpeakerTurn(20.0, 24.0, "MAIN"), SpeakerTurn(26.0, 30.0, "MAIN")]),
    )

    assert (result[0].start, result[0].end) == (20.0, 30.0)
    assert result[0].speaker_confidence == "low"
    assert statistics["fallback_timing"]["speech_envelope_count"] == 1
    assert statistics["fallback_timing"]["detected_speech_coverage"] == 1.0


def test_partial_word_timing_preserves_full_text_without_inventing_a_split() -> None:
    segment = Segment(
        0.0,
        2.0,
        "full transcript",
        [Word(0.0, 0.8, "full"), Word(1.2, 2.0, "script")],
        speaker_confidence="low",
    )
    result = align_transcription(
        transcription(segment),
        diarization([SpeakerTurn(0.0, 1.0, "A"), SpeakerTurn(1.0, 2.0, "B")]),
        AlignmentOptions(speaker_switch_penalty=0.0),
    )

    assert len(result) == 1
    assert result[0].text == "full transcript"
    assert result[0].speaker_confidence == "low"


def test_supported_short_response_is_recovered_from_a_long_segment_tail() -> None:
    words = [Word(0.0, 1.0, "長"), Word(1.0, 2.0, "文"), Word(2.0, 2.5, "応答")]
    result, statistics = align_transcription_with_statistics(
        transcription(Segment(0.0, 2.5, "長文応答", words)),
        diarization([SpeakerTurn(0.0, 2.0, "MAIN"), SpeakerTurn(2.0, 2.5, "RESPONSE")]),
        AlignmentOptions(
            speaker_switch_penalty=10.0,
            boundary_snap_enabled=False,
            max_unsupported_fragment_duration=0.0,
        ),
    )

    assert [segment.text for segment in result] == ["長文", "応答"]
    assert statistics["boundary_snapping"]["trailing_response_recovery_count"] == 1


def test_sandwiched_fragment_is_absorbed_only_when_outer_speaker_has_support(
    monkeypatch: MonkeyPatch,
) -> None:
    words = [Word(0.0, 1.0, "A"), Word(1.0, 1.5, "x"), Word(1.5, 2.5, "B")]
    monkeypatch.setattr(
        "utteran.align._assign_word_speaker_sequence",
        lambda _words, _turns, _options: ["OUTER", "FRAGMENT", "OUTER"],
    )
    result, statistics = align_transcription_with_statistics(
        transcription(Segment(0.0, 2.5, "AxB", words)),
        diarization([SpeakerTurn(0.0, 2.5, "OUTER")]),
        AlignmentOptions(
            boundary_snap_enabled=False,
            max_unsupported_fragment_duration=0.0,
        ),
    )

    assert len(result) == 1
    assert statistics["sandwiched_fragment_reassessment_count"] == 1
