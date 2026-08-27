from __future__ import annotations

import pytest

from utteran.asr.whisper_cpp_words import has_dtw_timestamps, tokens_to_words


def token(text: str, t_dtw: int, start_ms: int, end_ms: int, p: float = 0.8) -> dict:
    return {
        "text": text,
        "t_dtw": t_dtw,
        "offsets": {"from": start_ms, "to": end_ms},
        "p": p,
    }


def test_special_tokens_are_ignored_and_utf8_fragments_are_joined() -> None:
    words = tokens_to_words(
        [
            token("[_BEG_]", -1, 0, 0),
            token("\xe3", 10, 100, 110),
            token("\x81", 11, 110, 120),
            token("\x82", 12, 120, 130),
            token("。", 13, 130, 140),
        ],
        segment_start=0.0,
        segment_end=1.0,
    )

    assert [word.text for word in words] == ["あ。"]
    assert words[0].start == pytest.approx(0.1)
    assert words[0].end == pytest.approx(0.14)
    assert words[0].probability == pytest.approx(0.8)


def test_space_prefixed_tokens_start_english_words() -> None:
    words = tokens_to_words(
        [token(" Hello", 0, 0, 50), token(" world", 5, 50, 100)],
        segment_start=0.0,
        segment_end=1.0,
    )
    assert [word.text for word in words] == ["Hello", "world"]


def test_missing_dtw_uses_millisecond_offsets() -> None:
    words = tokens_to_words([token(" test", -1, 250, 750)], segment_start=0.0, segment_end=1.0)
    assert (words[0].start, words[0].end) == pytest.approx((0.25, 0.75))
    assert not has_dtw_timestamps([token(" test", -1, 250, 750)])
    assert has_dtw_timestamps([token(" test", 25, 250, 750)])


def test_invalid_times_are_clamped_to_segment() -> None:
    words = tokens_to_words([token(" test", 0, 0, 9000)], segment_start=1.0, segment_end=2.0)
    assert words[0].start == 1.0
    assert words[0].end == 2.0


def test_out_of_segment_or_non_increasing_dtw_falls_back_to_token_offsets() -> None:
    words = tokens_to_words(
        [
            token("\xe3\x81\x82", 10, 100_100, 100_240),
            token("\xe3\x81\x84", 10, 100_240, 100_390),
        ],
        segment_start=100.0,
        segment_end=100.5,
    )

    assert [(word.start, word.end) for word in words] == pytest.approx(
        [(100.1, 100.24), (100.24, 100.39)]
    )


def test_segment_relative_offsets_are_shifted_to_the_recording_timeline() -> None:
    words = tokens_to_words(
        [token(" test", -1, 100, 390)],
        segment_start=100.0,
        segment_end=100.5,
    )

    assert (words[0].start, words[0].end) == pytest.approx((100.1, 100.39))


def test_valid_offsets_take_priority_over_backward_dtw() -> None:
    words = tokens_to_words(
        [
            token(" first", 20, 1000, 1500),
            token(" second", 10, 1500, 2000),
        ],
        segment_start=0.0,
        segment_end=3.0,
    )

    assert [(word.start, word.end) for word in words] == pytest.approx([(1.0, 1.5), (1.5, 2.0)])


def test_vad_mapped_offsets_override_compressed_dtw_timeline() -> None:
    words = tokens_to_words(
        [
            token(" after", 24, 6130, 6570),
            token(" silence", 68, 6570, 7310),
        ],
        segment_start=6.08,
        segment_end=10.76,
    )

    assert [(word.start, word.end) for word in words] == pytest.approx([(6.13, 6.57), (6.57, 7.31)])


def test_probability_is_mean_of_constituent_byte_tokens() -> None:
    words = tokens_to_words(
        [token("\xe3", 10, 100, 110, 0.3), token("\x81\x82", 11, 110, 120, 0.9)],
        segment_start=0.0,
        segment_end=1.0,
    )
    assert words[0].probability == pytest.approx(0.6)
