from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import ValidationError

from utteran.capabilities.collector import collect_managed_deployments, refresh_inventory
from utteran.capabilities.evaluator import (
    digest,
    evaluate_candidate,
    evidence_fingerprint,
    make_inventory,
    normalize_observation,
)
from utteran.capabilities.recipes import RECIPES, get_recipe, recipe_catalog
from utteran.capabilities.resolver import resolve
from utteran_contracts import Policy, ProcessingSettings
from utteran_contracts.capabilities import (
    BenchmarkEvidence,
    CapabilityObservation,
    DeploymentInventory,
    ExecutionIdentity,
    InventoryRefreshRequest,
    InventoryRefreshResult,
)
from utteran_contracts.capability_schema import schema_bytes
from utteran_gui.capability_inventory import CapabilityInventoryAdapter
from utteran_gui.cli import CliAdapter, ProfileInfo

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 11, 2, tzinfo=UTC)
LATER = NOW + timedelta(minutes=10)
FP = digest("synthetic test identity only")


@pytest.fixture
def settings() -> ProcessingSettings:
    data = json.loads(
        (ROOT / "docs/architecture/phase-a/examples.json").read_text(encoding="utf-8")
    )
    value = data["preset"]["settings"]
    value["asr"].update(
        backend="auto", model="auto", language="en", condition_on_previous_text=False
    )
    for namespace in ("faster_whisper", "whisper_cpp", "openvino_genai", "extra_arguments"):
        value["asr"].pop(namespace, None)
    value["asr"]["glossary"] = []
    value["diarization"]["enabled"] = False
    return ProcessingSettings.model_validate(value)


def change(settings: ProcessingSettings, section: str, **updates: Any) -> ProcessingSettings:
    data = settings.to_wire()
    data[section].update(updates)
    return ProcessingSettings.model_validate(data)


def policy(**updates: Any) -> Policy:
    return Policy.model_validate(
        dict(
            schema_version=1,
            id="policy",
            revision=1,
            name="test",
            mode="auto",
            fixed_candidate_id=None,
            allowed_profile_ids=[],
            objective="balanced",
            max_memory_bytes=None,
            fallback="none",
            execution_deadline_seconds=None,
        )
        | updates
    )


def deployment(
    settings: ProcessingSettings, recipe_id: str = "fw-cpu", **updates: Any
) -> DeploymentInventory:
    recipe = get_recipe(recipe_id)
    identity = ExecutionIdentity.model_validate(
        dict(
            recipe_id=recipe_id,
            recipe_revision=1,
            adapter_version="b4.1",
            smoke_version=1,
            profile_id="cpu",
            profile_generation="generation-1",
            profile_fingerprint=FP,
            platform="Windows",
            python_abi="cp312",
            runtime_fingerprint=FP,
            hardware_fingerprint=FP,
            model_id="large-v3-turbo",
            model_family=recipe.model_family,
            english_only=False,
            model_revision="revision-1",
            model_files_fingerprint=FP,
            native_fingerprint=FP if recipe.native_required else None,
            device=recipe.devices[0],
            device_index=0,
            compute_type=recipe.compute_types[0],
            execution_path="cli",
            local_complete_model=True,
            input_count=1,
            diarization_device="cpu" if settings.diarization.enabled else None,
            diarization_device_index=0 if settings.diarization.enabled else None,
            diarization_model=settings.diarization.model if settings.diarization.enabled else None,
            diarization_fingerprint=FP if settings.diarization.enabled else None,
        )
        | updates
    )
    fp = evidence_fingerprint(identity, settings)
    # Synthetic evidence in this test helper is NOT a real inference result.
    observations = tuple(
        CapabilityObservation.model_validate(
            dict(
                kind=kind,
                state="passed",
                source="provider",
                observed_at=NOW,
                expires_at=LATER,
                fingerprint=fp,
                reason_code="supported",
            )
        )
        for kind in (
            "hardware",
            "runtime",
            "venv",
            "model",
            "native",
            "smoke",
            "memory",
            "diarization",
        )
    )
    # Explicit synthetic runtime attestations, including engine-specific indices.
    # Production providers must observe these; core never copies requested values.
    actual = recipe.requested_route.to_wire()
    for component in ("encoder", "decoder"):
        if actual[component] == "selected-device":
            actual[component] = identity.device
    measured = {
        key: value if value != "backend-default" else "unknown"
        for key, value in actual.items()
        if key != "native_variant"
    }
    observations = tuple(
        o.model_copy(
            update={
                "initialized_route": actual,
                "encoder_observed": measured["encoder"],
                "decoder_observed": measured["decoder"],
                "encoder_device_index_observed": identity.device_index
                if measured["encoder"] not in {"unknown", "cpu"}
                else None,
                "decoder_device_index_observed": identity.device_index
                if measured["decoder"] not in {"unknown", "cpu"}
                else None,
            }
        )
        if o.kind == "smoke"
        else o
        for o in observations
    )
    return DeploymentInventory(
        identity=identity,
        observations=observations,
        required_memory_bytes=100,
        available_memory_bytes=200,
    )


def evaluate(d: DeploymentInventory, settings: ProcessingSettings, *, now: datetime = NOW):
    return evaluate_candidate(d, settings, d.identity.execution_path, now=now)


def replace_observation(d: DeploymentInventory, kind: str, **updates: Any) -> DeploymentInventory:
    return d.model_copy(
        update={
            "observations": tuple(
                o.model_copy(update=updates) if o.kind == kind else o for o in d.observations
            )
        }
    )


