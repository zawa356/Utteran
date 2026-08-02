"""Static catalog of models supported by current and planned backends."""

from __future__ import annotations

from dataclasses import dataclass

from utteran.errors import ConfigurationError


@dataclass(frozen=True)
class ModelEntry:
    """One backend-specific downloadable model artifact."""

    model_id: str
    display_name: str
    description: str
    backend: str
    format: str
    repository_id: str
    approximate_size_bytes: int
    license: str
    gated: bool

    @property
    def key(self) -> str:
        """Return the unambiguous CLI identifier."""
        return f"{self.backend}:{self.model_id}"

    @property
    def agreement_url(self) -> str:
        """Return the authoritative Hugging Face model page."""
        return f"https://huggingface.co/{self.repository_id}"


_GIB = 1024**3
_MIB = 1024**2

CATALOG: tuple[ModelEntry, ...] = (
    ModelEntry(
        model_id="large-v3-turbo",
        display_name="Whisper large-v3-turbo",
        description="推奨の多言語ASR。速度と精度のバランスを重視",
        backend="faster-whisper",
        format="CTranslate2",
        repository_id="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        approximate_size_bytes=int(1.6 * _GIB),
        license="MIT",
        gated=False,
    ),
    ModelEntry(
        model_id="large-v3-turbo",
        display_name="Whisper large-v3-turbo (OpenVINO)",
        description="将来のIntel向けASR。Phase 2では推論未実装",
        backend="openvino",
        format="OpenVINO IR",
        repository_id="OpenVINO/whisper-large-v3-turbo-fp16-ov",
        approximate_size_bytes=int(1.63 * _GIB),
        license="MIT",
        gated=False,
    ),
    ModelEntry(
        model_id="large-v3",
        display_name="Whisper large-v3",
        description="高精度な多言語ASR。turboより大容量",
        backend="faster-whisper",
        format="CTranslate2",
        repository_id="Systran/faster-whisper-large-v3",
        approximate_size_bytes=int(3.1 * _GIB),
        license="MIT",
        gated=False,
    ),
    ModelEntry(
        model_id="kotoba-whisper-v2.0",
        display_name="Kotoba-Whisper v2.0",
        description="日本語音声認識向けASR",
        backend="faster-whisper",
        format="CTranslate2",
        repository_id="kotoba-tech/kotoba-whisper-v2.0-faster",
        approximate_size_bytes=int(1.52 * _GIB),
        license="MIT",
        gated=False,
    ),
    ModelEntry(
        model_id="pyannote/speaker-diarization-community-1",
        display_name="pyannote community-1",
        description="話者分離。利用条件への同意とHFトークンが必要",
        backend="pyannote",
        format="pyannote pipeline",
        repository_id="pyannote/speaker-diarization-community-1",
        approximate_size_bytes=100 * _MIB,
        license="CC-BY-4.0",
        gated=True,
    ),
)


def list_models(*, backend: str | None = None) -> tuple[ModelEntry, ...]:
    """Return catalog entries in stable declaration order."""
    return tuple(entry for entry in CATALOG if backend is None or entry.backend == backend)


def get_model(identifier: str, *, backend: str | None = None) -> ModelEntry:
    """Resolve a qualified key or a model/backend pair without ambiguity."""
    exact = [entry for entry in CATALOG if entry.key == identifier]
    if exact:
        return exact[0]
    matches = [
        entry
        for entry in CATALOG
        if entry.model_id == identifier and (backend is None or entry.backend == backend)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        suffix = f" (backend={backend})" if backend else ""
        raise ConfigurationError(f"モデルカタログにありません: {identifier}{suffix}")
    choices = ", ".join(entry.key for entry in matches)
    raise ConfigurationError(
        f"モデル ID '{identifier}' は複数の backend に存在します。次から指定してください: {choices}"
    )
