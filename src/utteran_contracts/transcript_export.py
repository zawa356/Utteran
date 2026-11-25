"""Pure transcript rendering shared by the core exporters and E-C re-export (0.1.69).

One read model (:class:`TranscriptView`) holds exactly the values the five exporters use;
nothing else (no language, no diarization turns). The core exporters convert their
``PipelineResult`` to this view and the GUI builds it from a verified JSON v1 document,
so both paths run the same formatting code. No filesystem access, no core import.

Byte-level contract (unchanged from the 0.1.68 exporters): every renderer returns one
Unicode document with ``\\n`` line ends; :func:`encode` applies CRLF and the SRT-only BOM.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Literal

FORMATS = ("srt", "vtt", "json", "txt", "md")
Format = Literal["srt", "vtt", "json", "txt", "md"]
Newline = Literal["lf", "crlf"]


@dataclass(frozen=True)
class WordView:
    start: float
    end: float
    text: str
    probability: float | None = None


@dataclass(frozen=True)
class SegmentView:
    start: float
    end: float
    text: str
    speaker: str | None = None
    speaker_confidence: str = "high"
    words: tuple[WordView, ...] = ()


@dataclass(frozen=True)
class EngineView:
    backend: str
    model: str
    device: str


@dataclass(frozen=True)
class DiarizationView:
    backend: str
    model: str
    device: str
    # JSON value as written by the JSON exporter (``dict`` or ``None``).
    memory: Any = None


@dataclass(frozen=True)
class TranscriptView:
    """What the exporters render. ``input_path`` is ``str(Path)`` as the core wrote it."""

    input_path: str
    duration: float
    asr: EngineView
    diarization: DiarizationView | None
    created_at: str
    segments: tuple[SegmentView, ...]


@dataclass(frozen=True)
class RenderOptions:
    """Presentation-only options (same meaning as core ``ExportOptions``)."""

    speaker_labels: Mapping[str, str] = field(default_factory=dict)
    show_speaker: bool = True
    srt_bom: bool = False
    newline: Newline = "lf"


def display_speaker(speaker: str | None, options: RenderOptions) -> str | None:
    """Output-only label renaming: one lookup, never chained."""
    if speaker is None:
        return None
    return options.speaker_labels.get(speaker, speaker)


def display_text(text: str, speaker: str | None, options: RenderOptions) -> str:
    clean_text = text.strip()
    label = display_speaker(speaker, options)
    if options.show_speaker and label:
        return f"{label}: {clean_text}"
    return clean_text


def format_timestamp(seconds: float, decimal_separator: str) -> str:
    """Non-negative timestamp with millisecond rollover."""
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}{decimal_separator}{millis:03d}"


def render_srt(view: TranscriptView, options: RenderOptions) -> str:
    cues = [
        "\n".join(
            [
                str(index),
                f"{format_timestamp(segment.start, ',')} --> {format_timestamp(segment.end, ',')}",
                display_text(segment.text, segment.speaker, options),
            ]
        )
        for index, segment in enumerate(view.segments, start=1)
    ]
    return "\n\n".join(cues) + ("\n" if cues else "")


def render_vtt(view: TranscriptView, options: RenderOptions) -> str:
    cues = [
        "\n".join(
            [
                f"{format_timestamp(segment.start, '.')} --> {format_timestamp(segment.end, '.')}",
                display_text(segment.text, segment.speaker, options),
            ]
        )
        for segment in view.segments
    ]
    body = "\n\n".join(cues)
    return f"WEBVTT\n\n{body}" + ("\n" if body else "")


def render_txt(view: TranscriptView, options: RenderOptions) -> str:
    lines = [display_text(segment.text, segment.speaker, options) for segment in view.segments]
    return "\n".join(lines) + ("\n" if lines else "")


def render_md(view: TranscriptView, options: RenderOptions) -> str:
    asr = view.asr
    diarization = view.diarization
    lines = [
        f"# {PurePath(view.input_path).stem}",
        "",
        "## メタ情報",
        "",
        f"- 入力: `{view.input_path}`",
        f"- 長さ: {view.duration:.3f} 秒",
        f"- 作成日時: {view.created_at}",
        f"- ASR: {asr.backend} / {asr.model} / {asr.device}",
    ]
    if diarization is None:
        lines.append("- 話者分離: 無効")
    else:
        lines.append(
            f"- 話者分離: {diarization.backend} / {diarization.model} / {diarization.device}"
        )
    lines.extend(["", "## 文字起こし", ""])
    for segment in view.segments:
        text = segment.text.strip()
        label = display_speaker(segment.speaker, options)
        if options.show_speaker and label:
            lines.append(f"**{label}**: {text}")
        else:
            lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def json_payload(view: TranscriptView, options: RenderOptions) -> dict[str, Any]:
    """The schema version 1 document. ``show_speaker`` does not apply to JSON (it always
    carries the speaker of every segment); only ``speaker_labels`` renames them."""
    diarization = view.diarization
    segments = [
        {
            "start": segment.start,
            "end": segment.end,
            "speaker": display_speaker(segment.speaker, options),
            "speaker_confidence": segment.speaker_confidence,
            "text": segment.text,
            "words": [
                {
                    "start": word.start,
                    "end": word.end,
                    "text": word.text,
                    "probability": word.probability,
                }
                for word in segment.words
            ],
        }
        for segment in view.segments
    ]
    speakers = list(
        dict.fromkeys(
            speaker
            for segment in view.segments
            if (speaker := display_speaker(segment.speaker, options)) is not None
        )
    )
    return {
        "schema_version": 1,
        "input": {"path": view.input_path, "duration": view.duration},
        "processing": {
            "asr": {
                "backend": view.asr.backend,
                "model": view.asr.model,
                "device": view.asr.device,
            },
            "diarization": (
                None
                if diarization is None
                else {
                    "backend": diarization.backend,
                    "model": diarization.model,
                    "device": diarization.device,
                    "memory": diarization.memory,
                }
            ),
            "created_at": view.created_at,
        },
        "speakers": speakers,
        "segments": segments,
    }


def render_json(view: TranscriptView, options: RenderOptions) -> str:
    return json.dumps(json_payload(view, options), ensure_ascii=False, indent=2) + "\n"


_RENDERERS = {
    "srt": render_srt,
    "vtt": render_vtt,
    "json": render_json,
    "txt": render_txt,
    "md": render_md,
}


def render(format_name: str, view: TranscriptView, options: RenderOptions) -> str:
    try:
        renderer = _RENDERERS[format_name]
    except KeyError:
        raise ValueError(f"unsupported format: {format_name}") from None
    return renderer(view, options)


def apply_newline(content: str, newline: Newline) -> str:
    content = content.replace("\r\n", "\n")
    return content.replace("\n", "\r\n") if newline == "crlf" else content


def encode(format_name: str, content: str, options: RenderOptions) -> bytes:
    """Exactly what ``Exporter.write`` stores: line ending, then UTF-8 (BOM only for SRT)."""
    text = apply_newline(content, options.newline)
    encoding = "utf-8-sig" if format_name == "srt" and options.srt_bom else "utf-8"
    return text.encode(encoding)


def render_bytes(format_name: str, view: TranscriptView, options: RenderOptions) -> bytes:
    return encode(format_name, render(format_name, view, options), options)


# ---- strict reader of the JSON v1 document (E-C) ------------------------------------------


class SourceError(ValueError):
    """A fixed, value-free reason why a JSON document cannot be used as a source."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


