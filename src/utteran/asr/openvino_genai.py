"""OpenVINO GenAI Whisper backend for Intel CPU, GPU, and NPU."""

from __future__ import annotations

import importlib.util
import logging
import wave
from pathlib import Path
from typing import Any, ClassVar

from platformdirs import user_cache_dir

from utteran.asr.base import ASRBackend
from utteran.errors import BackendUnavailableError, ModelNotFoundError
from utteran.logging import structured_event
from utteran.models.catalog import ModelEntry, get_model
from utteran.models.manager import ModelManager
from utteran.types import (
    ASROptions,
    CancelToken,
    DeviceInfo,
    ProgressCallback,
    ProgressEvent,
    Segment,
    TranscriptionResult,
    Word,
)

DEGRADED_WORD_CHARACTER_THRESHOLD = 30
NPU_RECOMMENDATION_REASON = (
    "非推奨: 初回ロード約306秒、コンパイルキャッシュ約2.06 GiB。"
    "キャッシュ後は約4.8秒ですが、現時点ではGPUの方が高速です。"
)


def resolve_cache_dir() -> Path:
    """Keep OpenVINO compiled blobs inside utteran's managed cache tree."""
    return Path(user_cache_dir("utteran")) / "openvino-genai-compiled"


def cache_usage_bytes(path: Path | None = None) -> int:
    """Return the readable size of the managed compiled-model cache."""
    root = path or resolve_cache_dir()
    total = 0
    if not root.is_dir():
        return 0
    for candidate in root.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


class OpenVINOGenAIBackend(ASRBackend):
    """Run a locally downloaded OpenVINO GenAI Whisper pipeline."""

    name: ClassVar[str] = "openvino-genai"

    def __init__(self, *, diarization_enabled: bool = False) -> None:
        self._pipeline: Any | None = None
        self._entry: ModelEntry | None = None
        self._device = ""
        self._diarization_enabled = diarization_enabled

    @classmethod
    def is_available(cls) -> bool:
        try:
            if importlib.util.find_spec("openvino_genai") is None:
                return False
            if importlib.util.find_spec("openvino") is None:
                return False
            import openvino  # noqa: F401
            import openvino_genai  # noqa: F401
        except Exception:
            return False
        return True

    @classmethod
    def available_devices(cls) -> list[DeviceInfo]:
        if not cls.is_available():
            return []
        try:
            import openvino

            available = {
                str(item).split(".", 1)[0].upper() for item in openvino.Core().available_devices
            }
        except Exception:
            return []
        devices: list[DeviceInfo] = []
        for identifier in ("CPU", "GPU", "NPU"):
            if identifier not in available:
                continue
            devices.append(
                DeviceInfo(
                    id=identifier.lower(),
                    kind="cpu" if identifier == "CPU" else "other",
                    name=f"OpenVINO {identifier}",
                    recommended=identifier != "NPU",
                    recommendation_reason=(
                        NPU_RECOMMENDATION_REASON if identifier == "NPU" else None
                    ),
                )
            )
        return devices

    def load(self, model_id: str, device: str, compute_type: str) -> None:
        del compute_type
        if not self.is_available():
            raise BackendUnavailableError(
                "OpenVINO GenAIが未導入です。intelプロファイルを再構築してください。"
            )
        try:
            entry = get_model(model_id, backend=self.name)
        except Exception:
            raise ModelNotFoundError(
                f"OpenVINO GenAIモデルがカタログにありません: {model_id}"
            ) from None
        directory, _managed = ModelManager().find_installed(entry)
        if directory is None:
            raise ModelNotFoundError(
                f"モデル未導入: {entry.key}。"
                f"`utteran models download {entry.key}`を実行してください。"
            )
        selected = device.casefold()
        if selected == "auto":
            selected = (
                "gpu" if any(item.id == "gpu" for item in self.available_devices()) else "cpu"
            )
        available = {item.id for item in self.available_devices()}
        if selected not in available:
            raise BackendUnavailableError(
                f"OpenVINO GenAIデバイスを利用できません: {selected}。"
                f"利用可能: {', '.join(sorted(available)) or 'なし'}"
            )
        cache_dir = resolve_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        properties: dict[str, object] = {
            "word_timestamps": True,
            "CACHE_DIR": str(cache_dir),
        }
        if selected == "npu":
            properties["STATIC_PIPELINE"] = True
        try:
            import openvino_genai

            self._pipeline = openvino_genai.WhisperPipeline(
                directory, selected.upper(), **properties
            )
        except Exception as exc:
            raise BackendUnavailableError(
                f"OpenVINO GenAIの初期化に失敗しました ({selected}): {type(exc).__name__}"
            ) from None
        self._entry = entry
        self._device = selected
        structured_event(
            "asr_backend_resolved",
            backend=self.name,
            device=selected,
            model=entry.key,
            cache_dir=str(cache_dir),
            fallback_allowed=False,
        )

    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        if self._pipeline is None or self._entry is None:
            raise BackendUnavailableError("OpenVINO GenAIバックエンドがloadされていません。")
        if cancel is not None:
            cancel.raise_if_cancelled()
        if progress is not None:
            progress(ProgressEvent("asr", 0.0, 1.0, "OpenVINO GenAI文字起こし中"))
        samples, duration = _read_normalized_wav(audio_path)
        generation: dict[str, object] = {
            "return_timestamps": True,
            "word_timestamps": options.word_timestamps,
        }
        if options.language:
            generation["language"] = f"<|{options.language.casefold()}|>"
        if options.initial_prompt:
            generation["initial_prompt"] = options.initial_prompt
        try:
            result = self._pipeline.generate(samples, **generation)
        except Exception as exc:
            raise BackendUnavailableError(
                f"OpenVINO GenAIの文字起こしに失敗しました: {type(exc).__name__}"
            ) from None
        if cancel is not None:
            cancel.raise_if_cancelled()
        converted = _convert_result(result, duration, self._entry, self._device)
        statistics = degraded_word_statistics(converted.segments)
        structured_event(
            "openvino_genai_word_timestamps",
            word_count=statistics["word_count"],
            degraded_count=statistics["degraded_count"],
            fused_segment_count=statistics["fused_segment_count"],
            average_degraded_characters=statistics["average_degraded_characters"],
            discarded_count=statistics["discarded_count"],
        )
        if statistics["degraded_count"] and self._diarization_enabled:
            message = (
                "OpenVINO GenAIの単語タイムスタンプが粗く、話者割当の精度を保証できません。"
                "英語でも発生する場合はwhisper.cppを使用してください。"
            )
            logging.getLogger(__name__).warning(message)
            if progress is not None:
                progress(
                    ProgressEvent(
                        "asr", 1.0, 1.0, message, event_type="warning", details=statistics
                    )
                )
        if progress is not None:
            progress(ProgressEvent("asr", 1.0, 1.0, "OpenVINO GenAI文字起こし完了"))
        return converted

    def unload(self) -> None:
        self._pipeline = None
        self._entry = None
        self._device = ""


