"""ASR backend selection isolated from the pipeline."""

from utteran.asr.base import ASRBackend
from utteran.config import Config
from utteran.errors import BackendUnavailableError


def create_asr_backend(name: str, config: Config | None = None) -> ASRBackend:
    """Instantiate a Phase 1 ASR backend by stable registry name."""
    if name in {"auto", "faster-whisper"}:
        from utteran.asr.faster_whisper import FasterWhisperBackend

        return FasterWhisperBackend()
    if name == "whisper-cpp":
        from utteran.asr.whisper_cpp import WhisperCppBackend

        return WhisperCppBackend(None if config is None else config.asr.whisper_cpp)
    raise BackendUnavailableError(f"未対応の ASR バックエンドです: {name}")
