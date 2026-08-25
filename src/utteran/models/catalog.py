"""Static catalog of models supported by executable backends."""

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
    artifact_filename: str | None = None
    model_size: str | None = None
    quantization: str | None = None
    dtw_preset: str | None = None
    recommended: bool = True
    english_only: bool = False

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

_CORE_CATALOG: tuple[ModelEntry, ...] = (
    ModelEntry(
        model_id="silero-v6.2.0",
        display_name="Silero VAD v6.2.0 (whisper.cpp)",
        description="whisper.cppの無音区間除去に使用する軽量VADモデル",
        backend="whisper-cpp-vad",
        format="GGML VAD",
        repository_id="ggml-org/whisper-vad",
        approximate_size_bytes=885_159,
        license="MIT",
        gated=False,
        artifact_filename="ggml-silero-v6.2.0.bin",
    ),
    ModelEntry(
        model_id="tiny",
        display_name="Whisper tiny",
        description="最小・最速。短い音声の試用や低スペックCPU向け(精度は低め)",
        backend="faster-whisper",
        format="CTranslate2",
        repository_id="Systran/faster-whisper-tiny",
        approximate_size_bytes=78_200_000,
        license="MIT",
        gated=False,
        model_size="tiny",
        recommended=False,
    ),
    ModelEntry(
        model_id="base",
        display_name="Whisper base",
        description="軽量。速度を優先する短い音声や低スペックCPU向け",
        backend="faster-whisper",
        format="CTranslate2",
        repository_id="Systran/faster-whisper-base",
        approximate_size_bytes=148_000_000,
        license="MIT",
        gated=False,
        model_size="base",
    ),
    ModelEntry(
        model_id="small",
        display_name="Whisper small",
        description="速度と精度の軽量バランス。CPUでの普段使い向け",
        backend="faster-whisper",
        format="CTranslate2",
        repository_id="Systran/faster-whisper-small",
        approximate_size_bytes=486_000_000,
        license="MIT",
        gated=False,
        model_size="small",
    ),
    ModelEntry(
        model_id="medium",
        display_name="Whisper medium",
        description="large系より軽く、精度を重視するCPU環境向け",
        backend="faster-whisper",
        format="CTranslate2",
        repository_id="Systran/faster-whisper-medium",
        approximate_size_bytes=int(1.53 * _GIB),
        license="MIT",
        gated=False,
        model_size="medium",
        recommended=False,
    ),
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
        model_size="large-v3",
    ),
    ModelEntry(
        model_id="pyannote/speaker-diarization-community-1",
        display_name="pyannote community-1",
        description="話者分離。利用条件への同意とHFトークンが必要",
        backend="pyannote",
        format="pyannote pipeline",
        repository_id="pyannote/speaker-diarization-community-1",
        approximate_size_bytes=34 * _MIB,
        license="CC-BY-4.0",
        gated=True,
    ),
)

_GGML_FILES: tuple[tuple[str, int], ...] = (
    ("ggml-tiny.bin", 77691713),
    ("ggml-tiny-q5_1.bin", 32152673),
    ("ggml-tiny-q8_0.bin", 43537433),
    ("ggml-base.bin", 147951465),
    ("ggml-base-q5_1.bin", 59707625),
    ("ggml-base-q8_0.bin", 81768585),
    ("ggml-small.bin", 487601967),
    ("ggml-small-q5_1.bin", 190085487),
    ("ggml-small-q8_0.bin", 264464607),
    ("ggml-medium.bin", 1533763059),
    ("ggml-medium-q5_0.bin", 539212467),
    ("ggml-medium-q8_0.bin", 823369779),
    ("ggml-large-v1.bin", 3094623691),
    ("ggml-large-v2.bin", 3094623691),
    ("ggml-large-v2-q5_0.bin", 1080732091),
    ("ggml-large-v2-q8_0.bin", 1656129691),
    ("ggml-large-v3.bin", 3095033483),
    ("ggml-large-v3-q5_0.bin", 1081140203),
    ("ggml-large-v3-turbo.bin", 1624555275),
    ("ggml-large-v3-turbo-q5_0.bin", 574041195),
    ("ggml-large-v3-turbo-q8_0.bin", 874188075),
    ("ggml-tiny.en.bin", 77704715),
    ("ggml-tiny.en-q5_1.bin", 32166155),
    ("ggml-tiny.en-q8_0.bin", 43550795),
    ("ggml-base.en.bin", 147964211),
    ("ggml-base.en-q5_1.bin", 59721011),
    ("ggml-base.en-q8_0.bin", 81781811),
    ("ggml-small.en.bin", 487614201),
    ("ggml-small.en-q5_1.bin", 190098681),
    ("ggml-small.en-q8_0.bin", 264477561),
    ("ggml-medium.en.bin", 1533774781),
    ("ggml-medium.en-q5_0.bin", 539225533),
    ("ggml-medium.en-q8_0.bin", 823382461),
)

_RECOMMENDED_GGML = {
    "large-v3-turbo",
    "large-v3-turbo-q5_0",
    "large-v3",
    "large-v3-q5_0",
    "medium-q5_0",
    "base",
}


def _ggml_entry(filename: str, size: int) -> ModelEntry:
    stem = filename.removeprefix("ggml-").removesuffix(".bin")
    english_only = ".en" in stem
    model_size = stem.split("-q", 1)[0]
    quantization = "f16" if "-q" not in stem else "q" + stem.rsplit("-q", 1)[1]
    dtw_name = model_size.replace("large-v", "large.v")
    if model_size == "large-v3-turbo":
        dtw_name = "large.v3.turbo"
    return ModelEntry(
        model_id=stem,
        display_name=f"Whisper.cpp {stem}",
        description=f"whisper.cpp多言語ASR ({quantization})",
        backend="whisper-cpp",
        format="GGML",
        repository_id="ggerganov/whisper.cpp",
        approximate_size_bytes=size,
        license="MIT",
        gated=False,
        artifact_filename=filename,
        model_size=model_size,
        quantization=quantization,
        dtw_preset=dtw_name,
        recommended=stem in _RECOMMENDED_GGML,
        english_only=english_only,
    )


CATALOG: tuple[ModelEntry, ...] = _CORE_CATALOG + tuple(
    _ggml_entry(filename, size) for filename, size in _GGML_FILES
)


def list_models(
    *, backend: str | None = None, recommended_only: bool = False
) -> tuple[ModelEntry, ...]:
    """Return catalog entries in stable declaration order."""
    return tuple(
        entry
        for entry in CATALOG
        if (backend is None or entry.backend == backend)
        and (not recommended_only or entry.backend != "whisper-cpp" or entry.recommended)
    )


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
