"""ASR backend selection isolated from the pipeline."""

from utteran.asr.base import ASRBackend
from utteran.errors import BackendUnavailableError


def create_asr_backend(name: str) -> ASRBackend:
    """Instantiate a Phase 1 ASR backend by stable registry name."""
    if name in {"auto", "faster-whisper"}:
        from utteran.asr.faster_whisper import FasterWhisperBackend

        return FasterWhisperBackend()
    raise BackendUnavailableError(f"未対応の ASR バックエンドです: {name}")
