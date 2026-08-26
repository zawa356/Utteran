"""faster-whisper ASR backend for CPU and NVIDIA CUDA."""

from __future__ import annotations

import gc
import importlib.util
import logging
import time
from pathlib import Path
from typing import Any, ClassVar

from utteran.asr.base import ASRBackend
from utteran.devices import (
    register_cuda_dll_directories,
    select_faster_whisper_device,
    suppress_torch_import,
)
from utteran.errors import (
    BackendUnavailableError,
    CancelledError,
    ModelNotFoundError,
    VramExhaustedError,
)
from utteran.logging import structured_event
from utteran.models.manager import find_runtime_model
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


class FasterWhisperBackend(ASRBackend):
    """Convert faster-whisper values at the backend boundary."""

    name: ClassVar[str] = "faster-whisper"

    def __init__(self) -> None:
        self._model: Any | None = None
        self._model_id = ""
        self._device = ""

    @classmethod
    def is_available(cls) -> bool:
        """Return false instead of leaking an optional-import exception."""
        return importlib.util.find_spec("faster_whisper") is not None

    @classmethod
    def available_devices(cls) -> list[DeviceInfo]:
        """Report CPU and each CUDA device visible to CTranslate2."""
        devices = [DeviceInfo(id="cpu", kind="cpu", name="CPU")]
        if not cls.is_available():
            return []
        try:
            register_cuda_dll_directories()
            with suppress_torch_import():
                import ctranslate2

            for index in range(ctranslate2.get_cuda_device_count()):
                devices.append(
                    DeviceInfo(id=f"cuda:{index}", kind="cuda", name=f"NVIDIA CUDA {index}")
                )
        except Exception:
            pass
        return devices

    def load(self, model_id: str, device: str, compute_type: str) -> None:
        """Load a locally available CTranslate2 model without implicit downloads."""
        if not self.is_available():
            raise BackendUnavailableError(
                "faster-whisper が導入されていません。`uv sync` を実行してください。"
            )
        register_cuda_dll_directories()
        import_started = time.perf_counter()
        with suppress_torch_import() as suppressed_torch:
            from faster_whisper import WhisperModel
        import_duration = time.perf_counter() - import_started
        structured_event(
            "asr_ctranslate2_import_completed",
            backend=self.name,
            duration_seconds=round(import_duration, 3),
            torch_import_suppressed=suppressed_torch,
        )

        selection = select_faster_whisper_device(device, compute_type)
        runtime_model = find_runtime_model(self.name, model_id)
        model_source = str(runtime_model) if runtime_model is not None else model_id
        selected_device = selection.device
        device_index = selection.device_index
        selected_compute_type = selection.compute_type
        cpu_threads = 0
        num_workers = 1
        if selection.note:
            logging.getLogger(__name__).info(selection.note)
        load_started = time.perf_counter()
        try:
            self._model = WhisperModel(
                model_source,
                device=selected_device,
                device_index=device_index,
                compute_type=selected_compute_type,
                cpu_threads=cpu_threads,
                num_workers=num_workers,
                local_files_only=not Path(model_source).exists(),
            )
        except Exception as exc:
            if device == "auto" and selected_device == "cuda" and not _is_model_error(exc):
                logging.getLogger(__name__).warning(
                    "CUDA でモデルを初期化できないため CPU へフォールバックします。"
                )
                fallback = select_faster_whisper_device("cpu", compute_type)
                selected_device = fallback.device
                device_index = fallback.device_index
                selected_compute_type = fallback.compute_type
                try:
                    self._model = WhisperModel(
                        model_source,
                        device=selected_device,
                        device_index=device_index,
                        compute_type=selected_compute_type,
                        cpu_threads=cpu_threads,
                        num_workers=num_workers,
                        local_files_only=not Path(model_source).exists(),
                    )
                except Exception as fallback_error:
                    _raise_load_error(model_id, selected_device, fallback_error)
            else:
                _raise_load_error(model_id, selected_device, exc)
        load_duration = time.perf_counter() - load_started
        self._model_id = model_id
        self._device = f"cuda:{device_index}" if selected_device == "cuda" else selected_device
        structured_event(
            "asr_backend_resolved",
            backend=self.name,
            model=model_id,
            device=self._device,
            compute_type=selected_compute_type,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
            load_duration_seconds=round(load_duration, 3),
            fallback_allowed=device == "auto",
        )

    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        """Run ASR and eagerly convert the lazy generator to common dataclasses."""
        if self._model is None:
            raise BackendUnavailableError("faster-whisper モデルが読み込まれていません。")
        if cancel is not None:
            cancel.raise_if_cancelled()
        if progress is not None:
            progress(ProgressEvent("asr", 0.0, None, "文字起こしを開始します"))

        transcribe_started = time.perf_counter()
        try:
            backend_segments, info = self._model.transcribe(
                str(audio_path),
                language=options.language,
                initial_prompt=options.initial_prompt,
                vad_filter=options.vad_filter,
                beam_size=options.beam_size,
                condition_on_previous_text=options.condition_on_previous_text,
                word_timestamps=options.word_timestamps,
            )
            duration = float(info.duration)
            segments: list[Segment] = []
            for backend_segment in backend_segments:
                if cancel is not None:
                    cancel.raise_if_cancelled()
                words = [
                    Word(
                        start=float(word.start),
                        end=float(word.end),
                        text=str(word.word),
                        probability=(
                            None
                            if getattr(word, "probability", None) is None
                            else float(word.probability)
                        ),
                    )
                    for word in (backend_segment.words or [])
                ]
                segment = Segment(
                    start=float(backend_segment.start),
                    end=float(backend_segment.end),
                    text=str(backend_segment.text),
                    words=words,
                )
                segments.append(segment)
                if progress is not None:
                    progress(
                        ProgressEvent(
                            "asr",
                            min(segment.end, duration),
                            duration,
                            "文字起こし中",
                        )
                    )
        except CancelledError:
            raise
        except Exception as exc:
            _raise_inference_error(exc)

        transcribe_duration = time.perf_counter() - transcribe_started
        structured_event(
            "asr_transcribe_completed",
            backend=self.name,
            device=self._device,
            audio_duration_seconds=round(duration, 3),
            transcribe_duration_seconds=round(transcribe_duration, 3),
            realtime_factor=(
                round(duration / transcribe_duration, 3) if transcribe_duration else None
            ),
            segment_count=len(segments),
            vad_filter=options.vad_filter,
        )
        if progress is not None:
            progress(ProgressEvent("asr", duration, duration, "文字起こしが完了しました"))
        return TranscriptionResult(
            segments=segments,
            language=str(info.language),
            duration=duration,
            backend=self.name,
            model_id=self._model_id,
            device=self._device,
        )

    def unload(self) -> None:
        """Release the CTranslate2 model reference."""
        self._model = None
        gc.collect()


