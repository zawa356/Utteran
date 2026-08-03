"""ASR backend selection isolated from the pipeline."""

from utteran.asr.base import ASRBackend
from utteran.config import Config, WhisperCppConfig
from utteran.errors import BackendUnavailableError


def create_asr_backend(name: str, config: Config | None = None) -> ASRBackend:
    """Instantiate a Phase 1 ASR backend by stable registry name."""
    if name == "auto":
        from utteran.devices import detect_devices

        report = detect_devices(
            None if config is None else config.ffmpeg.path,
            venv_dir=None if config is None else config.general.venv_dir,
            native_dir=None if config is None else config.general.native_dir,
        )
        if report.auto_selection.asr_backend == "whisper-cpp":
            from utteran.asr.whisper_cpp import WhisperCppBackend

            settings = WhisperCppConfig() if config is None else config.asr.whisper_cpp
            return WhisperCppBackend(
                settings.model_copy(update={"variant": report.auto_selection.asr_device}),
                allow_fallback=True,
            )
        from utteran.asr.faster_whisper import FasterWhisperBackend

        return FasterWhisperBackend()
    if name == "faster-whisper":
        from utteran.asr.faster_whisper import FasterWhisperBackend

        return FasterWhisperBackend()
    if name == "whisper-cpp":
        from utteran.asr.whisper_cpp import WhisperCppBackend

        return WhisperCppBackend(None if config is None else config.asr.whisper_cpp)
    raise BackendUnavailableError(f"未対応の ASR バックエンドです: {name}")
