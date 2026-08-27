"""Backend-neutral data models and long-running operation interfaces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Literal, TypeAlias

DeviceKind: TypeAlias = Literal["cpu", "cuda", "xpu", "other"]


@dataclass(frozen=True)
class Word:
    """One recognized word with timestamps in seconds."""

    start: float
    end: float
    text: str
    probability: float | None = None


@dataclass
class Segment:
    """One transcript segment, optionally attributed to a speaker."""

    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str | None = None


@dataclass
class TranscriptionResult:
    """Backend-neutral ASR result."""

    segments: list[Segment]
    language: str
    duration: float
    backend: str
    model_id: str
    device: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptionResult:
        """Restore a result previously produced by :meth:`to_dict`."""
        segments = [
            Segment(
                start=float(segment["start"]),
                end=float(segment["end"]),
                text=str(segment["text"]),
                words=[
                    Word(
                        start=float(word["start"]),
                        end=float(word["end"]),
                        text=str(word["text"]),
                        probability=(
                            None if word.get("probability") is None else float(word["probability"])
                        ),
                    )
                    for word in segment.get("words", [])
                ],
                speaker=(None if segment.get("speaker") is None else str(segment["speaker"])),
            )
            for segment in data["segments"]
        ]
        return cls(
            segments=segments,
            language=str(data["language"]),
            duration=float(data["duration"]),
            backend=str(data["backend"]),
            model_id=str(data["model_id"]),
            device=str(data["device"]),
        )


@dataclass(frozen=True)
class SpeakerTurn:
    """A time range attributed to one internal speaker label."""

    start: float
    end: float
    speaker: str


@dataclass
class DiarizationResult:
    """Backend-neutral speaker diarization result."""

    turns: list[SpeakerTurn]
    exclusive_turns: list[SpeakerTurn] | None
    num_speakers: int
    backend: str
    model_id: str
    device: str
    memory: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiarizationResult:
        """Restore a result previously produced by :meth:`to_dict`."""
        exclusive_data = data.get("exclusive_turns")
        return cls(
            turns=[SpeakerTurn(**turn) for turn in data["turns"]],
            exclusive_turns=(
                None if exclusive_data is None else [SpeakerTurn(**turn) for turn in exclusive_data]
            ),
            num_speakers=int(data["num_speakers"]),
            backend=str(data["backend"]),
            model_id=str(data["model_id"]),
            device=str(data["device"]),
            memory=(None if data.get("memory") is None else dict(data["memory"])),
        )


@dataclass(frozen=True)
class DeviceInfo:
    """A device exposed by an inference backend."""

    id: str
    kind: DeviceKind
    name: str
    memory_bytes: int | None = None


@dataclass(frozen=True)
class ASROptions:
    """Backend-neutral ASR options."""

    language: str | None = "ja"
    initial_prompt: str | None = None
    vad_filter: bool = True
    beam_size: int = 5
    condition_on_previous_text: bool = False
    word_timestamps: bool = True


@dataclass(frozen=True)
class DiarizationOptions:
    """Backend-neutral speaker-count constraints."""

    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None


@dataclass(frozen=True)
class AlignmentOptions:
    """Thresholds controlling transcript/speaker reconciliation."""

    # Retained as accepted compatibility settings; Viterbi supersedes nearest-distance
    # assignment and word-count island absorption.
    max_nearest_distance: float = 2.0
    min_segment_duration: float = 0.3
    min_segment_words: int = 2
    speaker_switch_penalty: float = 0.75
    silence_switch_threshold: float = 0.3
    min_clear_turn_duration: float = 0.5
    max_same_speaker_bridge_gap: float = 0.3
    unknown_emission_score: float = 0.35
    min_unknown_duration: float = 1.0
    min_unknown_characters: int = 2
    max_unsupported_fragment_duration: float = 0.5
    max_unsupported_fragment_characters: int = 3
    min_fragment_speaker_overlap: float = 0.05
    merge_gap: float = 0.5
    renumber_speakers: bool = True


@dataclass(frozen=True)
class ExportOptions:
    """Backend-neutral presentation options shared by exporters."""

    speaker_labels: dict[str, str] = field(default_factory=dict)
    show_speaker: bool = True
    srt_bom: bool = False
    newline: Literal["lf", "crlf"] = "lf"


@dataclass(frozen=True)
class ProgressEvent:
    """Progress notification emitted by a long-running operation."""

    stage: str
    completed: float
    total: float | None = None
    message: str | None = None
    event_type: str = "progress"
    skipped: bool = False
    duration_seconds: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


ProgressCallback: TypeAlias = Callable[[ProgressEvent], None]


class CancelToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        """Request cancellation."""
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """Raise the public cancellation exception when requested."""
        if self.is_cancelled:
            from utteran.errors import CancelledError

            raise CancelledError


@dataclass(frozen=True)
class PipelineResult:
    """Serializable result passed from the pipeline to exporters."""

    input_path: Path
    transcription: TranscriptionResult
    diarization: DiarizationResult | None
    segments: list[Segment]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Return a backend-neutral representation for job persistence."""
        return {
            "input_path": str(self.input_path),
            "transcription": self.transcription.to_dict(),
            "diarization": (None if self.diarization is None else self.diarization.to_dict()),
            "segments": [asdict(segment) for segment in self.segments],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineResult:
        """Restore a pipeline result from a versioned intermediate file."""
        diarization_data = data.get("diarization")
        return cls(
            input_path=Path(str(data["input_path"])),
            transcription=TranscriptionResult.from_dict(data["transcription"]),
            diarization=(
                None if diarization_data is None else DiarizationResult.from_dict(diarization_data)
            ),
            segments=[_segment_from_dict(segment) for segment in data["segments"]],
            created_at=str(data["created_at"]),
        )


@dataclass(frozen=True)
class PipelineOutcome:
    """A completed pipeline result and its exported file paths."""

    result: PipelineResult
    output_paths: list[Path]
    job_id: str | None = None
    executed_stages: tuple[str, ...] = ()
    stage_durations: dict[str, float] = field(default_factory=dict)


def _segment_from_dict(data: dict[str, Any]) -> Segment:
    """Restore one common segment from persisted JSON data."""
    return Segment(
        start=float(data["start"]),
        end=float(data["end"]),
        text=str(data["text"]),
        words=[
            Word(
                start=float(word["start"]),
                end=float(word["end"]),
                text=str(word["text"]),
                probability=(
                    None if word.get("probability") is None else float(word["probability"])
                ),
            )
            for word in data.get("words", [])
        ],
        speaker=None if data.get("speaker") is None else str(data["speaker"]),
    )