def _raise_load_error(model_id: str, device: str, error: Exception) -> None:
    """Translate expected model, CUDA library, and memory failures."""
    detail = str(error).casefold()
    if _is_model_error(error):
        raise ModelNotFoundError(
            f"ASR モデル '{model_id}' をローカルで読み込めません。"
            "モデルは暗黙にダウンロードしません。"
            "`utteran models download faster-whisper:<モデルID>` で取得するか、"
            "ローカルモデルパスを指定してください。"
        ) from None
    if "out of memory" in detail or "cuda_error_out_of_memory" in detail:
        raise VramExhaustedError(
            "モデル読み込み中に VRAM が不足しました。CPU または小さいモデルを指定してください。"
        ) from None
    if device == "cuda":
        raise BackendUnavailableError(
            "CUDA バックエンドを初期化できません。CUDA 12、cuDNN 9、cuBLAS と "
            "NVIDIA ドライバーを確認してください。"
        ) from None
    raise BackendUnavailableError(
        "faster-whisper モデルを初期化できません。"
        "モデル形式、compute_type、実行デバイスを確認してください。"
    ) from None


def _is_model_error(error: Exception) -> bool:
    """Identify failures caused by an absent or incomplete local model."""
    detail = str(error).casefold()
    return any(
        marker in detail
        for marker in (
            "appropriate cached snapshot",
            "invalid model size",
            "local_files_only",
            "not cached",
            "not found",
            "unable to open file",
        )
    )


def _raise_inference_error(error: Exception) -> None:
    """Translate expected runtime failures without leaking backend tracebacks."""
    detail = str(error).casefold()
    if "out of memory" in detail or "cuda_error_out_of_memory" in detail:
        raise VramExhaustedError(
            "文字起こし中に VRAM が不足しました。CPU または小さいモデルを指定してください。"
        ) from None
    raise BackendUnavailableError(
        "faster-whisper の推論に失敗しました。入力音声、モデル、実行デバイスを確認してください。"
    ) from None
