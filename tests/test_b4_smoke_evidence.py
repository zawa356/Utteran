"""B4 private smoke evidence: format, fail-closed reading, invalidation, overlay.

All records here are synthetic and written only below ``tmp_path``. None of them is a
real inference result; production code has no path that writes a record without running
the smoke child (see test_b4_smoke_executor.py).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from utteran.capabilities.evaluator import digest, evaluate_candidate, evidence_fingerprint
from utteran.capabilities.evidence import (
    EVIDENCE_RECORD_LIMIT,
    EVIDENCE_TTL,
    EvidenceWriteError,
    SmokeEvidence,
    apply_smoke_evidence,
    evidence_directory,
    inspect_evidence,
    read_evidence,
    seal_evidence,
    smoke_identity,
    write_evidence,
)
from utteran.capabilities.fingerprints import hardware_fingerprint, runtime_fingerprint
from utteran.capabilities.recipes import get_recipe
from utteran_contracts import ProcessingSettings
from utteran_contracts.capabilities import (
    CapabilityObservation,
    DeploymentInventory,
    ExecutionIdentity,
)
from utteran_paths import DEV_MARKER_CONTENT, DEV_MARKER_NAME, DataPaths, is_marked_dev_root

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)
RUNTIME = digest("synthetic runtime identity")
HARDWARE = digest("synthetic hardware identity")
FIXTURE = digest("synthetic fixture")


def fixed(device: str) -> tuple[str | None, str | None]:
    """Synthetic fingerprints: CPU fully known, CUDA unknown (as in production)."""
    return (RUNTIME, HARDWARE) if device == "cpu" else (None, None)


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


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "dev-root"
    path.mkdir()
    (path / DEV_MARKER_NAME).write_text(DEV_MARKER_CONTENT, encoding="utf-8")
    return path


@pytest.fixture
def directory(root: Path) -> Path:
    value = evidence_directory(DataPaths(root=root, project_root=root))
    assert value is not None
    return value


def base_deployment(recipe_id: str = "fw-cpu", **updates: Any) -> DeploymentInventory:
    """Shape of a standard-collector worker deployment: fingerprints null, no smoke."""
    recipe = get_recipe(recipe_id)
    identity = ExecutionIdentity.model_validate(
        dict(
            recipe_id=recipe_id,
            recipe_revision=1,
            adapter_version="b4.1",
            smoke_version=1,
            profile_id="cpu",
            profile_generation="generation-1",
            profile_fingerprint=digest("profile"),
            platform="Linux",
            python_abi="cpython-312",
            runtime_fingerprint=None,
            hardware_fingerprint=None,
            model_id="tiny",
            model_family="ctranslate2",
            english_only=False,
            model_revision="local-" + digest("files"),
            model_files_fingerprint=digest("files"),
            native_fingerprint=None,
            device=recipe.devices[0],
            device_index=0,
            compute_type=recipe.compute_types[0],
            execution_path="worker",
            local_complete_model=True,
            input_count=1,
            diarization_device=None,
            diarization_device_index=None,
            diarization_model=None,
            diarization_fingerprint=None,
        )
        | updates
    )
    return DeploymentInventory(
        identity=identity, observations=(), required_memory_bytes=None, available_memory_bytes=None
    )


def collected(settings: ProcessingSettings, recipe_id: str = "fw-cpu", **updates: Any):
    """Base deployment with the collector's own venv/model/native observations."""
    d = base_deployment(recipe_id, **updates)
    fp = evidence_fingerprint(d.identity, settings)
    facts = [
        ("venv", "profile-manifest", "passed", "supported"),
        ("model", "model-files", "passed", "supported"),
        ("native", "native-manifest", "failed", "native_missing"),
    ]
    return d.model_copy(
        update={
            "observations": tuple(
                CapabilityObservation.model_validate(
                    dict(
                        kind=k,
                        source=s,
                        state=st,
                        reason_code=c,
                        fingerprint=fp,
                        observed_at=NOW,
                        expires_at=NOW + timedelta(minutes=5),
                    )
                )
                for k, s, st, c in facts
            )
        }
    )


def scope_for(settings: ProcessingSettings, deployment: DeploymentInventory) -> str:
    identity = smoke_identity(deployment.identity, fixed)
    assert identity is not None
    return evidence_fingerprint(identity, settings)


