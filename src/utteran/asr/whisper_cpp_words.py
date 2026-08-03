"""Convert whisper.cpp full-JSON tokens into backend-neutral words."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from utteran.types import Word

DTW_SECONDS_PER_TICK = 0.01


@dataclass(frozen=True)
class _Piece:
    text: str
    start: float
    end: float
    probability: float | None


def tokens_to_words(
    tokens: Sequence[Mapping[str, Any]], *, segment_start: float, segment_end: float
) -> list[Word]:
    """Join byte fragments, discard control tokens, and derive bounded word times."""
    pieces = _decode_pieces(tokens, segment_start, segment_end)
    words: list[Word] = []
    current: list[_Piece] = []

    def flush() -> None:
        if not current:
            return
        probabilities = [piece.probability for piece in current if piece.probability is not None]
        words.append(
            Word(
                start=current[0].start,
                end=current[-1].end,
                text="".join(piece.text for piece in current),
                probability=(sum(probabilities) / len(probabilities) if probabilities else None),
            )
        )
        current.clear()

    for piece in pieces:
        leading_space = piece.text[:1].isspace()
        text = piece.text.lstrip()
        if not text:
            continue
        if leading_space:
            flush()
        for character in text:
            character_piece = _Piece(character, piece.start, piece.end, piece.probability)
            if _is_cjk(character):
                flush()
                current.append(character_piece)
                flush()
            elif _is_punctuation(character) and words and not current:
                previous = words[-1]
                words[-1] = Word(
                    previous.start,
                    max(previous.end, character_piece.end),
                    previous.text + character,
                    previous.probability,
                )
            else:
                current.append(character_piece)
    flush()
    return words


def has_dtw_timestamps(tokens: Sequence[Mapping[str, Any]]) -> bool:
    """Return whether at least one ordinary token contains a DTW timestamp."""
    return any(not _is_special(str(token.get("text", ""))) and _dtw(token) >= 0 for token in tokens)


def _decode_pieces(
    tokens: Sequence[Mapping[str, Any]], segment_start: float, segment_end: float
) -> list[_Piece]:
    pieces: list[_Piece] = []
    pending: list[Mapping[str, Any]] = []
    pending_bytes = b""
    for token in tokens:
        text = str(token.get("text", ""))
        if _is_special(text):
            continue
        raw = _token_bytes(text)
        pending.append(token)
        pending_bytes += raw
        try:
            decoded = pending_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            if error.reason == "unexpected end of data":
                continue
            decoded = pending_bytes.decode("utf-8", errors="replace")
        pieces.append(_piece(decoded, pending, segment_start, segment_end))
        pending = []
        pending_bytes = b""
    if pending:
        pieces.append(
            _piece(
                pending_bytes.decode("utf-8", errors="replace"),
                pending,
                segment_start,
                segment_end,
            )
        )
    return pieces


def _piece(
    text: str,
    tokens: Sequence[Mapping[str, Any]],
    segment_start: float,
    segment_end: float,
) -> _Piece:
    start = _token_time(tokens[0], "from", segment_start)
    end = _token_time(tokens[-1], "to", segment_end)
    start = min(max(start, segment_start), segment_end)
    end = min(max(end, start), segment_end)
    probabilities = [float(token["p"]) for token in tokens if token.get("p") is not None]
    probability = sum(probabilities) / len(probabilities) if probabilities else None
    return _Piece(text, start, end, probability)


def _token_time(token: Mapping[str, Any], edge: str, fallback: float) -> float:
    dtw = _dtw(token)
    if dtw >= 0:
        # whisper_token_data::t_dtw uses the same 10 ms tick as t0/t1.
        return dtw * DTW_SECONDS_PER_TICK
    offsets = token.get("offsets")
    if isinstance(offsets, Mapping) and offsets.get(edge) is not None:
        return float(offsets[edge]) / 1000.0
    return fallback


def _dtw(token: Mapping[str, Any]) -> int:
    try:
        return int(token.get("t_dtw", -1))
    except (TypeError, ValueError):
        return -1


def _token_bytes(text: str) -> bytes:
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError:
        return text.encode("utf-8")


def _is_special(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("[") or stripped.startswith("<|")


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x9FFF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _is_punctuation(character: str) -> bool:
    return unicodedata.category(character).startswith("P")
