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


def test_probability_is_mean_of_constituent_byte_tokens() -> None:
    words = tokens_to_words(
        [token("\xe3", 10, 100, 110, 0.3), token("\x81\x82", 11, 110, 120, 0.9)],
        segment_start=0.0,
        segment_end=1.0,
    )
    assert words[0].probability == pytest.approx(0.6)