def record(scope: str, recipe_id: str = "fw-cpu", **updates: Any) -> SmokeEvidence:
    device = "cpu" if recipe_id == "fw-cpu" else "cuda"
    fields: dict[str, Any] = dict(
        scope=scope,
        recipe_id=recipe_id,
        state="passed",
        outcome="passed",
        observed_at=NOW,
        expires_at=NOW + EVIDENCE_TTL,
        encoder_observed=device,
        decoder_observed=device,
        encoder_device_index_observed=None if device == "cpu" else 0,
        decoder_device_index_observed=None if device == "cpu" else 0,
        required_memory_bytes=500_000_000 if device == "cpu" else None,
        available_memory_bytes=8_000_000_000 if device == "cpu" else None,
        fixture_sha256=FIXTURE,
    )
    fields.update(updates)
    return seal_evidence(**fields)


def overlay(settings, deployment, directory, now=NOW):
    (result,) = apply_smoke_evidence(
        (deployment,), settings, now=now, directory=directory, fingerprints=fixed
    )
    return result


def candidate(settings, deployment):
    return evaluate_candidate(deployment, settings, "worker", now=NOW + timedelta(minutes=1))


def issue_codes(c) -> set[str]:
    return {i.code for i in c.issues}


# --------------------------------------------------------------------------- paths/marker


def test_evidence_directory_requires_explicit_marked_root(tmp_path: Path, root: Path) -> None:
    assert DataPaths(root=None, project_root=tmp_path).capability_evidence is None
    assert evidence_directory(DataPaths(root=None, project_root=tmp_path)) is None
    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    assert evidence_directory(DataPaths(root=unmarked, project_root=tmp_path)) is None
    (unmarked / DEV_MARKER_NAME).write_text(DEV_MARKER_CONTENT + " ", encoding="utf-8")
    assert not is_marked_dev_root(unmarked)
    assert evidence_directory(DataPaths(root=unmarked, project_root=tmp_path)) is None
    value = evidence_directory(DataPaths(root=root, project_root=tmp_path))
    assert value == root / "cache" / "capability-evidence-v1"
    assert not value.exists()  # resolving never creates anything


def test_marker_constants_are_shared_with_g09_guard() -> None:
    from utteran_gui import instance_guard

    assert instance_guard.DEV_MARKER_NAME == DEV_MARKER_NAME
    assert instance_guard.DEV_MARKER_CONTENT == DEV_MARKER_CONTENT


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_symlinked_marker_is_not_a_marked_root(tmp_path: Path) -> None:
    real = tmp_path / "marker"
    real.write_text(DEV_MARKER_CONTENT, encoding="utf-8")
    root = tmp_path / "r"
    root.mkdir()
    (root / DEV_MARKER_NAME).symlink_to(real)
    assert not is_marked_dev_root(root)


# --------------------------------------------------------------------------- record format


def test_record_round_trip_and_value_free_content(settings, directory) -> None:
    scope = scope_for(settings, base_deployment())
    write_evidence(directory, record(scope))
    path = directory / f"{scope}.json"
    raw = path.read_text(encoding="utf-8")
    assert read_evidence(directory, scope, now=NOW + timedelta(hours=1)) == record(scope)
    # No settings body, prompt, path or transcript; digests only.
    assert "initial_prompt" not in raw and str(directory) not in raw and "tiny" not in raw
    assert set(json.loads(raw)) == set(SmokeEvidence.model_fields)
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0
    assert sorted(p.name for p in directory.iterdir()) == [f"{scope}.json"]


@pytest.mark.parametrize(
    "updates",
    [
        dict(expires_at=NOW),
        dict(expires_at=NOW + EVIDENCE_TTL + timedelta(seconds=1)),
        dict(observed_at=NOW.replace(tzinfo=None)),
        dict(state="passed", outcome="model_missing"),
        dict(state="failed", outcome="passed"),
        dict(encoder_device_index_observed=-1),
        dict(required_memory_bytes=0),
    ],
)
def test_record_validation_rejects_inconsistent_values(settings, updates) -> None:
    with pytest.raises(ValueError):
        record(scope_for(settings, base_deployment()), **updates)


# --------------------------------------------------------------------------- fail-closed reading