#: Structural limits of an accepted source (counted while validating; see e-c-reexport.md §5).
MAX_SEGMENTS = 200_000
MAX_WORDS = 2_000_000
MAX_SPEAKERS = 256
MAX_DEPTH = 32


def _reject_constant(_value: str) -> Any:
    raise SourceError("source_invalid")  # NaN / Infinity are not JSON and never re-exported


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise SourceError("source_invalid")  # duplicate keys: ambiguous document
    return result


def _number(value: Any) -> float:
    # bool is an int subclass and is never a timestamp; finiteness is enforced by the parser.
    if type(value) not in (int, float):
        raise SourceError("source_invalid")
    return value  # type: ignore[no-any-return]  # kept as loaded (int stays int)


def _text(value: Any) -> str:
    if type(value) is not str:
        raise SourceError("source_invalid")
    return value


def _exact(value: Any, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise SourceError("source_invalid")
    return value


def _depth(value: Any, level: int = 0) -> None:
    if level > MAX_DEPTH:
        raise SourceError("source_too_large")
    if isinstance(value, dict):
        for item in value.values():
            _depth(item, level + 1)
    elif isinstance(value, list):
        for item in value:
            _depth(item, level + 1)


def parse_json_v1(data: bytes) -> tuple[TranscriptView, Newline]:
    """The read model of a JSON v1 document written by the JSON exporter, or SourceError.

    Strict: exact keys and types, finite numbers only, no duplicate keys, UTF-8 without BOM.
    Then *lossless*: rendering the view again with no label change must give exactly the same
    bytes (LF or CRLF), so nothing in the file was dropped, reordered or reinterpreted. A file
    that is valid JSON but not what the exporter writes (hand edited, other tool) is refused.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        raise SourceError("source_noncanonical")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceError("source_invalid") from None
    try:
        raw = json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    except SourceError:
        raise
    except (ValueError, RecursionError):
        raise SourceError("source_invalid") from None
    if type(raw) is not dict or type(raw.get("schema_version")) is not int:
        raise SourceError("source_invalid")
    if raw["schema_version"] != 1:
        raise SourceError("source_schema_unsupported")
    top = _exact(raw, {"schema_version", "input", "processing", "speakers", "segments"})
    source = _exact(top["input"], {"path", "duration"})
    processing = _exact(top["processing"], {"asr", "diarization", "created_at"})
    asr = _exact(processing["asr"], {"backend", "model", "device"})
    diarization: DiarizationView | None = None
    if processing["diarization"] is not None:
        item = _exact(processing["diarization"], {"backend", "model", "device", "memory"})
        memory = item["memory"]
        if memory is not None and type(memory) is not dict:
            raise SourceError("source_invalid")
        _depth(memory)
        diarization = DiarizationView(
            _text(item["backend"]), _text(item["model"]), _text(item["device"]), memory
        )
    if type(top["segments"]) is not list or type(top["speakers"]) is not list:
        raise SourceError("source_invalid")
    if len(top["segments"]) > MAX_SEGMENTS:
        raise SourceError("source_too_large")
    words_total = 0
    segments: list[SegmentView] = []
    for entry in top["segments"]:
        segment = _exact(entry, {"start", "end", "speaker", "speaker_confidence", "text", "words"})
        speaker = segment["speaker"]
        if speaker is not None:
            _text(speaker)
        if segment["speaker_confidence"] not in ("high", "low"):
            raise SourceError("source_invalid")
        if type(segment["words"]) is not list:
            raise SourceError("source_invalid")
        words_total += len(segment["words"])
        if words_total > MAX_WORDS:
            raise SourceError("source_too_large")
        words = []
        for word_entry in segment["words"]:
            word = _exact(word_entry, {"start", "end", "text", "probability"})
            probability = word["probability"]
            words.append(
                WordView(
                    _number(word["start"]),
                    _number(word["end"]),
                    _text(word["text"]),
                    None if probability is None else _number(probability),
                )
            )
        segments.append(
            SegmentView(
                start=_number(segment["start"]),
                end=_number(segment["end"]),
                text=_text(segment["text"]),
                speaker=speaker,
                speaker_confidence=segment["speaker_confidence"],
                words=tuple(words),
            )
        )
    view = TranscriptView(
        input_path=_text(source["path"]),
        duration=_number(source["duration"]),
        asr=EngineView(_text(asr["backend"]), _text(asr["model"]), _text(asr["device"])),
        diarization=diarization,
        created_at=_text(processing["created_at"]),
        segments=tuple(segments),
    )
    if len(speakers(view)) > MAX_SPEAKERS:
        raise SourceError("source_too_large")
    canonical = render_json(view, RenderOptions())
    for newline in ("lf", "crlf"):
        if apply_newline(canonical, newline).encode("utf-8") == data:
            return view, newline
    raise SourceError("source_noncanonical")


def speakers(view: TranscriptView) -> list[str]:
    """Displayed speaker labels in order of first appearance (``None`` excluded)."""
    return list(dict.fromkeys(s.speaker for s in view.segments if s.speaker is not None))
