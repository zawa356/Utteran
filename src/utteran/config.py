"""Application settings and secure Hugging Face token providers."""

from __future__ import annotations

import os
import tomllib
import warnings
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from platformdirs import user_cache_dir, user_config_dir
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    SettingsConfigDict,
)

from utteran.errors import ConfigurationError

OutputFormat = Literal["srt", "vtt", "json", "txt", "md"]
LogLevel = Literal["debug", "info", "warning", "error", "critical"]

CONFIG_TEMPLATE = """[general]
output_dir = "./output"
job_dir = ""
log_level = "info"
venv_dir = ""
native_dir = ""
default_profile = ""

[asr]
backend = "auto"
model = "large-v3-turbo"
device = "auto"
compute_type = "auto"
language = "ja"
vad_filter = true
condition_on_previous_text = false
beam_size = 5
initial_prompt = ""
word_timestamps = "auto"

[asr.whisper_cpp]
variant = "auto"
dtw = "auto"
threads = 0
no_context = true
vad = false
vad_model = ""
vad_threshold = 0.5
entropy_threshold = 2.4
logprob_threshold = -1.0
no_speech_threshold = 0.6
temperature = 0.0
temperature_increment = 0.2
repetition_limit = 10

[diarization]
enabled = true
backend = "pyannote"
model = "pyannote/speaker-diarization-community-1"
device = "auto"
num_speakers = 0
min_speakers = 0
max_speakers = 0
memory_guard = "auto"
memory_safety_margin = 0.0

[output]
formats = ["srt", "json", "md"]
srt_bom = false
newline = "lf"
show_speaker = true
speaker_labels = {}

[ffmpeg]
path = ""

[alignment]
max_nearest_distance = 2.0
min_segment_duration = 0.3
min_segment_words = 2
merge_gap = 0.5
renumber_speakers = true
"""


def _default_output_formats() -> list[OutputFormat]:
    """Return a fresh default output format list."""
    return ["srt", "json", "md"]


class GeneralConfig(BaseModel):
    """General paths and logging settings."""

    output_dir: Path = Path("./output")
    job_dir: Path | None = None
    log_level: LogLevel = "info"
    venv_dir: Path | None = None
    native_dir: Path | None = None
    default_profile: str | None = None

    @field_validator("job_dir", "venv_dir", "native_dir", mode="before")
    @classmethod
    def empty_path_uses_default(cls, value: object) -> object:
        """Translate the documented empty-string sentinel to None."""
        return None if value == "" else value

    @field_validator("default_profile", mode="before")
    @classmethod
    def empty_default_profile_is_unset(cls, value: object) -> object:
        """Translate the documented empty-string sentinel to None."""
        return None if value == "" else value


class WhisperCppConfig(BaseModel):
    """whisper.cpp-specific process and alignment settings."""

    variant: Literal["auto", "cpu", "openvino", "vulkan", "openvino_vulkan"] = "auto"
    dtw: str = "auto"
    threads: int = Field(default=0, ge=0)
    no_context: bool = True
    vad: bool = False
    vad_model: Path | None = None
    vad_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    entropy_threshold: float = 2.4
    logprob_threshold: float = -1.0
    no_speech_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    temperature_increment: float = Field(default=0.2, ge=0.0, le=1.0)
    repetition_limit: int = Field(default=10, ge=0)

    @field_validator("vad_model", mode="before")
    @classmethod
    def empty_vad_model_is_unset(cls, value: object) -> object:
        return None if value == "" else value


class ASRConfig(BaseModel):
    """Automatic speech recognition settings."""

    backend: str = "auto"
    model: str = "large-v3-turbo"
    device: str = "auto"
    compute_type: str = "auto"
    language: str | None = "ja"
    vad_filter: bool = True
    condition_on_previous_text: bool = False
    beam_size: int = Field(default=5, ge=1)
    initial_prompt: str | None = ""
    word_timestamps: Literal["auto", "always", "never"] = "auto"
    whisper_cpp: WhisperCppConfig = Field(default_factory=WhisperCppConfig)


class DiarizationConfig(BaseModel):
    """Speaker diarization settings."""

    enabled: bool = True
    backend: str = "pyannote"
    model: str = "pyannote/speaker-diarization-community-1"
    device: str = "auto"
    num_speakers: int = Field(default=0, ge=0)
    min_speakers: int = Field(default=0, ge=0)
    max_speakers: int = Field(default=0, ge=0)
    memory_guard: Literal["auto", "warn", "off"] = "auto"
    memory_safety_margin: float = Field(default=0.0, ge=0.0, lt=1.0)

    @model_validator(mode="after")
    def validate_speaker_counts(self) -> DiarizationConfig:
        """Reject contradictory speaker-count constraints."""
        if self.num_speakers and (self.min_speakers or self.max_speakers):
            raise ValueError("num_speakers と min_speakers/max_speakers は同時指定できません")
        if self.min_speakers and self.max_speakers and self.min_speakers > self.max_speakers:
            raise ValueError("min_speakers は max_speakers 以下である必要があります")
        return self


class OutputConfig(BaseModel):
    """Export formats and speaker presentation settings."""

    formats: list[OutputFormat] = Field(default_factory=_default_output_formats, min_length=1)
    srt_bom: bool = False
    newline: Literal["lf", "crlf"] = "lf"
    show_speaker: bool = True
    speaker_labels: dict[str, str] = Field(default_factory=dict)


class FfmpegConfig(BaseModel):
    """ffmpeg executable override."""

    path: Path | None = None

    @field_validator("path", mode="before")
    @classmethod
    def empty_path_uses_discovery(cls, value: object) -> object:
        """Translate the documented empty-string sentinel to None."""
        return None if value == "" else value