def inventory(settings: ProcessingSettings, *deployments: DeploymentInventory, revision: int = 1):
    return make_inventory(tuple(deployments), settings, "cli", revision=revision, now=NOW)


def codes(candidate) -> set[str]:
    return {issue.code for issue in candidate.issues}


def test_registry_is_explicit_and_preserves_route_meaning() -> None:
    assert [r.id for r in RECIPES] == [
        "fw-cpu",
        "fw-cuda",
        "wc-cpu",
        "wc-vulkan",
        "wc-ov",
        "wc-ov-vk",
        "genai-ov",
    ]
    combined = get_recipe("wc-ov-vk")
    assert combined.requested_route.encoder == "openvino-gpu"
    assert combined.requested_route.decoder == "vulkan"
    assert get_recipe("genai-ov").backend == "openvino-genai"
    assert not any(
        r.requested_route.encoder == "openvino-gpu" and "cuda" in r.devices for r in RECIPES
    )
    with pytest.raises(ValueError, match="unsupported_recipe"):
        get_recipe("openvino-cuda")


@pytest.mark.parametrize("recipe_id", [r.id for r in RECIPES])
def test_each_recipe_with_matching_synthetic_evidence(settings, recipe_id) -> None:
    assert evaluate(deployment(settings, recipe_id), settings).runnable == "ready"


@pytest.mark.parametrize("recipe_id", [r.id for r in RECIPES])
def test_worker_transport_is_narrow(settings, recipe_id) -> None:
    d = deployment(settings, recipe_id, execution_path="worker")
    candidate = evaluate(d, settings)
    assert (candidate.runnable == "ready") == recipe_id.startswith("fw-")
    if not recipe_id.startswith("fw-"):
        assert "worker_backend_unsupported" in codes(candidate)


@pytest.mark.parametrize("language", ["auto", "ja", "zh", "th", "lo", "my", "yue", " JA "])
def test_genai_diarization_rejects_non_space_languages(settings, language) -> None:
    settings = change(change(settings, "diarization", enabled=True), "asr", language=language)
    c = evaluate(deployment(settings, "genai-ov"), settings)
    assert c.runnable == "blocked"
    assert "genai_diarization_language" in codes(c)


@pytest.mark.parametrize("diar_device", ["cpu", "cuda", "xpu"])
def test_diarization_component_is_independent_of_asr(settings, diar_device) -> None:
    settings = change(settings, "diarization", enabled=True, device_preference=diar_device)
    d = deployment(settings, "wc-vulkan", diarization_device=diar_device)
    assert evaluate(d, settings).runnable == "ready"
    assert (
        evaluate(
            replace_observation(d, "diarization", state="unknown", reason_code="component_missing"),
            settings,
        ).runnable
        == "unknown"
    )
    assert (
        evaluate(
            replace_observation(d, "diarization", state="failed", reason_code="component_missing"),
            settings,
        ).runnable
        == "blocked"
    )
    worker = deployment(settings, "fw-cpu", execution_path="worker", diarization_device=diar_device)
    assert "worker_diarization_unsupported" in codes(evaluate(worker, settings))


def test_xpu_unverified_platform_is_not_supported(settings) -> None:
    settings = change(settings, "diarization", enabled=True)
    d = deployment(settings, diarization_device="xpu", platform="Linux")
    assert "diarization_platform_unsupported" in codes(evaluate(d, settings))


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("venv", "profile_missing"),
        ("venv", "dependency_lock_changed"),
        ("model", "model_missing"),
        ("runtime", "runtime_missing"),
        ("hardware", "hardware_missing"),
        ("native", "native_missing"),
    ],
)
def test_observed_failures_block(settings, kind, reason) -> None:
    d = replace_observation(
        deployment(settings, "wc-vulkan"), kind, state="failed", reason_code=reason
    )
    assert evaluate(d, settings).runnable == "blocked"
    assert reason in codes(evaluate(d, settings))


@pytest.mark.parametrize("state", ["unknown", "not_applicable", "failed"])
def test_required_states_are_not_conflated(settings, state) -> None:
    d = replace_observation(deployment(settings), "smoke", state=state, reason_code="smoke_missing")
    c = evaluate(d, settings)
    assert c.runnable == ("blocked" if state == "failed" else "unknown")
    native = next(o for o in c.observations if o.kind == "native")
    assert native.state == "not_applicable"


def test_imports_and_files_without_smoke_are_insufficient(settings) -> None:
    d = deployment(settings)
    d = d.model_copy(update={"observations": tuple(o for o in d.observations if o.kind != "smoke")})
    assert evaluate(d, settings).runnable == "unknown"
    assert "smoke_missing" in codes(evaluate(d, settings))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profile_generation", "generation-2"),
        ("profile_fingerprint", digest("new lock/path/extras")),
        ("runtime_fingerprint", digest("new driver/runtime")),
        ("python_abi", "cp311"),
        ("model_revision", "revision-2"),
        ("model_files_fingerprint", digest("new files")),
        ("native_fingerprint", digest("new native manifest")),
        ("hardware_fingerprint", digest("new hardware")),
        ("device_index", 1),
        ("platform", "Linux"),
        ("execution_path", "worker"),
    ],
)
def test_environment_changes_invalidate_evidence(settings, field, value) -> None:
    d = deployment(settings, "wc-vulkan")
    d = d.model_copy(update={"identity": d.identity.model_copy(update={field: value})})
    c = evaluate(d, settings)
    assert c.runnable != "ready"
    assert "fingerprint_changed" in codes(c)


