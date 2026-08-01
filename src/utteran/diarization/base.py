"""Abstract interface for speaker diarization backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from utteran.types import (
    CancelToken,
    DeviceInfo,
    DiarizationOptions,
    DiarizationResult,
    ProgressCallback,
)


class DiarizationBackend(ABC):
    """Stable interface implemented by every diarization backend."""

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
    def load(self, model_id: str, device: str) -> None:
        """Load a model from local storage."""

    @abstractmethod
    def diarize(
        self,
        audio_path: Path,
        options: DiarizationOptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> DiarizationResult:
        """Diarize normalized audio into backend-neutral models."""

    @abstractmethod
    def unload(self) -> None:
        """Release model resources."""
