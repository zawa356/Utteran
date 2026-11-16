"""Versioned, pure compilation of prompt + glossary into the engine prompt text.

A glossary is a separate setting from ``initial_prompt``. Engines whose declaration says
``glossary="prompt_bias"`` receive it as leading text of the decoder prompt; nothing is
dropped silently and no recognition accuracy is promised (the engine keeps only the last
part of a long prompt, so the user's own prompt is placed last).

The compiler version is frozen in every job snapshot (``ResolvedSpec``) and sent to the
worker: a retry of an old snapshot is compiled by the version it was frozen with.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, get_args

PromptCompiler = Literal["b2-verbatim", "glossary-bias-1"]
PROMPT_COMPILERS: tuple[str, ...] = get_args(PromptCompiler)
CURRENT_PROMPT_COMPILER: PromptCompiler = "glossary-bias-1"
# Heuristic only (no tokenizer here): Whisper-family decoders keep roughly the last 224
# tokens of the prompt. Above this many characters the head of the glossary may not reach
# the model; validation warns, it does not refuse.
BIAS_SOFT_LIMIT = 400
_IDEOGRAPHIC = frozenset({"ja", "zh", "yue"})


def glossary_terms(glossary: Sequence[str]) -> tuple[str, ...]:
    """Trimmed, non-empty terms in first-seen order (exact duplicates removed)."""
    return tuple(dict.fromkeys(term.strip() for term in glossary if term.strip()))


def compile_prompt(
    initial_prompt: str | None,
    glossary: Sequence[str],
    language: str,
    *,
    compiler: str,
) -> str | None:
    """Engine prompt text for these settings, or ValueError with a fixed code.

    Without glossary terms every version returns ``initial_prompt`` unchanged, so
    existing presets keep the same core Config and stage hashes.
    """
    if compiler not in PROMPT_COMPILERS:
        raise ValueError("unsupported_prompt_compiler")
    terms = glossary_terms(glossary)
    if not terms:
        return initial_prompt
    if compiler == "b2-verbatim":
        # That version had no glossary support: refuse rather than ignore the terms.
        raise ValueError("glossary_requires_compiler")
    separator = "、" if language.strip().casefold() in _IDEOGRAPHIC else ", "
    bias = separator.join(terms)
    return bias if not initial_prompt else f"{bias}\n{initial_prompt}"


def bias_may_be_truncated(initial_prompt: str | None, glossary: Sequence[str]) -> bool:
    terms = glossary_terms(glossary)
    if not terms:
        return False
    return sum(map(len, terms)) + 2 * len(terms) + len(initial_prompt or "") > BIAS_SOFT_LIMIT