def test_condition_changes_invalidate_smoke(settings) -> None:
    d = deployment(settings)
    other = change(settings, "asr", beam_size=settings.asr.beam_size + 1)
    assert "fingerprint_changed" in codes(evaluate(d, other))


@pytest.mark.parametrize("reason", ["probe_timeout", "probe_error", "cache_stale"])
def test_probe_unknown_reasons_survive(settings, reason) -> None:
    d = replace_observation(deployment(settings), "runtime", state="unknown", reason_code=reason)
    assert reason in codes(evaluate(d, settings))


def test_expired_and_future_observations_are_unknown(settings) -> None:
    d = deployment(settings)
    assert evaluate(d, settings, now=LATER).runnable == "unknown"
    assert "expired" in codes(evaluate(d, settings, now=LATER))
    assert "observation_from_future" in codes(evaluate(d, settings, now=NOW - timedelta(seconds=1)))
    o = d.observations[0].model_copy(update={"observed_at": None, "expires_at": None})
    assert normalize_observation(o, o.fingerprint, NOW).reason_code == "observation_time_missing"
    o = d.observations[0].model_copy(update={"expires_at": None})
    assert normalize_observation(o, o.fingerprint, NOW).reason_code == "cache_stale"


@pytest.mark.parametrize(
    "field", ["runtime_fingerprint", "hardware_fingerprint", "model_files_fingerprint"]
)
def test_incomplete_identity_cannot_pass_even_with_matching_smoke(settings, field) -> None:
    d = deployment(settings, **{field: None})
    assert evaluate(d, settings).runnable == "unknown"
    assert "evidence_scope_incomplete" in codes(evaluate(d, settings))


@pytest.mark.parametrize(
    "updates",
    [
        dict(device="gpu"),
        dict(compute_type="float16"),
        dict(model_family="ggml"),
        dict(device_index=1),
    ],
)
def test_unsupported_recipe_configurations_block(settings, updates) -> None:
    assert evaluate(deployment(settings, **updates), settings).runnable == "blocked"


@pytest.mark.parametrize("updates", [dict(input_count=2), dict(local_complete_model=False)])
def test_worker_model_and_single_input_limits(settings, updates) -> None:
    d = deployment(settings, execution_path="worker", **updates)
    assert evaluate(d, settings).runnable == "blocked"


def test_requested_route_is_never_copied_to_observed(settings) -> None:
    d = replace_observation(
        deployment(settings, "wc-ov-vk"),
        "smoke",
        initialized_route=None,
        encoder_observed="unknown",
        decoder_observed="unknown",
        encoder_device_index_observed=None,
        decoder_device_index_observed=None,
    )
    c = evaluate(d, settings)
    assert c.runnable == "unknown" and "smoke_route_unverified" in codes(c)
    assert next(o for o in d.observations if o.kind == "smoke").state == "passed"
    smoke = next(o for o in c.observations if o.kind == "smoke")
    assert smoke.initialized_route is None
    assert smoke.encoder_observed == smoke.decoder_observed == "unknown"
    assert c.requested_route.encoder == "openvino-gpu"


def test_auto_only_selects_ready_and_fixed_never_falls_back(settings) -> None:
    good = deployment(settings)
    bad = replace_observation(deployment(settings, "fw-cuda"), "smoke", state="unknown")
    inv = inventory(settings, good, bad)
    result = resolve(inv, policy(), now=NOW)
    assert result.selected.deployment.identity.recipe_id == "fw-cpu"
    assert result.confidence == "low" and "static_heuristic" in result.reason_codes
    bad_id = evaluate(bad, settings).id
    fixed = resolve(inv, policy(mode="fixed", fixed_candidate_id=bad_id), now=NOW)
    assert fixed.error == "fixed_candidate_unavailable" and fixed.selected is None
    assert fixed.preapproved == ()
    assert resolve(inv, policy(), now=LATER).error == "no_runnable_candidate"
    assert resolve(inventory(settings), policy(), now=NOW).error == "no_runnable_candidate"


def test_allowed_profiles_memory_limits_and_deadlines(settings) -> None:
    inv = inventory(settings, deployment(settings))
    for p, code in [
        (policy(allowed_profile_ids=["cuda"]), "profile_not_allowed"),
        (policy(max_memory_bytes=99), "memory_limit"),
        (policy(execution_deadline_seconds=30.0), "deadline_unverified"),
    ]:
        result = resolve(inv, p, now=NOW)
        assert result.selected is None and code in result.alternatives[0].reason_codes
    assert resolve(inv, policy(allowed_profile_ids=[]), now=NOW).selected is not None
    d = deployment(settings).model_copy(update={"available_memory_bytes": None})
    assert "memory_unknown" in codes(evaluate(d, settings))
    d = d.model_copy(update={"available_memory_bytes": 99})
    assert "memory_insufficient" in codes(evaluate(d, settings))


