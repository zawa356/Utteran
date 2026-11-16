"""Static engine declarations: what each inference engine accepts and what the worker runs.

One table for facts that used to be repeated as ``backend == "faster-whisper"`` checks in
the B4 evaluator, the B2 config adapter, the B3 worker boundary, B5 validation warnings,
B7 admission and the B7 dispatcher. Data only: nothing here probes, imports an inference
library or claims readiness. A declaration says an engine *can accept* a setting;
whether a deployment is runnable is still decided by B4 evidence (``ready``).

Three axes stay separate (see docs/architecture/backend/engine-adapters.md):
engine (this table) / model (catalogue + manifest) / execution profile (recipe + venv).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictStr

from utteran_contracts.base import Contract, VersionedContract
from utteran_contracts.capabilities import Backend, Identifier, ModelFamily
from utteran_contracts.settings import ProcessingSettings

SettingsNamespace = Literal["faster_whisper", "whisper_cpp", "openvino_genai"]
WordTimestampMode = Literal["auto", "always", "never"]
NativeVariant = Literal["cpu", "vulkan", "openvino", "openvino_vulkan"]
# Languages written without spaces: word timing there needs engine support.
NON_SPACE_LANGUAGES = frozenset({"ja", "zh", "th", "lo", "my", "yue"})


class WorkerExecution(Contract):
    """What the B3 worker executes for an engine today (not what core/CLI can do)."""

    status: Literal["implemented", "not_implemented"]
    # Why the worker does not run this engine yet; "none" when implemented.
    blocker: Literal["none", "native_argv_privacy", "offline_load_unverified"]
    # Recipe IDs the worker executes. Must equal the recipes' worker_supported flags.
    recipes: tuple[Identifier, ...]
    diarization: StrictBool
    # Files that must exist in the frozen local model directory before core loads it.
    # A necessary pre-check only; the real load in core is the proof.
    required_model_files: tuple[Annotated[StrictStr, Field(min_length=1, max_length=200)], ...]


class EngineDeclaration(Contract):
    engine: Backend
    model_family: ModelFamily
    settings_namespace: SettingsNamespace
    # Python module whose presence the worker checks; None for native executables.
    runtime_module: Annotated[StrictStr, Field(min_length=1, max_length=100)] | None
    devices: tuple[Literal["cpu", "cuda", "gpu", "npu"], ...]
    native_variants: tuple[NativeVariant, ...]
    word_timestamp_modes: tuple[WordTimestampMode, ...]
    # False: word timestamps are unavailable for NON_SPACE_LANGUAGES.
    non_space_word_timestamps: StrictBool
    segment_timestamps: StrictBool
    initial_prompt: Literal["native", "unsupported"]
    # No engine compiles a glossary yet; "unsupported" must be refused, never ignored.
    glossary: Literal["unsupported", "prompt_bias"]
    vad_threshold: StrictBool
    condition_on_previous_text: StrictBool
    extra_arguments: StrictBool
    # Fields of the engine's own settings namespace that the structured path refuses.
    unsupported_namespace_fields: tuple[Identifier, ...]
    # Whether the pure structured-settings adapter (B2) converts this engine.
    structured_adapter: StrictBool
    cancel_granularity: Literal["segment", "process", "stage"]
    progress_granularity: Literal["segment", "percent", "stage"]
    worker: WorkerExecution


class DiarizationEngineDeclaration(Contract):
    engine: Literal["pyannote"]
    devices: tuple[Literal["cpu", "cuda", "xpu"], ...]
    # ASR must produce word timestamps for speaker assignment in the core pipeline.
    requires_word_timestamps: StrictBool
    cancel_granularity: Literal["stage"]
    progress_granularity: Literal["stage"]
    worker: Literal["implemented", "not_implemented"]
    worker_blocker: Literal["none", "offline_load_unverified"]


class EngineCatalog(VersionedContract):
    engines: tuple[EngineDeclaration, ...]
    diarization_engines: tuple[DiarizationEngineDeclaration, ...]


def _worker(
    status: Literal["implemented", "not_implemented"],
    blocker: Literal["none", "native_argv_privacy", "offline_load_unverified"],
    recipes: tuple[str, ...] = (),
    files: tuple[str, ...] = (),
) -> WorkerExecution:
    return WorkerExecution(
        status=status,
        blocker=blocker,
        recipes=recipes,
        diarization=False,
        required_model_files=files,
    )


ENGINE_DECLARATIONS: tuple[EngineDeclaration, ...] = (
    EngineDeclaration(
        engine="faster-whisper",
        model_family="ctranslate2",
        settings_namespace="faster_whisper",
        runtime_module="faster_whisper",
        devices=("cpu", "cuda"),
        native_variants=(),
        word_timestamp_modes=("auto", "always"),
        non_space_word_timestamps=True,
        segment_timestamps=True,
        initial_prompt="native",
        glossary="unsupported",
        vad_threshold=False,
        condition_on_previous_text=True,
        extra_arguments=False,
        unsupported_namespace_fields=(),
        structured_adapter=True,
        cancel_granularity="segment",
        progress_granularity="segment",
        worker=_worker(
            "implemented",
            "none",
            ("fw-cpu", "fw-cuda"),
            ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"),
        ),
    ),
    EngineDeclaration(
        engine="whisper-cpp",
        model_family="ggml",
        settings_namespace="whisper_cpp",
        runtime_module=None,
        devices=("cpu", "gpu"),
        native_variants=("cpu", "vulkan", "openvino", "openvino_vulkan"),
        word_timestamp_modes=("auto", "always", "never"),
        non_space_word_timestamps=True,
        segment_timestamps=True,
        initial_prompt="native",
        glossary="unsupported",
        vad_threshold=True,
        condition_on_previous_text=False,
        extra_arguments=False,
        unsupported_namespace_fields=(),
        structured_adapter=True,
        cancel_granularity="process",
        progress_granularity="percent",
        # The native CLI receives the prompt and input path as argv, visible to other
        # local processes; the worker keeps this route closed until that is addressed.
        worker=_worker("not_implemented", "native_argv_privacy"),
    ),
    EngineDeclaration(
        engine="openvino-genai",
        model_family="openvino-genai",
        settings_namespace="openvino_genai",
        runtime_module="openvino_genai",
        devices=("cpu", "gpu", "npu"),
        native_variants=(),
        word_timestamp_modes=("auto", "always", "never"),
        non_space_word_timestamps=False,
        segment_timestamps=True,
        initial_prompt="native",
        glossary="unsupported",
        vad_threshold=True,
        condition_on_previous_text=False,
        extra_arguments=False,
        unsupported_namespace_fields=("max_new_tokens",),
        structured_adapter=False,
        cancel_granularity="stage",
        progress_granularity="stage",
        worker=_worker("not_implemented", "offline_load_unverified"),
    ),
)

DIARIZATION_DECLARATIONS: tuple[DiarizationEngineDeclaration, ...] = (
    DiarizationEngineDeclaration(
        engine="pyannote",
        devices=("cpu", "cuda", "xpu"),
        requires_word_timestamps=True,
        cancel_granularity="stage",
        progress_granularity="stage",
        # Pipeline.from_pretrained can resolve transitive remote components; the worker
        # does not run it until a fully local load is proven.
        worker="not_implemented",
        worker_blocker="offline_load_unverified",
    ),
)

_BY_ENGINE = {item.engine: item for item in ENGINE_DECLARATIONS}
_BY_FAMILY = {item.model_family: item for item in ENGINE_DECLARATIONS}
_DIARIZATION = {item.engine: item for item in DIARIZATION_DECLARATIONS}


def engine_catalog() -> EngineCatalog:
    return EngineCatalog(
        schema_version=1,
        engines=ENGINE_DECLARATIONS,
        diarization_engines=DIARIZATION_DECLARATIONS,
    )


def get_engine(engine: str) -> EngineDeclaration:
    """Declared engine or ValueError("unsupported_engine"); never a default."""
    try:
        return _BY_ENGINE[engine]  # type: ignore[index]
    except KeyError:
        raise ValueError("unsupported_engine") from None


def engine_for_family(model_family: str) -> EngineDeclaration:
    try:
        return _BY_FAMILY[model_family]  # type: ignore[index]
    except KeyError:
        raise ValueError("unsupported_engine") from None


def get_diarization_engine(engine: str) -> DiarizationEngineDeclaration:
    try:
        return _DIARIZATION[engine]  # type: ignore[index]
    except KeyError:
        raise ValueError("unsupported_engine") from None


def worker_engines() -> tuple[EngineDeclaration, ...]:
    return tuple(item for item in ENGINE_DECLARATIONS if item.worker.status == "implemented")


def worker_recipes() -> frozenset[str]:
    return frozenset(recipe for item in worker_engines() for recipe in item.worker.recipes)


def setting_refusals(
    engine: EngineDeclaration, settings: ProcessingSettings
) -> tuple[tuple[str, str], ...]:
    """(JSON pointer, code) for every setting this engine cannot honour.

    The same list drives the B4 evaluator and the B2 adapter, so a value is either
    converted or refused with a reason; it is never dropped silently.
    """
    asr = settings.asr
    refused: list[tuple[str, str]] = []
    if asr.glossary and engine.glossary == "unsupported":
        refused.append(("/asr/glossary", "unsupported_setting"))
    if asr.extra_arguments is not None and not engine.extra_arguments:
        refused.append(("/asr/extra_arguments", "unsupported_setting"))
    for namespace in ("faster_whisper", "whisper_cpp", "openvino_genai"):
        if namespace != engine.settings_namespace and getattr(asr, namespace) is not None:
            refused.append((f"/asr/{namespace}", "inactive_backend_settings"))
    if asr.initial_prompt and engine.initial_prompt == "unsupported":
        refused.append(("/asr/initial_prompt", "unsupported_setting"))
    if asr.vad.threshold is not None and not engine.vad_threshold:
        refused.append(("/asr/vad/threshold", "unsupported_setting"))
    if asr.word_timestamps not in engine.word_timestamp_modes:
        refused.append(("/asr/word_timestamps", "unsupported_setting"))
    if asr.condition_on_previous_text and not engine.condition_on_previous_text:
        refused.append(("/asr/condition_on_previous_text", "unsupported_setting"))
    # "auto" may still resolve to a non-space language, so it is refused as well.
    language = asr.language.strip().casefold()
    if (
        not engine.non_space_word_timestamps
        and asr.word_timestamps == "always"
        and language in NON_SPACE_LANGUAGES | {"auto", ""}
    ):
        refused.append(("/asr/word_timestamps", "unsupported_setting"))
    own = getattr(asr, engine.settings_namespace)
    if own is not None:
        for name in engine.unsupported_namespace_fields:
            if getattr(own, name, None) is not None:
                refused.append((f"/asr/{engine.settings_namespace}/{name}", "unsupported_setting"))
    return tuple(refused)


def worker_refusals(
    engine: EngineDeclaration, settings: ProcessingSettings, *, recipe_id: str | None = None
) -> tuple[str, ...]:
    """Fixed codes for why the B3 worker cannot execute these settings on this engine."""
    codes: list[str] = []
    worker = engine.worker
    if worker.status != "implemented" or (
        recipe_id is not None and recipe_id not in worker.recipes
    ):
        codes.append("worker_backend_unsupported")
    if settings.diarization.enabled and not worker.diarization:
        codes.append("worker_diarization_unsupported")
    if settings.asr.glossary and engine.glossary == "unsupported":
        codes.append("worker_glossary_unsupported")
    if settings.asr.extra_arguments is not None and not engine.extra_arguments:
        codes.append("worker_arguments_unsupported")
    return tuple(codes)
