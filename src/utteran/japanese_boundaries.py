"""Optional Sudachi-backed Japanese boundary detection."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Literal, Protocol, cast


class _Tokenizer(Protocol):
    def tokenize(self, text: str, mode: Any) -> list[Any]: ...


@lru_cache(maxsize=1)
def _load_sudachi() -> tuple[_Tokenizer, Any, Any] | None:
    """Load Sudachi lazily so missing optional dependencies never stop a job."""
    try:
        from sudachipy import dictionary, tokenizer
    except (ImportError, OSError) as exc:
        logging.getLogger(__name__).warning(
            "SudachiPyまたはsudachidict_coreを利用できないため、日本語の話者境界補正を"
            "スキップします: %s",
            exc,
        )
        return None
    try:
        instance = cast(_Tokenizer, dictionary.Dictionary().create())
    except Exception as exc:  # Sudachi also raises its own dictionary/config exceptions.
        logging.getLogger(__name__).warning(
            "Sudachi辞書を初期化できないため、日本語の話者境界補正をスキップします: %s",
            exc,
        )
        return None
    return instance, tokenizer.Tokenizer.SplitMode.A, tokenizer.Tokenizer.SplitMode.B


def japanese_morpheme_boundaries(text: str, unit: Literal["A", "B"] = "A") -> set[int] | None:
    """Return character offsets after Sudachi morphemes, or ``None`` if unavailable."""
    loaded = _load_sudachi()
    if loaded is None:
        return None
    instance, mode_a, mode_b = loaded
    boundaries = {0}
    offset = 0
    morphemes = instance.tokenize(text, mode_a if unit == "A" else mode_b)
    for index, morpheme in enumerate(morphemes):
        offset += len(str(morpheme.surface()))
        next_is_attached = index + 1 < len(morphemes) and _is_attached_morpheme(
            morphemes[index + 1]
        )
        current_pos = tuple(str(value) for value in morpheme.part_of_speech())
        current_is_prefix = bool(current_pos) and current_pos[0] == "接頭辞"
        if not next_is_attached and not current_is_prefix:
            boundaries.add(offset)
    # A dictionary/plugin mismatch must not cause text loss or a bogus snap.
    if offset != len(text):
        logging.getLogger(__name__).warning(
            "Sudachiの文字位置が入力と一致しないため、日本語の話者境界補正をスキップします"
        )
        return None
    return boundaries


def japanese_phrase_boundaries(text: str) -> set[int] | None:
    """Return conservative phrase-like offsets derived from Sudachi POS information."""
    loaded = _load_sudachi()
    if loaded is None:
        return None
    instance, mode_a, _mode_b = loaded
    morphemes = list(instance.tokenize(text, mode_a))
    boundaries = {0, len(text)}
    offset = 0
    for index, morpheme in enumerate(morphemes[:-1]):
        offset += len(str(morpheme.surface()))
        current = tuple(str(value) for value in morpheme.part_of_speech())
        following = tuple(str(value) for value in morphemes[index + 1].part_of_speech())
        current_major = current[0] if current else ""
        following_major = following[0] if following else ""
        if current_major in {"副詞", "連体詞"} and following_major not in {
            "助詞",
            "助動詞",
            "接尾辞",
        }:
            boundaries.add(offset)
    if sum(len(str(morpheme.surface())) for morpheme in morphemes) != len(text):
        return None
    return boundaries


def _is_attached_morpheme(morpheme: Any) -> bool:
    """Return whether a morpheme should not begin a speaker segment."""
    part_of_speech = tuple(str(value) for value in morpheme.part_of_speech())
    major = part_of_speech[0] if part_of_speech else ""
    subtype = part_of_speech[1] if len(part_of_speech) > 1 else ""
    return major in {"助詞", "助動詞", "接尾辞"} or (major == "動詞" and subtype == "非自立可能")