@pytest.mark.parametrize(
    ("section", "updates", "code"),
    [
        ("asr", dict(backend="whisper-cpp"), "backend_conflict"),
        ("asr", dict(model="tiny"), "model_conflict"),
        ("asr", dict(faster_whisper=dict(compute_type="float32")), "compute_conflict"),
        ("asr", dict(whisper_cpp=dict(threads=4)), "inactive_backend_settings"),
        ("asr", dict(word_timestamps="never"), "unsupported_setting"),
        ("asr", dict(glossary=["test"]), "unsupported_setting"),
    ],
)
def test_hard_setting_filters(settings, section, updates, code) -> None:
    settings = change(settings, section, **updates)
    c = evaluate(deployment(settings), settings)
    assert c.runnable == "blocked" and code in codes(c)


def test_determinism_preapproved_and_immutable_results(settings) -> None:
    one = deployment(settings, "fw-cuda", profile_id="cuda")
    two = deployment(settings, "fw-cuda", profile_id="intel")
    a = resolve(inventory(settings, one, two), policy(fallback="preapproved"), now=NOW)
    b = resolve(inventory(settings, two, one), policy(fallback="preapproved"), now=NOW)
    assert a == b and a.selected.id < a.preapproved[0].id
    assert a.selected.deployment.identity.profile_generation == "generation-1"
    frozen = a.to_wire()
    new = resolve(inventory(settings, two, revision=2), policy(), now=NOW)
    assert a.to_wire() == frozen and new.inventory_revision == 2
    old = evaluate(one, settings)
    assert next(c for c in inventory(settings, one, two).candidates if c.id == old.id) == old
    with pytest.raises(ValidationError):
        a.selected = None


def add_benchmark(
    d: DeploymentInventory, settings, elapsed: float, **updates: Any
) -> DeploymentInventory:
    value = BenchmarkEvidence.model_validate(
        dict(
            fingerprint=evidence_fingerprint(d.identity, settings),
            observed_at=NOW,
            expires_at=LATER,
            fixture_sha256=FP,
            duration_seconds=30.0,
            language="en",
            model_revision="revision-1",
            precision="int8",
            word_timestamps="always",
            diarization=False,
            threads=4,
            warmup_runs=1,
            runtime_version="runtime-1",
            elapsed_seconds=elapsed,
        )
        | updates
    )
    return d.model_copy(update={"benchmark": value})


def test_benchmarks_are_used_only_when_comparable(settings) -> None:
    cpu = add_benchmark(deployment(settings), settings, 1.0)
    cuda = add_benchmark(deployment(settings, "fw-cuda"), settings, 2.0)
    selected = resolve(inventory(settings, cpu, cuda), policy(), now=NOW)
    assert selected.selected.deployment.identity.recipe_id == "fw-cpu"
    assert selected.confidence == "medium"
    assert selected.evidence_kinds == ("smoke", "benchmark")
    different = add_benchmark(
        deployment(settings, "fw-cuda"), settings, 0.1, fixture_sha256=digest("other")
    )
    result = resolve(inventory(settings, cpu, different), policy(), now=NOW)
    assert result.confidence == "low" and "benchmark_incomparable" in result.reason_codes
    assert "static_heuristic" in result.reason_codes


@pytest.mark.parametrize(
    "updates",
    [
        dict(precision="float32"),
        dict(model_revision="other"),
        dict(language="ja"),
        dict(expires_at=NOW),
        dict(fingerprint=digest("stale")),
        dict(diarization=True),
    ],
)
def test_benchmark_mismatch_never_boosts_confidence(settings, updates) -> None:
    d = add_benchmark(deployment(settings), settings, 1.0, **updates)
    assert resolve(inventory(settings, d), policy(), now=NOW).confidence == "low"


def test_contract_validation_privacy_and_schema(settings, tmp_path) -> None:
    inv = inventory(settings, deployment(settings))
    result = resolve(inv, policy(), now=NOW)
    for obj in (inv, result, recipe_catalog()):
        assert "large-v3-turbo" not in repr(obj)
        assert obj.public_projection()["schema_version"] == 1
        with pytest.raises(ValidationError):
            obj.model_copy(update={"unexpected": "PRIVATE_VALUE"})
    schema = json.loads(schema_bytes())
    jsonschema.Draft202012Validator.check_schema(schema)
    validator = jsonschema.Draft202012Validator(schema)
    validator.validate(inv.to_wire())
    validator.validate(result.to_wire())
    validator.validate(recipe_catalog().to_wire())
    artifact = ROOT / "docs/architecture/b4/capabilities.schema.json"
    assert artifact.read_bytes() == schema_bytes() == schema_bytes()
    command = [sys.executable, "-m", "utteran_contracts.capability_schema", "--check", "--output"]
    assert subprocess.run([*command, str(artifact)], capture_output=True).returncode == 0
    stale = tmp_path / "schema.json"
    stale.write_text("old", encoding="utf-8")
    assert subprocess.run([*command, str(stale)], capture_output=True).returncode == 1
    assert stale.read_text(encoding="utf-8") == "old"
    with pytest.raises(ValidationError):
        inv.model_copy(update={"schema_version": True})
    with pytest.raises(ValidationError):
        inv.candidates[0].deployment.identity.model_copy(update={"profile_id": "/private/path"})
    with pytest.raises(ValidationError):
        inv.model_copy(update={"candidates": (inv.candidates[0], inv.candidates[0])})
    with pytest.raises(ValidationError):
        policy(mode="fixed", fixed_candidate_id="test", fallback="preapproved")