def _read_normalized_wav(path: Path) -> tuple[list[float], float]:
    try:
        with wave.open(str(path), "rb") as reader:
            if (
                reader.getnchannels() != 1
                or reader.getsampwidth() != 2
                or reader.getframerate() != 16_000
            ):
                raise BackendUnavailableError(
                    "OpenVINO GenAIには16kHz/mono/PCM16の正規化済みWAVが必要です。"
                )
            frames = reader.readframes(reader.getnframes())
            duration = reader.getnframes() / reader.getframerate()
    except (OSError, wave.Error, ZeroDivisionError) as exc:
        raise BackendUnavailableError(f"正規化WAVを読み込めません: {type(exc).__name__}") from None
    values = memoryview(frames).cast("h")
    return [sample / 32768.0 for sample in values], duration


def _convert_result(
    result: Any, duration: float, entry: ModelEntry, device: str
) -> TranscriptionResult:
    raw_words = list(getattr(result, "words", None) or [])
    words = [
        Word(float(item.start_ts), float(item.end_ts), str(item.word), None) for item in raw_words
    ]
    raw_chunks = list(getattr(result, "chunks", None) or [])
    segments: list[Segment] = []
    for chunk in raw_chunks:
        start, end = float(chunk.start_ts), float(chunk.end_ts)
        selected_words = [word for word in words if start <= (word.start + word.end) / 2 <= end]
        segments.append(Segment(start, end, str(chunk.text), selected_words))
    if not segments:
        texts = list(getattr(result, "texts", None) or [])
        text = str(texts[0]) if texts else ""
        end = max((word.end for word in words), default=duration)
        segments = [Segment(0.0, end, text, words)] if text or words else []
    assigned = {id(word) for segment in segments for word in segment.words}
    for word in words:
        if id(word) not in assigned and segments:
            target = min(
                segments,
                key=lambda segment: abs(
                    (segment.start + segment.end) / 2 - (word.start + word.end) / 2
                ),
            )
            target.words.append(word)
    language = (
        str(getattr(result, "language", "") or "unknown").removeprefix("<|").removesuffix("|>")
    )
    return TranscriptionResult(
        segments, language, duration, OpenVINOGenAIBackend.name, entry.model_id, device
    )


def degraded_word_statistics(segments: list[Segment]) -> dict[str, int | float]:
    """Report fused/oversized words without dropping recognized data or text logging."""
    lengths: list[int] = []
    fused = 0
    total = 0
    for segment in segments:
        total += len(segment.words)
        if (
            len(segment.words) == 1
            and len(segment.text.strip()) >= DEGRADED_WORD_CHARACTER_THRESHOLD
        ):
            fused += 1
        for word in segment.words:
            length = len(word.text.strip())
            if length >= DEGRADED_WORD_CHARACTER_THRESHOLD:
                lengths.append(length)
    return {
        "word_count": total,
        "degraded_count": len(lengths),
        "fused_segment_count": fused,
        "average_degraded_characters": (sum(lengths) / len(lengths) if lengths else 0.0),
        "discarded_count": 0,
    }
