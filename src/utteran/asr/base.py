"""Abstract interface for automatic speech recognition backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from utteran.types import ASROptions, CancelToken, DeviceInfo, ProgressCallback, TranscriptionResult


class ASRBackend(ABC):
    """Stable interface implemented by every ASR backend."""

    name: ClassVar[str]

    @classmethod
    @abstractmethod
    def is_available(cls) -> bool:
        """Return whether the backend dependency can be imported."""

    @classmethod
    @abstractmethod
    def available_devices(cls) -> list[DeviceInfo]:
        """Return devices usable by this backend."""

    @abstractmethod
    def load(self, model_id: str, device: str, compute_type: str) -> None:
        """Load a model from local storage."""

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        """Transcribe normalized audio into backend-neutral models."""

    @abstractmethod
    def unload(self) -> None:
        """Release model resources."""