def test_readonly_collector_never_claims_hardware_or_smoke(settings, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "uv.lock").write_text("lock", encoding="utf-8")
    profile = tmp_path / "profile"
    profile.mkdir()
    data = collect_managed_deployments(
        "cpu",
        settings,
        "cli",
        now=NOW,
        profile_root=profile,
        repo_root=repo,
        model_root=tmp_path / "models",
        native_root=tmp_path / "native",
    )
    assert data
    assert all(c.runnable != "ready" for c in inventory(settings, *data).candidates)
    assert all("profile_manifest_missing" in codes(evaluate(d, settings)) for d in data)
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    collect_managed_deployments(
        "cpu",
        settings,
        "cli",
        now=NOW,
        profile_root=profile,
        repo_root=repo,
        model_root=tmp_path / "models",
        native_root=tmp_path / "native",
    )
    assert before == sorted(str(p) for p in tmp_path.rglob("*"))


def test_provider_profile_scope_and_empty_inventory(settings) -> None:
    request = InventoryRefreshRequest(
        schema_version=1, profile_id="cuda", settings=settings, execution_path="cli", revision=2
    )
    with pytest.raises(ValueError, match="profile_scope_mismatch"):
        refresh_inventory(request, now=NOW, provider=lambda *_: (deployment(settings),))
    inv = refresh_inventory(request, now=NOW, provider=lambda *_: ())
    assert inv.revision == 2 and inv.candidates == ()


