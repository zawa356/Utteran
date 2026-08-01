"""Public exception hierarchy and CLI exit-code mapping."""


class UtteranError(Exception):
    """Base class for expected, user-actionable failures."""

    exit_code = 1


class ConfigurationError(UtteranError):
    """Invalid or missing configuration."""

    exit_code = 2


class HuggingFaceTokenMissingError(ConfigurationError):
    """Raised when the pyannote Hugging Face token is not configured."""

    def __init__(self) -> None:
        super().__init__(
            "Hugging Face トークンが未設定です。"
            "https://huggingface.co/settings/tokens で読み取りトークンを取得し、"
            "HF_TOKEN 環境変数、.env、または OS キーリングに設定してください。"
            "あわせて https://huggingface.co/pyannote/speaker-diarization-community-1 "
            "でモデル利用条件に同意してください。"
        )


class HuggingFaceAuthenticationError(ConfigurationError):
    """Raised when a configured Hugging Face token is rejected."""


class ModelAgreementError(ConfigurationError):
    """Raised when gated model terms have not been accepted."""


class DependencyError(UtteranError):
    """A required executable or Python backend is unavailable."""

    exit_code = 3


class FfmpegNotFoundError(DependencyError):
    """Raised when ffmpeg cannot be found."""

    def __init__(self) -> None:
        super().__init__(
            "ffmpeg が見つかりません。ffmpeg をインストールして PATH に追加するか、"
            "config.toml の [ffmpeg].path に実行ファイルを指定してください。"
            "Phase 2 以降では setup.ps1 からも導入できます。"
        )


class BackendUnavailableError(DependencyError):
    """Raised when a selected inference backend is not installed."""


class ModelNotFoundError(DependencyError):
    """Raised when a requested model is unavailable locally."""


class VramExhaustedError(DependencyError):
    """Raised when GPU memory is insufficient."""


class InputError(UtteranError):
    """An input path or media stream is invalid."""

    exit_code = 4


class InputFileNotFoundError(InputError):
    """Raised when the requested input file does not exist."""


class UnsupportedInputError(InputError):
    """Raised when the input is not a supported regular media file."""


class AudioDecodeError(InputError):
    """Raised when ffmpeg cannot decode the input."""


class JobError(UtteranError):
    """A job manifest, lock, or lifecycle operation failed."""


class JobLockedError(JobError):
    """Raised when another live process owns a job directory."""


class JobNotFoundError(JobError):
    """Raised when a requested job does not exist."""


class JobManifestError(JobError):
    """Raised when a manifest cannot safely identify its job."""


class CancelledError(UtteranError):
    """Raised on cooperative or keyboard cancellation."""

    exit_code = 130

    def __init__(self) -> None:
        super().__init__("処理はユーザーにより中断されました。")
