"""ASR backend selection isolated from the pipeline."""

from utteran.asr.base import ASRBackend
from utteran.config import Config, WhisperCppConfig
from utteran.errors import BackendUnavailableError, ConfigurationError

NON_SPACE_LANGUAGES = frozenset({"ja", "zh", "th", "lo", "my", "yue"})


def validate_asr_configuration(config: Config) -> None:
    """Reject GenAI combinations whose word timing cannot support diarization."""
    if config.asr.backend != "openvino-genai" or not config.diarization.enabled:
        return
    language = config.asr.language.casefold() if config.asr.language else None
    if language is None or language in NON_SPACE_LANGUAGES:
        label = "auto (未指定)" if language is None else language
        raise ConfigurationError(
            f"OpenVINO GenAIは言語{label}と話者分離を併用できません。"
            "非スペース言語では単語タイムスタンプを取得できず、話者割当の精度を保証できないためです。"
            "`--asr-backend whisper-cpp`を指定するか、`--no-diarization`を使用してください。"
        )


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
            # Keep ``auto`` intact: WhisperCppBackend owns the policy that only an
            # automatic variant may fall back after native initialization fails.
            return WhisperCppBackend(settings)
        from utteran.asr.faster_whisper import FasterWhisperBackend

        return FasterWhisperBackend()
    if name == "faster-whisper":
        from utteran.asr.faster_whisper import FasterWhisperBackend

        return FasterWhisperBackend()
    if name == "whisper-cpp":
        from utteran.asr.whisper_cpp import WhisperCppBackend

        return WhisperCppBackend(None if config is None else config.asr.whisper_cpp)
    if name == "openvino-genai":
        if config is not None:
            validate_asr_configuration(config)
        from utteran.asr.openvino_genai import OpenVINOGenAIBackend

        return OpenVINOGenAIBackend(
            diarization_enabled=False if config is None else config.diarization.enabled
        )
    raise BackendUnavailableError(f"未対応の ASR バックエンドです: {name}")