def test_opt_in_gui_adapter_sanitizes_errors_and_checks_scope(
    settings, tmp_path, monkeypatch
) -> None:
    cli = CliAdapter(tmp_path)
    root = tmp_path / "profile"
    (root / "bin").mkdir(parents=True)
    (root / "Scripts").mkdir()
    (root / "bin/python").touch()
    (root / "Scripts/python.exe").touch()
    info = ProfileInfo("cpu", root, True, root / "utteran", compatible=True)
    monkeypatch.setattr(cli, "profile_info", lambda _: info)
    calls = []
    request = InventoryRefreshRequest(
        schema_version=1, profile_id="cpu", settings=settings, execution_path="cli", revision=1
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        response = InventoryRefreshResult(
            schema_version=1,
            reason_code="supported",
            inventory=inventory(settings, deployment(settings)),
        )
        return subprocess.CompletedProcess(
            command, 0, json.dumps(response.to_wire()).encode(), b"PRIVATE_TRACE"
        )

    adapter = CapabilityInventoryAdapter(cli, runner=run)
    result = adapter.refresh(request)
    assert result.inventory is not None
    command, kwargs = calls[0]
    assert command[-2:] == ["utteran.capabilities.bridge", "refresh"]
    assert "-I" in command and "-B" in command
    assert "initial_prompt" not in str(command) and b"initial_prompt" in kwargs["input"]
    assert "PRIVATE_TRACE" not in repr(result)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("PRIVATE", 1, stderr="PRIVATE_TRACE")

    result = CapabilityInventoryAdapter(cli, runner=timeout).refresh(request)
    assert result.inventory is None and result.reason_code == "probe_timeout"
    assert "PRIVATE" not in str(result)
    monkeypatch.setattr(
        cli, "profile_info", lambda _: ProfileInfo("cpu", root, False, root / "missing")
    )
    assert adapter.refresh(request).inventory is None


def test_gui_adapter_imports_no_core_or_inference() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            """
import sys
import utteran_gui.capability_inventory
assert not any(n in sys.modules for n in (
    'utteran', 'torch', 'openvino', 'ctranslate2', 'faster_whisper', 'pyannote'))
""",
        ],
        capture_output=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    tree = ast.parse((ROOT / "src/utteran_gui/capability_inventory.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "utteran"
        for n in ast.walk(tree)
    )


def test_import_schema_resolve_and_collection_do_not_write_or_execute(settings, tmp_path) -> None:
    request = InventoryRefreshRequest(
        schema_version=1, profile_id="cpu", settings=settings, execution_path="cli", revision=1
    )
    fixture = tmp_path / "request.json"
    fixture.write_text(json.dumps(request.to_wire()), encoding="utf-8")
    code = r"""
import sys, json, importlib.abc
from datetime import UTC, datetime
blocked = {'torch','torchaudio','openvino','openvino_genai',
           'faster_whisper','ctranslate2','pyannote'}
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('heavy import')
sys.meta_path.insert(0, Guard())
def audit(event, args):
    if event.startswith(('subprocess.', 'socket.connect', 'socket.bind', 'os.system', 'os.fork')):
        raise AssertionError('execution side effect')
    if event in {'os.mkdir','os.remove','os.rename','os.rmdir','os.unlink'}:
        raise AssertionError('filesystem mutation')
    if event == 'open' and (args[2] & (64 | 512 | 1024 | 1 | 2)):
        raise AssertionError('file write')
sys.addaudithook(audit)
from utteran_contracts.capability_schema import schema_bytes
from utteran_contracts.capabilities import InventoryRefreshRequest
from utteran_contracts import Policy
from utteran.capabilities.collector import refresh_inventory
from utteran.capabilities.resolver import resolve
from pathlib import Path
request = InventoryRefreshRequest.model_validate_json(Path(sys.argv[1]).read_bytes())
inv = refresh_inventory(request, now=datetime.now(UTC))
assert all(c.runnable != 'ready' for c in inv.candidates)
policy = Policy(schema_version=1, id='p', revision=1, name='p', mode='auto',
 fixed_candidate_id=None, allowed_profile_ids=(), objective='speed', max_memory_bytes=None,
 fallback='none', execution_deadline_seconds=None)
assert resolve(inv, policy, now=datetime.now(UTC)).error == 'no_runnable_candidate'
assert schema_bytes()
assert not blocked.intersection(sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code, str(fixture)],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["request.json"]


def test_bridge_has_no_legacy_logging_or_raw_errors(settings) -> None:
    command = [sys.executable, "-B", "-m", "utteran.capabilities.bridge"]
    completed = subprocess.run([*command, "recipes"], capture_output=True, timeout=20)
    assert json.loads(completed.stdout) == recipe_catalog().to_wire()
    assert completed.stderr == b""
    failed = subprocess.run(
        [*command, "refresh"], input=b'{"PRIVATE_TOKEN":"secret"}', capture_output=True, timeout=20
    )
    assert json.loads(failed.stdout)["reason_code"] == "bridge_invalid"
    assert b"PRIVATE" not in failed.stdout and failed.stderr == b""


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("valid", "supported"),
        ("lock", "dependency_lock_changed"),
        ("path", "profile_path_changed"),
        ("extras", "profile_extras_changed"),
        ("invalid", "profile_manifest_invalid"),
        ("missing_lock", "current_lock_missing"),
    ],
)
def test_collector_manifest_compatibility_matches_existing_policy(
    settings, tmp_path, mutation, expected
):
    import hashlib

    from utteran.capabilities.collector import _profile_state
    from utteran.profiles import PROFILE_EXTRAS

    repo = tmp_path / "repo"
    root = tmp_path / "profile"
    repo.mkdir()
    root.mkdir()
    lock = repo / "uv.lock"
    lock.write_text("lock-content", encoding="utf-8")
    manifest = dict(
        schema_version=1,
        lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
        extras=list(PROFILE_EXTRAS["cpu"]),
        venv_path=str(root.resolve()),
    )
    if mutation == "lock":
        manifest["lock_sha256"] = "old"
    if mutation == "path":
        manifest["venv_path"] = str(tmp_path / "old-profile")
    if mutation == "extras":
        manifest["extras"] = ["cuda"]
    if mutation == "invalid":
        manifest["schema_version"] = 2
    if mutation == "missing_lock":
        lock.unlink()
    (root / ".utteran-profile.json").write_text(json.dumps(manifest), encoding="utf-8-sig")
    assert _profile_state("cpu", root, lock)[0] == expected
    if mutation != "invalid":
        cli = CliAdapter(repo)
        _, reason = cli._profile_compatibility("cpu", root)
        assert (reason or "supported") == expected


def test_complete_managed_model_is_not_smoke(settings, tmp_path):
    import hashlib

    from utteran.models.catalog import get_model
    from utteran.models.manager import ModelManager
    from utteran.profiles import PROFILE_EXTRAS

    repo = tmp_path / "repo"
    root = tmp_path / "profile"
    repo.mkdir()
    root.mkdir()
    lock = repo / "uv.lock"
    lock.write_text("lock", encoding="utf-8")
    (root / ".utteran-profile.json").write_text(
        json.dumps(
            dict(
                schema_version=1,
                lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
                extras=list(PROFILE_EXTRAS["cpu"]),
                venv_path=str(root.resolve()),
            )
        ),
        encoding="utf-8",
    )
    models = tmp_path / "models"
    model = ModelManager(models).managed_path(get_model("large-v3-turbo", backend="faster-whisper"))
    model.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
        (model / name).write_bytes(b"synthetic-not-an-inference-model")
    kwargs = dict(
        now=NOW,
        profile_root=root,
        repo_root=repo,
        model_root=models,
        native_root=tmp_path / "native",
    )
    first = collect_managed_deployments("cpu", settings, "worker", **kwargs)
    d = next(
        d
        for d in first
        if d.identity.recipe_id == "fw-cpu" and d.identity.model_id == "large-v3-turbo"
    )
    c = evaluate(d, settings)
    assert d.identity.local_complete_model
    assert c.runnable == "unknown" and "smoke_missing" in codes(c)
    assert next(o for o in c.observations if o.kind == "venv").state == "passed"
    (model / "model.bin").write_bytes(b"updated-synthetic-model")
    second = collect_managed_deployments("cpu", settings, "worker", **kwargs)
    other = next(
        x
        for x in second
        if x.identity.recipe_id == "fw-cpu" and x.identity.model_id == "large-v3-turbo"
    )
    assert d.identity.model_revision != other.identity.model_revision
    assert d.identity.model_files_fingerprint != other.identity.model_files_fingerprint


def test_model_language_and_diarization_fixed_constraints(settings):
    japanese = change(settings, "asr", language="ja")
    assert "model_language_conflict" in codes(
        evaluate(deployment(japanese, english_only=True), japanese)
    )
    diar = change(settings, "diarization", enabled=True, device_preference="cuda")
    d = deployment(diar, diarization_device="cpu", diarization_model="other-model")
    assert {"diarization_model_conflict", "diarization_device_conflict"} <= codes(evaluate(d, diar))


def test_duplicate_and_unknown_observation_data_is_rejected(settings):
    d = deployment(settings)
    with pytest.raises(ValidationError):
        d.model_copy(update={"observations": (*d.observations, d.observations[0])})
    with pytest.raises(ValidationError):
        d.observations[0].model_copy(update={"reason_code": "PRIVATE_RAW_STDERR"})
    with pytest.raises(ValidationError):
        d.observations[0].model_copy(update={"source": "/private/path"})
    with pytest.raises(ValidationError):
        d.identity.model_copy(update={"smoke_version": 2})
    with pytest.raises(ValueError):
        evaluate(d, settings, now=NOW.replace(tzinfo=None))


def test_fixed_success_stale_missing_and_preapproved_are_explicit(settings):
    inv = inventory(settings, deployment(settings))
    p = policy(mode="fixed", fixed_candidate_id=inv.candidates[0].id)
    assert resolve(inv, p, now=NOW).selected is not None
    assert resolve(inv, p, now=LATER).error == "fixed_candidate_unavailable"
    assert (
        resolve(inv, policy(mode="fixed", fixed_candidate_id="missing"), now=NOW).selected is None
    )
    assert resolve(inv, policy(fallback="none"), now=NOW).preapproved == ()


def test_adapter_rejects_other_profile_reports(settings, tmp_path, monkeypatch):
    cli = CliAdapter(tmp_path)
    adapter = CapabilityInventoryAdapter(cli)
    result = InventoryRefreshResult(
        schema_version=1,
        reason_code="supported",
        inventory=inventory(settings, deployment(settings, profile_id="intel")),
    )
    monkeypatch.setattr(adapter, "_read", lambda *_: result.to_wire())
    request = InventoryRefreshRequest(
        schema_version=1, profile_id="cpu", settings=settings, execution_path="cli", revision=1
    )
    assert adapter.refresh(request).reason_code == "profile_scope_mismatch"
    monkeypatch.setattr(adapter, "_read", lambda *_: {"private": "PRIVATE_TRACE"})
    assert adapter.refresh(request).reason_code == "bridge_invalid"


def test_fixed_backend_and_model_exclusions_are_not_candidates(settings):
    fixed = change(settings, "asr", backend="faster-whisper", model="large-v3-turbo")
    inv = inventory(
        fixed, deployment(fixed), deployment(fixed, "wc-vulkan"), deployment(fixed, model_id="tiny")
    )
    assert len(inv.candidates) == 1
    assert len(inv.exclusions) == 2
    assert {code for item in inv.exclusions for code in item.reason_codes} == {
        "backend_conflict",
        "model_conflict",
    }
    result = resolve(inv, policy(), now=NOW)
    assert result.selected is not None and result.exclusions == inv.exclusions


def test_smoke_observations_cannot_claim_success_on_another_route(settings):
    d = deployment(settings, "wc-ov-vk")
    wrong = replace_observation(d, "smoke", initialized_route=get_recipe("wc-cpu").requested_route)
    assert evaluate(wrong, settings).runnable == "blocked"
    assert "smoke_route_mismatch" in codes(evaluate(wrong, settings))
    wrong = replace_observation(d, "smoke", encoder_observed="cpu")
    assert "smoke_route_mismatch" in codes(evaluate(wrong, settings))
    observed = replace_observation(
        d, "smoke", encoder_observed="openvino-gpu", decoder_observed="vulkan"
    )
    assert evaluate(observed, settings).runnable == "ready"
    # backend-default may initialize on CPU; selected-device resolves to the
    # explicitly requested device. Neither value is fabricated by the evaluator.
    genai = deployment(settings, "genai-ov")
    genai = replace_observation(
        genai, "smoke", initialized_route=get_recipe("fw-cpu").requested_route
    )
    assert evaluate(genai, settings).runnable == "ready"


@pytest.mark.parametrize(
    "updates",
    [
        dict(word_timestamps="always", language="ja"),
        dict(openvino_genai=dict(max_new_tokens=10)),
        dict(condition_on_previous_text=True),
    ],
)
def test_genai_does_not_claim_unimplemented_or_incompatible_parameters(settings, updates):
    settings = change(settings, "asr", **updates)
    assert "unsupported_setting" in codes(evaluate(deployment(settings, "genai-ov"), settings))


@pytest.mark.parametrize("device", ["cpu", "gpu", "npu"])
def test_genai_explicit_device_route_observation(settings, device):
    from utteran_contracts.capabilities import RequestedRoute

    d = deployment(settings, "genai-ov", device=device)
    actual = RequestedRoute.model_validate(
        dict(native_variant=None, encoder=device, decoder=device)
    )
    d = replace_observation(
        d, "smoke", initialized_route=actual, encoder_observed=device, decoder_observed=device
    )
    assert evaluate(d, settings).runnable == "ready"


def test_unknown_platform_is_not_an_observed_failure(settings):
    candidate = evaluate(deployment(settings, platform="unknown"), settings)
    assert candidate.runnable == "unknown"
    assert (
        next(o for o in candidate.observations if o.kind == "software_support").state == "unknown"
    )


@pytest.mark.parametrize("recipe_id", [r.id for r in RECIPES])
def test_passed_smoke_without_measured_route_is_unverified(settings, recipe_id):
    d = replace_observation(
        deployment(settings, recipe_id),
        "smoke",
        initialized_route=None,
        encoder_observed="unknown",
        decoder_observed="unknown",
        encoder_device_index_observed=None,
        decoder_device_index_observed=None,
    )
    before = d.to_wire()
    c = evaluate(d, settings)
    assert c.runnable == "unknown" and "smoke_route_unverified" in codes(c)
    assert next(o for o in c.observations if o.kind == "smoke").state == "unknown"
    assert d.to_wire() == before


@pytest.mark.parametrize("component", ["encoder", "decoder"])
def test_partial_cuda_observation_does_not_verify_route(settings, component):
    d = replace_observation(
        deployment(settings, "fw-cuda"), "smoke", **{component + "_observed": "unknown"}
    )
    assert evaluate(d, settings).runnable == "unknown"
    # A positive CPU observation is a mismatch even if the other component is missing.
    other = "decoder" if component == "encoder" else "encoder"
    d = replace_observation(d, "smoke", **{other + "_observed": "cpu"})
    assert "smoke_route_mismatch" in codes(evaluate(d, settings))


@pytest.mark.parametrize("component", ["encoder", "decoder"])
def test_accelerator_indices_are_observed_not_inferred(settings, component):
    d = replace_observation(
        deployment(settings, "fw-cuda", device_index=1),
        "smoke",
        **{component + "_device_index_observed": None},
    )
    assert "smoke_route_unverified" in codes(evaluate(d, settings))
    d = replace_observation(d, "smoke", **{component + "_device_index_observed": 0})
    assert "smoke_route_mismatch" in codes(evaluate(d, settings))
    d = replace_observation(d, "smoke", **{component + "_device_index_observed": 1})
    assert evaluate(d, settings).runnable == "ready"


@pytest.mark.parametrize(
    "recipe_id,component",
    [
        ("wc-cpu", "encoder"),
        ("wc-cpu", "decoder"),
        ("wc-vulkan", "decoder"),
        ("wc-ov", "encoder"),
        ("wc-ov-vk", "encoder"),
        ("wc-ov-vk", "decoder"),
    ],
)
def test_native_route_requires_variant_and_required_components(settings, recipe_id, component):
    d = deployment(settings, recipe_id)
    assert evaluate(d, settings).runnable == "ready"
    missing = replace_observation(d, "smoke", initialized_route=None)
    assert "smoke_route_unverified" in codes(evaluate(missing, settings))
    missing = replace_observation(d, "smoke", **{component + "_observed": "unknown"})
    assert "smoke_route_unverified" in codes(evaluate(missing, settings))
    wrong = replace_observation(d, "smoke", initialized_route=get_recipe("fw-cpu").requested_route)
    assert "smoke_route_mismatch" in codes(evaluate(wrong, settings))


@pytest.mark.parametrize("recipe_id", ["wc-vulkan", "wc-ov"])
def test_backend_default_is_not_a_required_component_observation(settings, recipe_id):
    d = deployment(settings, recipe_id)
    assert evaluate(d, settings).runnable == "ready"
    component = "encoder" if recipe_id == "wc-vulkan" else "decoder"
    assert (
        getattr(next(o for o in d.observations if o.kind == "smoke"), component + "_observed")
        == "unknown"
    )


@pytest.mark.parametrize("device", ["cpu", "gpu", "npu"])
def test_genai_selected_device_is_measured_for_each_component(settings, device):
    d = deployment(settings, "genai-ov", device=device)
    assert evaluate(d, settings).runnable == "ready"
    missing = replace_observation(d, "smoke", encoder_observed="unknown")
    assert evaluate(missing, settings).runnable == "unknown"
    wrong = replace_observation(d, "smoke", decoder_observed="cuda")
    assert evaluate(wrong, settings).runnable == "blocked"


@pytest.mark.parametrize(
    "updates,now,reason",
    [
        (dict(expires_at=NOW + timedelta(seconds=1)), LATER, "expired"),
        (dict(fingerprint=digest("other-scope")), NOW, "fingerprint_changed"),
        (dict(observed_at=NOW + timedelta(seconds=1)), NOW, "observation_from_future"),
        (dict(observed_at=None, expires_at=None), NOW, "observation_time_missing"),
    ],
)
def test_verified_route_cannot_revive_invalid_evidence(settings, updates, now, reason):
    d = replace_observation(deployment(settings, "fw-cuda"), "smoke", **updates)
    c = evaluate(d, settings, now=now)
    assert c.runnable == "unknown" and reason in codes(c)
    smoke = next(o for o in c.observations if o.kind == "smoke")
    assert smoke.encoder_device_index_observed is None
    assert "smoke_route_unverified" not in codes(c)


def test_unverified_route_resolution_and_failure_precedence(settings):
    d = replace_observation(deployment(settings, "fw-cuda"), "smoke", decoder_observed="unknown")
    inv = inventory(settings, d, deployment(settings))
    result = resolve(inv, policy(), now=NOW)
    assert result.selected.deployment.identity.recipe_id == "fw-cpu"
    alternative = next(a for a in result.alternatives if a.candidate.id == evaluate(d, settings).id)
    assert not alternative.eligible and "smoke_route_unverified" in alternative.reason_codes
    result = resolve(
        inv, policy(mode="fixed", fixed_candidate_id=evaluate(d, settings).id), now=NOW
    )
    assert result.error == "fixed_candidate_unavailable" and result.preapproved == ()
    failed = replace_observation(d, "model", state="failed", reason_code="model_missing")
    assert evaluate(failed, settings).runnable == "blocked"
    failed = replace_observation(d, "smoke", state="failed", reason_code="probe_error")
    assert "probe_error" in codes(evaluate(failed, settings))
    assert "smoke_route_unverified" not in codes(evaluate(failed, settings))


def test_openvino_gpu_label_is_not_generic_gpu(settings):
    d = replace_observation(deployment(settings, "wc-ov"), "smoke", encoder_observed="gpu")
    assert "smoke_route_mismatch" in codes(evaluate(d, settings))