def write_raw(directory: Path, scope: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{scope}.json"
    path.write_bytes(data)
    return path


def snapshot(path: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()}


@pytest.mark.parametrize(
    "mutate,status",
    [
        (lambda w: w[:-20], "invalid"),  # partial write
        (lambda w: b"\xff\xfe" + w, "invalid"),  # not UTF-8
        (lambda w: w.replace(b'"version":1', b'"version":2'), "invalid"),  # unknown version
        (lambda w: w.replace(b'"state":"passed"', b'"state":"pending"'), "invalid"),  # tamper
        (lambda w: w[:-1] + b',"extra":1}', "invalid"),
        (lambda w: w + b" " * 9000, "too_large"),
        (lambda w: b'{"a":1,"a":2}', "invalid"),
        (lambda w: b"[]", "invalid"),
    ],
)
def test_corrupt_records_are_rejected_without_modification(
    settings, directory, mutate, status
) -> None:
    scope = scope_for(settings, base_deployment())
    wire = json.dumps(record(scope).to_wire(), sort_keys=True, separators=(",", ":")).encode()
    write_raw(directory, scope, mutate(wire))
    before = snapshot(directory)
    assert inspect_evidence(directory, scope, now=NOW)[0] == status
    assert read_evidence(directory, scope, now=NOW) is None
    assert snapshot(directory) == before


def test_resealed_but_misnamed_record_is_scope_mismatch(settings, directory) -> None:
    scope = scope_for(settings, base_deployment())
    other = digest("another scope")
    write_raw(directory, scope, json.dumps(record(other).to_wire()).encode())
    assert inspect_evidence(directory, scope, now=NOW)[0] == "scope_mismatch"


@pytest.mark.parametrize("state,outcome", [("pending", "running"), ("failed", "inference_failed")])
def test_only_passed_records_are_adopted(settings, directory, state, outcome) -> None:
    scope = scope_for(settings, base_deployment())
    write_evidence(
        directory,
        record(
            scope,
            state=state,
            outcome=outcome,
            encoder_observed="unknown",
            decoder_observed="unknown",
        ),
    )
    assert inspect_evidence(directory, scope, now=NOW)[0] == "not_passed"


def test_time_window_is_checked_and_never_extended(settings, directory) -> None:
    scope = scope_for(settings, base_deployment())
    write_evidence(directory, record(scope))
    before = snapshot(directory)
    assert inspect_evidence(directory, scope, now=NOW - timedelta(seconds=1))[0] == "future"
    assert inspect_evidence(directory, scope, now=NOW + EVIDENCE_TTL)[0] == "expired"
    assert (
        inspect_evidence(directory, scope, now=NOW + EVIDENCE_TTL - timedelta(seconds=1))[0]
        == "adopted"
    )
    assert snapshot(directory) == before  # reading never touches expiry or files
    with pytest.raises(ValueError):
        inspect_evidence(directory, scope, now=NOW.replace(tzinfo=None))


def test_absent_and_unsafe_locations(settings, directory, tmp_path) -> None:
    scope = scope_for(settings, base_deployment())
    assert inspect_evidence(directory, scope, now=NOW)[0] == "absent"
    assert inspect_evidence(directory, "not-a-digest", now=NOW)[0] == "invalid"
    (directory / f"{scope}.json").mkdir(parents=True)
    assert inspect_evidence(directory, scope, now=NOW)[0] == "unsafe"


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges")
def test_symlinked_record_and_directory_are_unsafe(settings, root, directory, tmp_path) -> None:
    scope = scope_for(settings, base_deployment())
    outside = tmp_path / "outside"
    outside.mkdir()
    write_evidence(outside, record(scope))
    directory.parent.mkdir(parents=True)
    directory.symlink_to(outside, target_is_directory=True)
    assert inspect_evidence(directory, scope, now=NOW)[0] == "unsafe"
    with pytest.raises(EvidenceWriteError):
        write_evidence(directory, record(scope))
    directory.unlink()
    directory.mkdir()
    (directory / f"{scope}.json").symlink_to(outside / f"{scope}.json")
    assert inspect_evidence(directory, scope, now=NOW)[0] == "unsafe"
    with pytest.raises(EvidenceWriteError):
        write_evidence(directory, record(scope))


def test_writer_refuses_new_scopes_beyond_limit(settings, directory) -> None:
    for index in range(EVIDENCE_RECORD_LIMIT):
        write_evidence(directory, record(digest(index)))
    with pytest.raises(EvidenceWriteError) as caught:
        write_evidence(directory, record(digest("one more")))
    assert caught.value.code == "evidence_limit"
    write_evidence(directory, record(digest(0), state="failed", outcome="inference_failed"))
    assert len(list(directory.glob("*.json"))) == EVIDENCE_RECORD_LIMIT


def test_concurrent_replacement_never_exposes_partial_records(settings, directory) -> None:
    scope = scope_for(settings, base_deployment())
    write_evidence(directory, record(scope))
    seen: set[str] = set()
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            seen.add(inspect_evidence(directory, scope, now=NOW)[0])

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for index in range(200):
            state = ("passed", "passed") if index % 2 else ("failed", "inference_failed")
            write_evidence(directory, record(scope, state=state[0], outcome=state[1]))
    finally:
        stop.set()
        thread.join()
    # Windows may briefly deny opening a file being replaced: that is "unsafe" (rejected).
    assert seen <= {"adopted", "not_passed", "unsafe"}
    assert "invalid" not in seen
    assert not [p for p in directory.iterdir() if p.name.endswith(".tmp")]


# --------------------------------------------------------------------------- overlay


def test_overlay_without_evidence_keeps_collector_output(settings, directory) -> None:
    d = collected(settings)
    assert overlay(settings, d, directory) == d
    assert overlay(settings, d, None) == d
    assert candidate(settings, d).runnable != "ready"


def test_complete_synthetic_cpu_evidence_is_the_only_ready_path(settings, directory) -> None:
    d = collected(settings)
    write_evidence(directory, record(scope_for(settings, d)))
    merged = overlay(settings, d, directory)
    c = candidate(settings, merged)
    assert merged.identity.runtime_fingerprint == RUNTIME
    assert merged.identity.hardware_fingerprint == HARDWARE
    assert c.runnable == "ready", issue_codes(c)
    smoke = next(o for o in c.observations if o.kind == "smoke")
    assert smoke.source == "smoke" and smoke.expires_at == NOW + EVIDENCE_TTL
    assert merged.required_memory_bytes == 500_000_000


@pytest.mark.parametrize(
    "updates,expected",
    [
        (dict(required_memory_bytes=None), "memory_unknown"),
        (dict(available_memory_bytes=None), "memory_unknown"),
        (dict(encoder_observed="unknown"), "smoke_route_unverified"),
        (dict(decoder_observed="unknown"), "smoke_route_unverified"),
    ],
)
def test_valid_smoke_with_missing_evidence_stays_unknown(settings, directory, updates, expected):
    d = collected(settings)
    write_evidence(directory, record(scope_for(settings, d), **updates))
    c = candidate(settings, overlay(settings, d, directory))
    assert c.runnable == "unknown" and expected in issue_codes(c)


def test_memory_shortage_blocks(settings, directory) -> None:
    d = collected(settings)
    write_evidence(
        directory,
        record(scope_for(settings, d), required_memory_bytes=9, available_memory_bytes=8),
    )
    c = candidate(settings, overlay(settings, d, directory))
    assert c.runnable == "blocked" and "memory_insufficient" in issue_codes(c)


def test_route_mismatch_blocks_and_requested_values_are_not_copied(settings, directory) -> None:
    d = collected(settings)
    write_evidence(
        directory,
        record(scope_for(settings, d), encoder_observed="cuda", encoder_device_index_observed=0),
    )
    c = candidate(settings, overlay(settings, d, directory))
    assert c.runnable == "blocked" and "smoke_route_mismatch" in issue_codes(c)


def test_cuda_evidence_never_reaches_ready_without_identity(settings, directory) -> None:
    d = collected(settings, "fw-cuda", device="cuda")
    write_evidence(directory, record(scope_for(settings, d), "fw-cuda"))
    merged = overlay(settings, d, directory)
    c = candidate(settings, merged)
    assert merged.identity.runtime_fingerprint is None
    assert c.runnable == "unknown"
    assert {"evidence_scope_incomplete", "memory_unknown"} <= issue_codes(c)
    smoke = next(o for o in c.observations if o.kind == "smoke")
    assert smoke.state == "passed"  # the route itself was attested with indices


@pytest.mark.parametrize("missing", ["encoder", "decoder"])
def test_cuda_index_missing_is_unverified(settings, directory, missing) -> None:
    d = collected(settings, "fw-cuda", device="cuda")
    write_evidence(
        directory,
        record(scope_for(settings, d), "fw-cuda", **{f"{missing}_device_index_observed": None}),
    )
    c = candidate(settings, overlay(settings, d, directory))
    assert "smoke_route_unverified" in issue_codes(c)


@pytest.mark.parametrize(
    "change",
    [
        dict(profile_generation="generation-2"),
        dict(model_files_fingerprint=digest("other files")),
        dict(model_revision="local-" + digest("other files")),
        dict(compute_type="float32"),
        dict(profile_fingerprint=digest("profile moved")),
    ],
)
def test_identity_changes_invalidate_evidence(settings, directory, change) -> None:
    write_evidence(directory, record(scope_for(settings, collected(settings))))
    changed = collected(settings, **change)
    merged = overlay(settings, changed, directory)
    assert merged == changed  # no adoption: different scope
    assert candidate(settings, merged).runnable != "ready"


def test_settings_runtime_and_hardware_changes_invalidate_evidence(settings, directory) -> None:
    d = collected(settings)
    write_evidence(directory, record(scope_for(settings, d)))
    other = settings.to_wire()
    other["asr"]["language"] = "ja"
    other_settings = ProcessingSettings.model_validate(other)
    (merged,) = apply_smoke_evidence(
        (collected(other_settings),),
        other_settings,
        now=NOW,
        directory=directory,
        fingerprints=fixed,
    )
    assert merged.identity.runtime_fingerprint is None
    for fps in ((digest("new driver/runtime"), HARDWARE), (RUNTIME, digest("new cpu"))):
        (merged,) = apply_smoke_evidence(
            (d,), settings, now=NOW, directory=directory, fingerprints=lambda _device, fps=fps: fps
        )
        assert merged == d


def test_overlay_ignores_non_worker_and_non_fw_deployments(settings, directory) -> None:
    cli = collected(settings, execution_path="cli")
    # Even a record for the exact CLI identity+fingerprints is never consulted.
    forged = ExecutionIdentity.model_validate(
        {
            **cli.identity.model_dump(mode="json"),
            "runtime_fingerprint": RUNTIME,
            "hardware_fingerprint": HARDWARE,
        }
    )
    write_evidence(directory, record(evidence_fingerprint(forged, settings)))
    assert overlay(settings, cli, directory) == cli
    assert smoke_identity(base_deployment("wc-cpu", model_family="ggml").identity, fixed) is None


def test_failed_resmoke_replaces_previous_success(settings, directory) -> None:
    d = collected(settings)
    scope = scope_for(settings, d)
    write_evidence(directory, record(scope))
    assert candidate(settings, overlay(settings, d, directory)).runnable == "ready"
    write_evidence(
        directory,
        record(
            scope,
            state="pending",
            outcome="running",
            encoder_observed="unknown",
            decoder_observed="unknown",
        ),
    )
    assert candidate(settings, overlay(settings, d, directory)).runnable != "ready"


def test_expired_evidence_is_not_adopted_by_overlay(settings, directory) -> None:
    d = collected(settings)
    write_evidence(directory, record(scope_for(settings, d)))
    assert overlay(settings, d, directory, now=NOW + EVIDENCE_TTL) == d


# --------------------------------------------------------------------------- fingerprints


def test_metadata_fingerprints_are_cpu_only_and_stable() -> None:
    assert runtime_fingerprint("cuda") is None and hardware_fingerprint("cuda") is None
    first = (runtime_fingerprint("cpu"), hardware_fingerprint("cpu"))
    assert first == (runtime_fingerprint("cpu"), hardware_fingerprint("cpu"))
    # The dev environment has faster-whisper/ctranslate2 metadata installed.
    assert first[0] is not None


def test_fingerprint_module_has_no_heavy_import_or_subprocess(tmp_path) -> None:
    import subprocess

    code = r"""
import sys
def audit(event, args):
    if event.startswith(('subprocess.', 'os.system', 'os.fork', 'socket.connect')):
        raise AssertionError(event)
sys.addaudithook(audit)
from utteran.capabilities.fingerprints import hardware_fingerprint, runtime_fingerprint
runtime_fingerprint('cpu'); hardware_fingerprint('cpu')
assert not {'ctranslate2','faster_whisper','torch'} & set(sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