class AlignmentConfig(BaseModel):
    """ASR/diarization alignment thresholds from design section 7."""

    max_nearest_distance: float = Field(default=2.0, ge=0.0)
    min_segment_duration: float = Field(default=0.3, ge=0.0)
    min_segment_words: int = Field(default=2, ge=0)
    merge_gap: float = Field(default=0.5, ge=0.0)
    renumber_speakers: bool = True


class Config(BaseSettings):
    """Validated application settings with explicit source precedence."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    diarization: DiarizationConfig = Field(default_factory=DiarizationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    ffmpeg: FfmpegConfig = Field(default_factory=FfmpegConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)

    model_config = SettingsConfigDict(
        env_prefix="UTTERAN_",
        env_nested_delimiter="__",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @classmethod
    def load(
        cls,
        *,
        config_path: Path | None = None,
        dotenv_path: Path | None = None,
        cli_overrides: dict[str, Any] | None = None,
    ) -> Config:
        """Load settings using CLI > env > .env > config.toml > defaults."""
        selected_config = config_path or Path(user_config_dir("utteran")) / "config.toml"
        selected_dotenv = dotenv_path or Path.cwd() / ".env"
        merged: dict[str, Any] = {}
        _deep_merge(merged, _read_toml(selected_config))
        _deep_merge(
            merged,
            DotEnvSettingsSource(
                cls,
                env_file=selected_dotenv,
                env_file_encoding="utf-8",
            )(),
        )
        _deep_merge(merged, EnvSettingsSource(cls)())
        _deep_merge(merged, cli_overrides or {})
        try:
            return cls.model_validate(merged)
        except ValidationError as exc:
            raise ConfigurationError(f"設定が不正です: {exc}") from None

    @property
    def effective_job_dir(self) -> Path:
        """Return the configured job dir or the platform cache default."""
        return self.general.job_dir or Path(user_cache_dir("utteran")) / "jobs"


def default_config_path() -> Path:
    """Return the platform-specific config.toml location."""
    return Path(user_config_dir("utteran")) / "config.toml"


def initialize_config(path: Path | None = None) -> Path:
    """Create the documented token-free template without overwriting a file."""
    selected = path or default_config_path()
    selected.parent.mkdir(parents=True, exist_ok=True)
    try:
        with selected.open("x", encoding="utf-8", newline="\n") as config_file:
            config_file.write(CONFIG_TEMPLATE)
    except FileExistsError:
        raise ConfigurationError(f"設定ファイルは既に存在します: {selected}") from None
    return selected


class TokenProvider(ABC):
    """Interface used by CLI and future GUI code to resolve an HF token."""

    @abstractmethod
    def get_token(self) -> str | None:
        """Return a token without logging it, or None when unavailable."""


class EnvironmentTokenProvider(TokenProvider):
    """Read a Hugging Face token from process environment variables."""

    def get_token(self) -> str | None:
        """Prefer the standard HF_TOKEN name."""
        return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or None


class DotEnvTokenProvider(TokenProvider):
    """Read a Hugging Face token from a dotenv file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get_token(self) -> str | None:
        """Return the first supported token name from the dotenv file."""
        if not self._path.is_file():
            return None
        values = dotenv_values(self._path)
        value = values.get("HF_TOKEN") or values.get("HUGGING_FACE_HUB_TOKEN")
        return value or None


class KeyringTokenProvider(TokenProvider):
    """Read a Hugging Face token from the OS credential store."""

    def get_token(self) -> str | None:
        """Silently skip hosts without a usable keyring backend."""
        try:
            import keyring

            value = keyring.get_password("utteran", "huggingface")
            return value if isinstance(value, str) else None
        except Exception:
            return None


class ChainedTokenProvider(TokenProvider):
    """Return the first token supplied by a sequence of providers."""

    def __init__(self, providers: list[TokenProvider]) -> None:
        self._providers = providers

    def get_token(self) -> str | None:
        """Resolve a token in configured priority order."""
        for provider in self._providers:
            token = provider.get_token()
            if token:
                return token
        return None


def default_token_provider(dotenv_path: Path | None = None) -> TokenProvider:
    """Create the design-specified env > .env > keyring provider chain."""
    selected_dotenv = dotenv_path or Path.cwd() / ".env"
    return ChainedTokenProvider(
        [
            EnvironmentTokenProvider(),
            DotEnvTokenProvider(selected_dotenv),
            KeyringTokenProvider(),
        ]
    )


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge nested source values into target, replacing scalar values."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            nested_target = target[key]
            assert isinstance(nested_target, dict)
            _deep_merge(nested_target, value)
        else:
            target[key] = value


def _read_toml(path: Path) -> dict[str, Any]:
    """Read config TOML and discard any token-like keys with a warning."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"設定ファイルを読み込めません: {path}: {exc}") from None
    if _strip_token_keys(data):
        warnings.warn(
            "config.toml 内のトークン設定は安全のため無視しました。"
            "HF_TOKEN 環境変数、.env、または OS キーリングを使用してください。",
            UserWarning,
            stacklevel=2,
        )
    return data


def _strip_token_keys(data: dict[str, Any]) -> bool:
    """Remove secret-looking TOML keys recursively without inspecting values."""
    token_keys = {"token", "hf_token", "huggingface_token", "hugging_face_token", "access_token"}
    found = False
    for key in list(data):
        value = data[key]
        if key.casefold() in token_keys:
            del data[key]
            found = True
        elif isinstance(value, dict):
            found = _strip_token_keys(value) or found
    return found
