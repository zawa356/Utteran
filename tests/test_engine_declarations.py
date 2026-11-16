"""Engine declarations are the single source for what each engine accepts and what the
B3 worker runs (utteran_contracts.engines).

Covers: declaration <-> recipe <-> core registry consistency, the same refusal list in
the B4 evaluator inputs and the B2 adapter, engine-neutral B7 planning (no
``faster-whisper`` literal left in B7/B3), and fail-closed refusal of every engine the
worker does not execute. Synthetic fixtures only; no model, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from test_b7_b3_model_route import MODEL_FILES, model_files, offline, over_ipc, planned
from test_b7_jobs import Env
from utteran.asr.registry import create_asr_backend
from utteran.capabilities.recipes import RECIPES
from utteran.config import Config
from utteran.config_adapter import ConfigAdaptationError, ExecutionContext, adapt_settings
from utteran.diarization.registry import create_diarization_backend
from utteran.errors import BackendUnavailableError
from utteran.worker_runtime import explicit_config, offline_preflight
from utteran_contracts import ProcessingSettings, engines
from utteran_contracts.engines import (
    DIARIZATION_DECLARATIONS,
    ENGINE_DECLARATIONS,
    EngineDeclaration,
    engine_catalog,
    engine_for_family,
    get_engine,
    setting_refusals,
    worker_recipes,
    worker_refusals,
)
from utteran_contracts.jobs import JobSnapshot
from utteran_contracts.worker import WorkerErrorCode
from utteran_contracts.worker_framing import ProtocolError
from utteran_gui.job_admission import WORKER_RECIPES
from utteran_gui.job_dispatcher import PlanError, plan_request

ROOT = Path(__file__).parents[1]
__all__ = ["offline"]  # re-exported pytest fixture


@pytest.fixture
def settings_data() -> dict[str, Any]:
    data = json.loads(
        (ROOT / "docs/architecture/phase-a/examples.json").read_text(encoding="utf-8")
    )["preset"]["settings"]
    data["asr"]["backend"] = "auto"
    data["asr"]["condition_on_previous_text"] = False
    for namespace in ("faster_whisper", "whisper_cpp", "openvino_genai"):
        data["asr"].pop(namespace, None)
    return data


def _settings(data: dict[str, Any], **asr: Any) -> ProcessingSettings:
    return ProcessingSettings.model_validate({**data, "asr": {**data["asr"], **asr}})


# ----------------------------------------------------------------- table consistency


def test_catalog_is_complete_and_unique() -> None:
    catalog = engine_catalog()
    assert [e.engine for e in catalog.engines] == [
        "faster-whisper",
        "whisper-cpp",
        "openvino-genai",
    ]
    assert len({e.model_family for e in catalog.engines}) == 3
    assert len({e.settings_namespace for e in catalog.engines}) == 3
    assert [d.engine for d in catalog.diarization_engines] == ["pyannote"]
    for engine in catalog.engines:
        assert get_engine(engine.engine) is engine
        assert engine_for_family(engine.model_family) is engine
        implemented = engine.worker.status == "implemented"
        assert implemented == bool(engine.worker.recipes)
        assert implemented == (engine.worker.blocker == "none")
        assert not engine.worker.diarization or implemented


@pytest.mark.parametrize("name", ["", "auto", "whisperx", "Faster-Whisper"])
def test_unknown_engine_is_refused_not_defaulted(name: str) -> None:
    with pytest.raises(ValueError, match="unsupported_engine"):
        get_engine(name)
    with pytest.raises(ValueError, match="unsupported_engine"):
        engine_for_family(name)


# Recipe flags as shipped before the declarations existed (0.1.51): deriving them from
# the table must not change a byte of the B4 catalogue or any evidence fingerprint.
SHIPPED = {
    "fw-cpu": (True, True, True),
    "fw-cuda": (True, True, True),
    "wc-cpu": (False, True, True),
    "wc-vulkan": (False, True, True),
    "wc-ov": (False, True, True),
    "wc-ov-vk": (False, True, True),
    "genai-ov": (False, True, False),
}


def test_recipes_are_derived_without_changing_shipped_flags() -> None:
    assert {
        r.id: (r.worker_supported, r.word_timestamps, r.non_space_diarization) for r in RECIPES
    } == SHIPPED


def test_every_recipe_fits_its_engine_declaration() -> None:
    for recipe in RECIPES:
        engine = get_engine(recipe.backend)
        assert recipe.model_family == engine.model_family
        assert set(recipe.devices) <= set(engine.devices)
        variant = recipe.requested_route.native_variant
        assert recipe.native_required == bool(engine.native_variants)
        assert variant is None or variant in engine.native_variants
        assert recipe.worker_supported == (recipe.id in engine.worker.recipes)
    known = {r.id for r in RECIPES}
    assert worker_recipes() <= known
    assert worker_recipes() == WORKER_RECIPES == {"fw-cpu", "fw-cuda"}
    for engine in ENGINE_DECLARATIONS:
        declared = {
            r.requested_route.native_variant for r in RECIPES if r.backend == engine.engine
        } - {None}
        assert declared == set(engine.native_variants)


def test_core_registry_implements_every_declared_engine() -> None:
    for engine in ENGINE_DECLARATIONS:
        assert create_asr_backend(engine.engine).name == engine.engine
    for diarization in DIARIZATION_DECLARATIONS:
        assert create_diarization_backend(diarization.engine).name == diarization.engine
    with pytest.raises(BackendUnavailableError):
        create_asr_backend("undeclared-engine")


# ------------------------------------------------------------- one refusal list

CASES: dict[str, tuple[dict[str, Any], str]] = {
    "glossary": ({"glossary": ["Utteran"]}, "/asr/glossary"),
    "extra_arguments": (
        {
            "extra_arguments": {
                "backend": "whisper-cpp",
                "tokens": ["--flag"],
                "allow_unverified": True,
            }
        },
        "/asr/extra_arguments",
    ),
    "vad_threshold": ({"vad": {"enabled": True, "threshold": 0.5}}, "/asr/vad/threshold"),
    "never": ({"word_timestamps": "never"}, "/asr/word_timestamps"),
    "previous_text": ({"condition_on_previous_text": True}, "/asr/condition_on_previous_text"),
}
# engine -> the cases it refuses. Anything not listed must be accepted.
REFUSED = {
    "faster-whisper": {"glossary", "extra_arguments", "vad_threshold", "never"},
    "whisper-cpp": {"glossary", "extra_arguments", "previous_text"},
    "openvino-genai": {"glossary", "extra_arguments", "previous_text", "vad_threshold"},
}


@pytest.mark.parametrize("engine", [e.engine for e in ENGINE_DECLARATIONS])
@pytest.mark.parametrize("case", sorted(CASES))
def test_setting_is_refused_with_a_reason_or_accepted_never_dropped(
    engine: str, case: str, settings_data: dict[str, Any]
) -> None:
    change, path = CASES[case]
    declaration = get_engine(engine)
    settings = _settings(settings_data, **change)
    refused = dict(setting_refusals(declaration, settings))
    assert (path in refused) == (case in REFUSED[engine])
    assert setting_refusals(declaration, _settings(settings_data)) == ()
    if not declaration.structured_adapter:
        return
    # The pure adapter (what the worker executes) agrees with the declaration.
    variant = "cpu" if declaration.native_variants else None
    context = ExecutionContext(
        Config.model_validate({}),
        engine,
        "large-v3-turbo",
        "cpu",
        "cpu",
        "int8" if variant is None else "ggml",
        variant,  # type: ignore[arg-type]
    )
    if path in refused:
        with pytest.raises(ConfigAdaptationError) as error:
            adapt_settings(settings, context)
        assert (error.value.path, error.value.code) == (path, refused[path])
    else:
        assert adapt_settings(settings, context).config.asr.backend == engine


def test_inactive_namespace_and_namespace_fields_are_refused(
    settings_data: dict[str, Any],
) -> None:
    other = _settings(settings_data, whisper_cpp={"threads": 2})
    assert ("/asr/whisper_cpp", "inactive_backend_settings") in setting_refusals(
        get_engine("faster-whisper"), other
    )
    assert setting_refusals(get_engine("whisper-cpp"), other) == ()
    genai = _settings(settings_data, openvino_genai={"max_new_tokens": 8})
    assert ("/asr/openvino_genai/max_new_tokens", "unsupported_setting") in setting_refusals(
        get_engine("openvino-genai"), genai
    )


@pytest.mark.parametrize("language", ["ja", "auto", "zh"])
def test_engine_without_non_space_word_timing_refuses_always(
    language: str, settings_data: dict[str, Any]
) -> None:
    settings = _settings(settings_data, word_timestamps="always", language=language)
    assert ("/asr/word_timestamps", "unsupported_setting") in setting_refusals(
        get_engine("openvino-genai"), settings
    )
    assert setting_refusals(get_engine("whisper-cpp"), settings) == ()
    english = _settings(settings_data, word_timestamps="always", language="en")
    assert setting_refusals(get_engine("openvino-genai"), english) == ()


# ------------------------------------------------------------- worker fail closed


def test_worker_refusals_follow_the_declaration(settings_data: dict[str, Any]) -> None:
    plain = _settings(settings_data)
    fw = get_engine("faster-whisper")
    assert worker_refusals(fw, plain, recipe_id="fw-cpu") == ()
    assert worker_refusals(fw, plain, recipe_id="wc-cpu") == ("worker_backend_unsupported",)
    for name in ("whisper-cpp", "openvino-genai"):
        assert worker_refusals(get_engine(name), plain) == ("worker_backend_unsupported",)
    diarized = ProcessingSettings.model_validate(
        {**settings_data, "diarization": {**settings_data["diarization"], "enabled": True}}
    )
    assert worker_refusals(fw, diarized, recipe_id="fw-cpu") == ("worker_diarization_unsupported",)
    assert worker_refusals(fw, _settings(settings_data, glossary=["x"]), recipe_id="fw-cpu") == (
        "worker_glossary_unsupported",
    )


def _respec(snapshot: JobSnapshot, **changes: Any) -> JobSnapshot:
    data = snapshot.to_wire()
    data["resolved"].update(changes)
    return JobSnapshot.model_validate(data)


def _plan(snapshot: JobSnapshot, tmp: Path) -> Any:
    return plan_request(
        snapshot, job_id="job_" + "0" * 32, attempt_id="att_" + "1" * 32, job_dir=tmp / "jobs"
    )


WC_ROUTE = {"native_variant": "cpu", "encoder": "cpu", "decoder": "cpu"}


@pytest.mark.parametrize(
    "changes",
    [
        {"backend": "whisper-cpp", "recipe_id": "wc-cpu", "requested_route": WC_ROUTE},
        {"backend": "openvino-genai", "recipe_id": "genai-ov"},
        {"backend": "whisper-cpp"},  # engine does not match the frozen recipe
        {"recipe_id": "wc-cpu"},
        {"requested_route": WC_ROUTE},  # native variant on a non-native engine
        {"device": "npu"},
    ],
    ids=["whisper-cpp", "openvino-genai", "engine-recipe", "recipe", "variant", "device"],
)
def test_dispatcher_refuses_routes_the_worker_does_not_execute(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    env = Env(tmp_path, None)
    snapshot, _ = planned(env)
    with pytest.raises(PlanError, match="worker_plan_unsupported"):
        _plan(_respec(snapshot, **changes), tmp_path)


def test_faster_whisper_plan_is_unchanged_by_the_generalization(tmp_path: Path) -> None:
    env = Env(tmp_path, None)
    snapshot, request = planned(env)
    context = request.context
    assert context.asr_backend == snapshot.resolved.backend == "faster-whisper"
    assert context.asr_device == snapshot.resolved.device
    assert context.native_variant is None
    assert context.diarization_device == "cpu"


@pytest.mark.parametrize("backend", ["whisper-cpp", "openvino-genai"])
def test_b3_refuses_engines_closed_in_the_declaration(
    tmp_path: Path, offline: list[Any], backend: str
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    model_files(model)
    config = Config.model_validate(
        {"asr": {"backend": backend, "model": str(model)}, "diarization": {"enabled": False}}
    )
    with pytest.raises(ProtocolError) as error:
        offline_preflight(config)
    assert error.value.code is WorkerErrorCode.UNSUPPORTED_SETTING
    assert offline == []


def test_b3_refuses_diarization_until_the_declaration_opens_it(
    tmp_path: Path, offline: list[Any]
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    model_files(model)
    config = Config.model_validate(
        {"asr": {"backend": "faster-whisper", "model": str(model), "device": "cpu"}}
    )
    assert config.diarization.enabled
    with pytest.raises(ProtocolError) as error:
        offline_preflight(config)
    assert error.value.code is WorkerErrorCode.UNSUPPORTED_SETTING


# ------------------------------------------------------- the contract is engine-neutral


def _open(monkeypatch: pytest.MonkeyPatch, engine: str, recipes: tuple[str, ...]) -> None:
    """Test-only declaration that opens one more engine; the shipped table is untouched."""
    opened = EngineDeclaration.model_validate(
        {
            **get_engine(engine).to_wire(),
            "worker": {
                "status": "implemented",
                "blocker": "none",
                "recipes": recipes,
                "diarization": False,
                "required_model_files": ("ggml-model.bin",),
            },
        }
    )
    monkeypatch.setitem(engines._BY_ENGINE, engine, opened)
    monkeypatch.setitem(engines._BY_FAMILY, opened.model_family, opened)


def test_opening_an_engine_in_the_declaration_is_enough_for_b7_and_b3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: list[Any]
) -> None:
    """No engine name is hard-coded between the frozen snapshot and the core Config.

    With a declaration that lets the worker run whisper.cpp, the same B7 plan and B3
    boundary carry engine, native variant, model ID and frozen directory through the
    private IPC into a core Config -- without editing the dispatcher or the worker.
    """

    def neutral(settings: dict[str, Any]) -> None:
        settings["asr"].pop("faster_whisper", None)
        settings["asr"].update(backend="auto", condition_on_previous_text=False)

    env = Env(tmp_path, neutral)
    snapshot, _ = planned(env)
    model = tmp_path / "ggml"
    model.mkdir()
    (model / "ggml-model.bin").write_text("synthetic", encoding="utf-8")
    wc = _respec(
        snapshot,
        backend="whisper-cpp",
        recipe_id="wc-cpu",
        requested_route=WC_ROUTE,
        compute_type="ggml",
        worker_model_dir=str(model),
    )
    with pytest.raises(PlanError):
        _plan(wc, tmp_path)
    _open(monkeypatch, "whisper-cpp", ("wc-cpu",))
    request = over_ipc(_plan(wc, tmp_path))
    context = request.context
    assert (context.asr_backend, context.native_variant, context.asr_device) == (
        "whisper-cpp",
        "cpu",
        "cpu",
    )
    assert context.asr_model == snapshot.resolved.model_id
    config = explicit_config(request)
    assert config.asr.backend == "whisper-cpp"
    assert config.asr.whisper_cpp.variant == "cpu"
    assert config.asr.model == str(model)
    offline_preflight(config)
    (model / "ggml-model.bin").unlink()
    with pytest.raises(ProtocolError) as error:
        offline_preflight(config)
    assert error.value.code is WorkerErrorCode.MODEL_MISSING
    # A variant the engine does not declare stays refused even when it is opened.
    with pytest.raises((PlanError, ValueError)):
        _plan(_respec(wc, requested_route={**WC_ROUTE, "native_variant": None}), tmp_path)


@pytest.mark.parametrize(
    "module",
    [
        "src/utteran_gui/job_admission.py",
        "src/utteran_gui/job_dispatcher.py",
        "src/utteran/worker_runtime.py",
    ],
)
def test_no_engine_name_is_hard_coded_in_the_execution_route(module: str) -> None:
    source = (ROOT / module).read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    for engine in ENGINE_DECLARATIONS:
        assert f'"{engine.engine}"' not in code


def test_required_model_files_match_the_b3_precheck() -> None:
    assert get_engine("faster-whisper").worker.required_model_files == MODEL_FILES


def test_declared_vad_threshold_matches_what_each_core_adapter_reads() -> None:
    """A declaration must not promise a setting the core adapter never reads.

    Review of 0.1.52: the GenAI adapter has no VAD handling at all, so a threshold would
    have been dropped silently; it is declared unsupported and therefore refused.
    """
    sources = {
        "faster-whisper": "src/utteran/asr/faster_whisper.py",
        "whisper-cpp": "src/utteran/asr/whisper_cpp.py",
        "openvino-genai": "src/utteran/asr/openvino_genai.py",
    }
    for engine in ENGINE_DECLARATIONS:
        source = (ROOT / sources[engine.engine]).read_text(encoding="utf-8")
        if engine.vad_threshold:
            assert "vad_threshold" in source, engine.engine
    assert [e.engine for e in ENGINE_DECLARATIONS if e.vad_threshold] == ["whisper-cpp"]
