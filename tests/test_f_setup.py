"""束F (0.1.71): setup -> B4 evidence -> readiness -> B7 admission, diagnostics, #89.

Synthetic only: fake children / in-process smoke child with a fake runtime, fake NVML,
fake registry. Nothing here is real-GPU, real-model or real-Windows evidence.
"""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import test_b4_smoke_b7 as b4b7
from test_b4_smoke_executor import FakeRuntime, fixed
from test_b7_jobs import SENTINEL
from test_b8_setup import (
    FW,
    PYA,
    SECRET,
    World,
    executor,
    make_store,
    queued_setup,
    request,
    run_once,
)
from utteran.capabilities import nvml
from utteran.capabilities.smoke import SmokeRequest, run_smoke
from utteran_contracts import ProcessingSettings
from utteran_contracts.setup import SetupPlanRequest
from utteran_control.setup_repository import SetupControlStore
from utteran_gui import hardware
from utteran_gui.process_verifier import current_owner
from utteran_gui.setup_diagnostics import (
    DiagnosticProbes,
    RuntimeReport,
    SetupDiagnostics,
    VcRedist,
    detect_vcredist,
    parse_devices,
    recommend,
)
from utteran_gui.setup_executor import ChildResult, SetupExecutor, smoke_outcome
from utteran_gui.setup_planner import PlanRejected, build_plan
from utteran_gui.setup_readiness import check_readiness

TURBO = "faster-whisper:large-v3-turbo"


def asr_settings(model: str = "large-v3-turbo", **asr: Any) -> ProcessingSettings:
    data = json.loads(
        (
            Path(__file__).resolve().parents[1] / "frontend/src/domain/v1/presetTemplate.json"
        ).read_text(encoding="utf-8")
    )
    data["asr"].update(model=model, **asr)
    return ProcessingSettings.model_validate(data)


def verify_request(profile: str = "cpu", *refs: str, **kw: Any) -> SetupPlanRequest:
    refs = refs or (TURBO,)
    return SetupPlanRequest.model_validate(
        dict(
            schema_version=1,
            profile_kind=profile,
            model_refs=list(refs),
            diarization_enabled=PYA in refs,
            verification_settings=kw.get("settings", asr_settings()).to_wire(),
        )
    )


# --------------------------------------------------------------------------- plan


def test_plan_with_verification_ends_with_capability_smoke_and_hides_settings(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    world.install("cpu")
    settings = asr_settings(initial_prompt=SENTINEL)
    content = build_plan(verify_request(settings=settings), world.observer.observe())
    assert [s.kind for s in content.steps] == ["model_download", "capability_smoke"]
    assert content.steps[-1].target == TURBO and content.verification is not None
    assert content.verification.recipe_id == "fw-cpu"
    store = make_store(tmp_path / "ops.sqlite3")
    scope_body = content.to_wire()
    from utteran_control.store import IdempotencyScope

    accepted = store.create_plan(
        IdempotencyScope("/api/v1/setup-plans", "plan-key-00000001"),
        store.setup_digest(scope_body),
        content,
    )
    text = json.dumps(accepted.body)
    assert SENTINEL not in text  # the public plan view carries a digest only
    assert accepted.body["verification"]["recipe_id"] == "fw-cpu"
    assert len(accepted.body["verification"]["settings_sha256"]) == 64
    assert SENTINEL not in json.dumps(store.get_plan(accepted.body["id"]).to_wire())


def test_plan_without_verification_keeps_the_previous_shape_and_hash(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.install("cuda")
    content = build_plan(request("cuda"), world.observer.observe())
    assert "verification" not in content.to_wire()
    assert [s.kind for s in content.steps][-1] == "smoke"


def test_diarization_plan_keeps_the_cli_smoke_before_the_asr_verification(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    world.install("cuda")
    world.credential = True
    content = build_plan(verify_request("cuda", TURBO, PYA), world.observer.observe())
    assert [s.kind for s in content.steps] == [
        "credential_preflight",
        "model_download",
        "model_download",
        "smoke",
        "capability_smoke",
    ]
    assert content.verification is not None and content.verification.recipe_id == "fw-cuda"


@pytest.mark.parametrize(
    ("profile", "settings", "refs"),
    [
        ("intel", asr_settings(), (TURBO,)),  # no B4 smoke recipe for this profile
        ("cpu", asr_settings(model="small"), (TURBO,)),  # verified model not in the plan
        ("cpu", asr_settings(backend="whisper-cpp"), (TURBO,)),
    ],
)
def test_unverifiable_requests_are_refused(tmp_path, profile, settings, refs) -> None:
    world = World(tmp_path)
    world.install(profile)
    with pytest.raises(PlanRejected) as caught:
        build_plan(
            SetupPlanRequest(
                schema_version=1,
                profile_kind=profile,
                model_refs=refs,
                diarization_enabled=False,
                verification_settings=settings,
            ),
            world.observer.observe(),
        )
    assert caught.value.code == "verification_unsupported" and caught.value.status == 422


def test_verification_with_diarization_enabled_is_refused(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.install("cuda")
    data = asr_settings().to_wire()
    data["diarization"]["enabled"] = True
    with pytest.raises(PlanRejected):
        build_plan(
            verify_request("cuda", TURBO, PYA, settings=ProcessingSettings.model_validate(data)),
            world.observer.observe(),
        )


# --------------------------------------------------------------------------- executor


class SmokeWorld(World):
    """Adds the B4 smoke child to the B8 fake world (scripted fixed outcome)."""

    def __init__(self, tmp: Path, outcome: str = "passed") -> None:
        super().__init__(tmp)
        self.outcome = outcome
        self.stdin: list[bytes] = []
        self.envs: list[dict[str, str]] = []

    def run(self, argv, *, cwd, env, on_spawn, capture_status, stdin=None) -> ChildResult:
        if argv[-2:] == ["utteran.capabilities.smoke", "run"]:
            from test_b8_setup import FakeProcess

            self.calls.append(list(argv))
            assert stdin is not None and capture_status
            self.stdin.append(stdin)
            self.envs.append(env)
            on_spawn(FakeProcess())
            line = json.dumps({"schema_version": 1, "outcome": self.outcome, "evidence": "none"})
            return ChildResult(0, line)
        return super().run(argv, cwd=cwd, env=env, on_spawn=on_spawn, capture_status=capture_status)


class SmokeAdapter:
    def __init__(self, world: World) -> None:
        from test_b8_setup import Adapter

        self.inner = Adapter(world)
        self.repo_root = world.repo

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def capability_smoke_command(self, profile: str) -> list[str]:
        return [f"python-{profile}", "-I", "-B", "-m", "utteran.capabilities.smoke", "run"]

    def capability_environment(self, profile: str) -> dict[str, str]:
        return {"UTTERAN_PROFILE": profile, "HF_HUB_OFFLINE": "1"}


def smoke_executor(store: SetupControlStore, world: World) -> SetupExecutor:
    ex = executor(store, world)
    ex.adapter = SmokeAdapter(world)  # type: ignore[assignment]
    return ex


def _run(store: SetupControlStore, world: World) -> None:
    ex = smoke_executor(store, world)
    candidate = store.next_dispatchable()
    assert candidate is not None
    ex.dispatch(*candidate, current_owner())


def _model_ok(world: World, ref: str) -> None:
    world.model_dir(ref).mkdir(parents=True, exist_ok=True)
    (world.model_dir(ref) / "ok").write_text("x")


def test_capability_smoke_step_succeeds_only_on_a_passed_outcome(tmp_path: Path) -> None:
    world = SmokeWorld(tmp_path)
    world.install("cpu")
    world.credential = True
    _model_ok(world, TURBO)
    store = make_store(tmp_path / "ops.sqlite3")
    op = queued_setup(store, world, verify_request(settings=asr_settings(initial_prompt=SENTINEL)))
    _run(store, world)
    view = store.get_setup(op.body["id"])
    assert view.status == "succeeded"
    step = next(s for s in view.steps if s.kind == "capability_smoke")
    assert step.status == "succeeded" and step.evidence is not None
    assert step.evidence.kind == "capability_evidence_recorded"
    (body,) = world.stdin
    sent = json.loads(body)
    assert sent["recipe_id"] == "fw-cpu" and sent["model_id"] == "large-v3-turbo"
    assert sent["settings"]["asr"]["initial_prompt"] == SENTINEL  # stdin only
    assert all(SENTINEL not in arg for call in world.calls for arg in call)  # never argv
    assert world.envs[0] == {"UTTERAN_PROFILE": "cpu", "HF_HUB_OFFLINE": "1"}
    assert SENTINEL not in json.dumps(view.to_wire())


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        ("backend_unavailable", "smoke_backend_unavailable"),
        ("out_of_memory", "smoke_out_of_memory"),
        ("model_load_failed", "smoke_model_incomplete"),
        ("route_unverified", "smoke_route_unverified"),
        ("internal_error", "smoke_failed"),
        ("something new", "smoke_failed"),
    ],
)
def test_capability_smoke_failures_are_grouped_codes(tmp_path, outcome, reason) -> None:
    world = SmokeWorld(tmp_path, outcome)
    world.install("cpu")
    _model_ok(world, TURBO)
    store = make_store(tmp_path / "ops.sqlite3")
    op = queued_setup(store, world, verify_request())
    _run(store, world)
    view = store.get_setup(op.body["id"])
    assert view.status == "failed"
    assert view.error is not None and view.error.code == "smoke_failed"
    step = next(s for s in view.steps if s.kind == "capability_smoke")
    assert (step.status, step.reason_code, step.evidence) == ("failed", reason, None)


def test_smoke_outcome_parser_is_strict() -> None:
    good = json.dumps({"schema_version": 1, "outcome": "passed", "evidence": "recorded"})
    assert smoke_outcome(good) == "passed"
    for bad in (
        None,
        "",
        "{}",
        "[1]",
        json.dumps({"schema_version": 2, "outcome": "passed", "evidence": "none"}),
    ):
        assert smoke_outcome(bad) is None


def test_failed_verification_retry_reuses_the_approved_plan(tmp_path: Path) -> None:
    world = SmokeWorld(tmp_path, "backend_unavailable")
    world.install("cpu")
    _model_ok(world, TURBO)
    store = make_store(tmp_path / "ops.sqlite3")
    op = queued_setup(store, world, verify_request())
    _run(store, world)
    failed = store.get_setup(op.body["id"])
    from utteran_control.store import IdempotencyScope

    obs = world.observer.observe()
    retried = store.retry_setup(
        failed.id,
        failed.revision,
        IdempotencyScope(f"/api/v1/setup-operations/{failed.id}/retry", "retry-key-000001"),
        fingerprint=obs.fingerprint,
        credentials=obs.credentials,
    )
    world.outcome = "passed"
    _run(store, world)
    assert store.get_setup(retried.body["id"]).status == "succeeded"
    assert store.get_setup(failed.id).status == "failed"  # the original is unchanged


# ------------------------------------------- integration: unbuilt -> ready -> admission


def test_cpu_setup_verification_makes_the_same_settings_ready_and_admittable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real B8 executor + real smoke orchestration (fake runtime) + real B4 read-back +
    real B7 admission. Before: not ready (verifiable). After: ready, admitted, prestart ok."""
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    env = b4b7.Env(jobs, b4b7.worker_settings)
    w = b4b7.World(tmp_path, monkeypatch)
    env.provider = w.provider
    env.admission.provider = w.provider
    source = env.file()
    b4b7.refused(env, source)
    settings = w.settings()  # the exact effective settings of this job
    body = {"schema_version": 1, "settings": settings.to_wire(), "policy_ref": env.policy_ref}
    before = check_readiness(env.admission, body)
    assert before["state"] == "not_ready", before
    assert any(c["recipe_id"] == "fw-cpu" and c["verifiable"] for c in before["candidates"]), before

    class InProcessSmoke(SmokeWorld):
        def run(self, argv, *, cwd, env, on_spawn, capture_status, stdin=None):
            if argv[-2:] != ["utteran.capabilities.smoke", "run"]:
                return super().run(
                    argv, cwd=cwd, env=env, on_spawn=on_spawn, capture_status=capture_status
                )
            from test_b8_setup import FakeProcess

            on_spawn(FakeProcess())
            assert stdin is not None
            result = run_smoke(
                SmokeRequest.model_validate_json(stdin),
                runtime=FakeRuntime(),
                now=lambda: datetime.now(UTC),
                paths=w.paths,
                profile_root=w.profile_root,
                repo_root=w.repo,
                fingerprints=fixed,
            )
            return ChildResult(0, json.dumps(result.to_wire()))

    (tmp_path / "b8").mkdir()
    runner_world = InProcessSmoke(tmp_path / "b8")
    runner_world.install("cpu")
    _model_ok(runner_world, TURBO)
    store = make_store(tmp_path / "ops.sqlite3")
    req = SetupPlanRequest(
        schema_version=1,
        profile_kind="cpu",
        model_refs=(TURBO,),
        diarization_enabled=False,
        verification_settings=settings,
    )
    op = queued_setup(store, runner_world, req)
    _run(store, runner_world)
    assert store.get_setup(op.body["id"]).status == "succeeded"

    after = check_readiness(env.admission, body)
    assert after["state"] == "ready" and after["selected"]["recipe_id"] == "fw-cpu"
    snapshot = b4b7.accepted(env, source)
    assert env.admission.prestart_check(snapshot) is None
    # A different settings value (e.g. language) is a different evidence scope: not ready.
    other = settings.to_wire()
    other["asr"]["language"] = "en" if other["asr"]["language"] != "en" else "ja"
    changed = check_readiness(env.admission, {**body, "settings": other})
    assert changed["state"] == "not_ready"
    assert any(c["verifiable"] for c in changed["candidates"])


def test_readiness_refuses_unknown_policy_and_bad_bodies(tmp_path, monkeypatch) -> None:
    from utteran_gui.job_admission import AdmissionError

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    env = b4b7.Env(jobs, b4b7.worker_settings)
    body = {
        "schema_version": 1,
        "settings": asr_settings().to_wire(),
        "policy_ref": {"id": "pol_" + "0" * 32, "revision": 1},
    }
    with pytest.raises(AdmissionError) as caught:
        check_readiness(env.admission, body)
    assert caught.value.code == "policy_unresolved"
    with pytest.raises(AdmissionError) as caught:
        check_readiness(env.admission, {"schema_version": 1})
    assert caught.value.code == "contract_validation_failed"


# --------------------------------------------------------------------------- diagnostics


class Cli:
    def __init__(self, tmp: Path, profiles: tuple[str, ...] = ("cuda",)) -> None:
        self.repo_root = tmp
        self.profiles = profiles

    def profile_info(self, name: str) -> Any:
        return type("Info", (), {"exists": name in self.profiles})()


def gpu(*vendors: str, error: str | None = None, driver: str | None = "32.0.15.6094"):
    return hardware.GpuReport(
        tuple(hardware.GpuAdapter(f"{v} adapter", v, driver) for v in vendors),  # type: ignore[arg-type]
        "none",
        error=error,
    )


def devices_json(ct2: bool | None, torch: bool | None = True) -> dict[str, Any]:
    return {
        "ctranslate2": {
            "cuda_status": "timeout" if ct2 is None else "completed",
            "cuda_device_count": 1,
            "cuda_devices": [{"usable": bool(ct2)}],
        },
        "pytorch": {"cuda_status": "completed", "cuda_available": torch},
        "probes": [
            {"name": "ctranslate2_cuda_count", "status": "timeout" if ct2 is None else "completed"},
            {"name": "/abs/path", "status": "completed"},
        ],
    }


def diagnostics(tmp: Path, runtime: Any, **kw: Any) -> SetupDiagnostics:
    probes = DiagnosticProbes(
        gpu=kw.pop("gpu", lambda: gpu("nvidia")),
        memory=lambda: hardware.MemoryReport(16 * 2**30, 8 * 2**30),
        disk=lambda _p: hardware.DiskReport(100 * 2**30),
        vcredist=lambda: VcRedist("present", "v14.40"),
        runtime=runtime,
    )
    return SetupDiagnostics(Cli(tmp, kw.pop("profiles", ("cuda",))), probes=probes, **kw)


def test_first_runtime_timeout_is_retried_once_and_the_second_pass_wins(tmp_path) -> None:
    calls: list[bool] = []

    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        calls.append(refresh)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("devices", 200)
        return devices_json(True)

    service = diagnostics(tmp_path, runtime)
    service.refresh(runtime=True, force_runtime=True)
    assert service.wait(5)
    view = service.snapshot()
    assert view["phase"] == "completed"
    assert view["runtime"]["ctranslate2_cuda"] is True and view["runtime"]["attempts"] == 2
    assert calls == [True, False]  # the retry reuses the warm cache, never --refresh again
    assert view["recommendation"]["profile"] == "cuda"
    assert view["recommendation"]["reason_codes"][0] == "runtime_cuda_confirmed"
    assert {p["name"] for p in view["runtime"]["probes"]} == {"ctranslate2_cuda_count"}


def test_repeated_timeout_stays_unknown_never_cpu_or_no_gpu(tmp_path) -> None:
    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        raise subprocess.TimeoutExpired("devices", 200)

    service = diagnostics(tmp_path, runtime)
    service.refresh(runtime=True)
    assert service.wait(5)
    view = service.snapshot()
    assert view["runtime"]["reason_code"] == "probe_timeout"
    assert view["runtime"]["ctranslate2_cuda"] is None and view["runtime"]["attempts"] == 2
    assert view["gpu"]["state"] == "detected"
    assert view["recommendation"]["profile"] == "cuda"  # NVIDIA seen by the OS, not denied
    assert "runtime_not_confirmed" in view["recommendation"]["reason_codes"]


def test_concurrent_refreshes_share_one_pass(tmp_path) -> None:
    release = threading.Event()
    calls: list[int] = []

    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        calls.append(1)
        release.wait(5)
        return devices_json(True)

    service = diagnostics(tmp_path, runtime)
    generations = {service.refresh(runtime=True)["generation"] for _ in range(5)}
    release.set()
    assert service.wait(5)
    assert generations == {1} and len(calls) == 1


def test_an_older_pass_never_overwrites_a_newer_one(tmp_path) -> None:
    service = diagnostics(tmp_path, lambda p, r: devices_json(True))
    service.refresh(runtime=True)
    assert service.wait(5)
    assert service._apply(0, phase="failed") is False  # stale generation
    assert service.snapshot()["phase"] == "completed"


def test_no_profile_and_running_setup_skip_the_runtime_probe(tmp_path) -> None:
    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        raise AssertionError("must not probe")

    service = diagnostics(tmp_path, runtime, profiles=())
    service.refresh(runtime=True)
    assert service.wait(5)
    assert service.snapshot()["runtime"]["reason_code"] == "profile_missing"
    busy = diagnostics(tmp_path, runtime, busy_profile=lambda: "setup_x")
    busy.refresh(runtime=True)
    assert busy.wait(5)
    assert busy.snapshot()["runtime"]["reason_code"] == "setup_running"


def test_gpu_probe_failure_is_unknown_with_a_cpu_recommendation(tmp_path) -> None:
    service = diagnostics(tmp_path, lambda p, r: devices_json(None), gpu=lambda: gpu(error="boom"))
    service.refresh(runtime=False)
    assert service.wait(5)
    view = service.snapshot()
    assert view["gpu"]["state"] == "unknown" and view["gpu"]["reason_code"] == "gpu_probe_failed"
    assert view["recommendation"] == {
        "profile": "cpu",
        "reason_codes": ["gpu_detection_unknown"],
        "confident": False,
    }


def test_recommendation_codes() -> None:
    assert recommend(gpu(), None)["reason_codes"] == ["no_gpu_detected"]
    assert recommend(gpu("intel"), None)["profile"] == "cpu"
    no_driver = recommend(gpu("nvidia", driver=None), None)
    assert "nvidia_driver_unknown" in no_driver["reason_codes"]
    dead = recommend(gpu("nvidia"), RuntimeReport("cuda", None, False, False))
    assert dead["profile"] == "cpu" and "runtime_cuda_unavailable" in dead["reason_codes"]
    asr_only = recommend(gpu("nvidia"), RuntimeReport("cuda", None, True, None))
    assert asr_only["reason_codes"] == ["runtime_cuda_confirmed", "diarization_cuda_unconfirmed"]


def test_parse_devices_is_an_allowlist() -> None:
    report = parse_devices("cuda", devices_json(True), 1)
    wire = report.to_wire()
    assert wire["probes"] == [{"name": "ctranslate2_cuda_count", "status": "completed"}]
    assert "/abs/path" not in json.dumps(wire)


@pytest.mark.parametrize(
    ("installed", "dlls", "state"),
    [
        (1, 3, "present"),
        (None, 0, "missing"),
        (0, 0, "missing"),
        (1, 1, "partial"),
        (None, 3, "partial"),
    ],
)
def test_vcredist_needs_registry_and_dlls(tmp_path, installed, dlls, state) -> None:
    for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")[:dlls]:
        (tmp_path / name).write_bytes(b"")
    result = detect_vcredist(
        system="Windows", read_registry=lambda: (installed, "v14.40.33810.00"), system32=tmp_path
    )
    assert result.state == state
    assert detect_vcredist(system="Linux").state == "not_applicable"
    assert result.to_wire()["download_url"].startswith("https://aka.ms/")


def test_gpu_driver_version_is_bounded_and_plain() -> None:
    assert hardware._driver_version({"DriverVersion": "32.0.15.6094"}) == "32.0.15.6094"
    assert hardware._driver_version({"DriverVersion": "C:\\evil path"}) is None
    assert hardware._driver_version({}) is None


# --------------------------------------------------------------------------- #89 devices


def test_devices_retries_a_timed_out_cuda_probe_once_per_pass(monkeypatch) -> None:
    from utteran import devices

    calls: list[str] = []

    def probe(name, label, timeout, *, argument=None):
        calls.append(name)
        if name == "ctranslate2_cuda_count" and calls.count(name) == 1:
            return devices._ProbeRun(devices.ProbeOutcome(name, label, "timeout", timeout), None)
        if name == "torch_cuda":
            return devices._ProbeRun(devices.ProbeOutcome(name, label, "timeout", timeout), None)
        result = {"count": 1, "version": "4.8.1", "compute_types": ["float16"]}
        if name == "nvidia_metadata":
            result = {"stdout": "0, NVIDIA Synthetic, 8192\n1, NVIDIA Other, 4096\n"}
        return devices._ProbeRun(devices.ProbeOutcome(name, label, "completed", 0.1), result)

    monkeypatch.setattr(devices, "run_isolated_probe", probe)
    monkeypatch.setattr(devices.importlib.util, "find_spec", lambda name: object())
    reports = devices._detect_isolated_runtimes(20.0, None)
    assert calls.count("ctranslate2_cuda_count") == 2  # timeout -> one retry -> success
    assert calls.count("torch_cuda") == 2  # retried once, still timeout -> unknown
    assert reports.ctranslate2.cuda_device_count == 1
    count = next(o for o in reports.outcomes if o.name == "ctranslate2_cuda_count")
    assert (count.status, count.attempts) == ("completed", 2)
    assert next(o for o in reports.outcomes if o.name == "torch_cuda").status == "timeout"
    assert not devices._probe_results_are_cacheable(reports)  # a timeout is never cached
    # Two GPUs reported by nvidia-smi, one CUDA ordinal: no name/memory guessed (#89).
    (device,) = reports.ctranslate2.cuda_devices
    assert device.name == "NVIDIA CUDA 0" and device.memory_bytes is None


def test_devices_maps_metadata_only_when_unambiguous() -> None:
    from utteran import devices

    run = devices._ProbeRun(devices.ProbeOutcome("x", "x", "completed", 0.1), {"count": 1})
    cuda = devices._ProbeRun(
        devices.ProbeOutcome("ctranslate2_cuda", "x", "completed", 0.1),
        {"compute_types": ["float16"]},
    )
    report = devices._combine_ctranslate2(
        True, run, run, [cuda], {0: ("NVIDIA Synthetic", 8 * 2**30)}
    )
    assert report.cuda_devices[0].name == "NVIDIA Synthetic"


# --------------------------------------------------------------------------- NVML


class FakeNvml:
    """Synthetic NVML C API (ctypes-style out parameters)."""

    def __init__(self, gpus: list[tuple[str, str, int, int]], used: int | None = None) -> None:
        self.gpus = gpus  # (uuid, name, total, free)
        self.used = used

    @staticmethod
    def _put(buffer: Any, text: str) -> None:
        buffer.value = text.encode()

    def nvmlInit_v2(self) -> int:
        return 0

    def nvmlShutdown(self) -> int:
        return 0

    def nvmlSystemGetDriverVersion(self, buffer: Any, size: Any) -> int:
        self._put(buffer, "560.94")
        return 0

    def nvmlSystemGetCudaDriverVersion_v2(self, ref: Any) -> int:
        ref._obj.value = 12060
        return 0

    def nvmlDeviceGetCount_v2(self, ref: Any) -> int:
        ref._obj.value = len(self.gpus)
        return 0

    def nvmlDeviceGetHandleByIndex_v2(self, index: Any, ref: Any) -> int:
        ref._obj.value = index.value + 1
        return 0

    def nvmlDeviceGetHandleByUUID(self, uuid: bytes, ref: Any) -> int:
        for i, gpu_ in enumerate(self.gpus):
            if gpu_[0].encode() == uuid:
                ref._obj.value = i + 1
                return 0
        return 13

    def _gpu(self, handle: Any) -> tuple[str, str, int, int]:
        return self.gpus[int(handle.value) - 1]

    def nvmlDeviceGetUUID(self, handle: Any, buffer: Any, size: Any) -> int:
        self._put(buffer, self._gpu(handle)[0])
        return 0

    def nvmlDeviceGetName(self, handle: Any, buffer: Any, size: Any) -> int:
        self._put(buffer, self._gpu(handle)[1])
        return 0

    def nvmlDeviceGetMemoryInfo(self, handle: Any, ref: Any) -> int:
        _, _, total, free = self._gpu(handle)
        ref._obj.total, ref._obj.free, ref._obj.used = total, free, total - free
        return 0

    def nvmlDeviceGetComputeRunningProcesses_v3(self, handle: Any, count: Any, entries: Any) -> int:
        if self.used is None:
            count._obj.value = 0
            return 0
        entries[0].pid = 4242
        entries[0].usedGpuMemory = self.used
        count._obj.value = 1
        return 0


GPU_A = ("GPU-aaaaaaaa-0000-0000-0000-000000000001", "NVIDIA Synthetic A", 8 * 2**30, 6 * 2**30)
GPU_B = ("GPU-bbbbbbbb-0000-0000-0000-000000000002", "NVIDIA Synthetic B", 4 * 2**30, 1 * 2**30)


def test_nvml_snapshot_is_sorted_by_uuid_and_reads_memory() -> None:
    lib = FakeNvml([GPU_B, GPU_A], used=900 * 2**20)
    snap = nvml.snapshot(lib)
    assert snap is not None and snap.driver_version == "560.94"
    assert snap.cuda_driver_version == 12060
    assert [g.uuid for g in snap.gpus] == [GPU_A[0], GPU_B[0]]
    assert nvml.free_memory(GPU_B[0], lib) == 1 * 2**30
    assert nvml.process_used_memory(GPU_A[0], 4242, lib) == 900 * 2**20
    assert nvml.process_used_memory(GPU_A[0], 1, lib) is None
    assert nvml.free_memory("GPU-unknown", lib) is None


def test_nvml_not_available_reads_as_unknown() -> None:
    assert (
        nvml.process_used_memory(GPU_A[0], 4242, FakeNvml([GPU_A], used=nvml.NOT_AVAILABLE)) is None
    )

    class Broken(FakeNvml):
        def nvmlInit_v2(self) -> int:
            return 9

    assert nvml.snapshot(Broken([GPU_A])) is None


def test_cuda_fingerprints_follow_the_gpu_set_driver_and_environment(monkeypatch) -> None:
    from utteran.capabilities import fingerprints

    monkeypatch.setattr(
        fingerprints, "package_versions", lambda: {n: "1" for n in fingerprints.RUNTIME_PACKAGES}
    )
    current = {"lib": FakeNvml([GPU_A])}
    monkeypatch.setattr(nvml, "load_nvml", lambda: current["lib"])
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    first = fingerprints.current_fingerprints("cuda")
    assert None not in first
    current["lib"] = FakeNvml([GPU_A, GPU_B])
    assert fingerprints.hardware_fingerprint("cuda") != first[1]
    current["lib"] = FakeNvml([GPU_A])
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    assert fingerprints.current_fingerprints("cuda") != first
    current["lib"] = None
    assert fingerprints.current_fingerprints("cuda") == (None, None)


def test_cuda_uuid_formatting_from_the_driver_api() -> None:
    class Cuda:
        def cuInit(self, flags: int) -> int:
            return 0

        def cuDeviceGet(self, ref: Any, ordinal: Any) -> int:
            ref._obj.value = ordinal.value
            return 0

        def cuDeviceGetUuid_v2(self, raw: Any, device: Any) -> int:
            for i in range(16):
                raw[i] = i
            return 0

    assert nvml.cuda_ordinal_uuid(0, Cuda()) == "GPU-00010203-0405-0607-0809-0a0b0c0d0e0f"
    assert nvml.cuda_ordinal_uuid(-1, Cuda()) is None


def test_setup_executor_signature_accepts_no_stdin_for_old_runners(tmp_path) -> None:
    # Existing step kinds never pass stdin, so runners without the parameter keep working.
    world = World(tmp_path)
    world.install("cuda")
    world.credential = True
    store = make_store(tmp_path / "ops.sqlite3")
    queued_setup(store, world, request("cuda", FW))
    run_once(store, world)
    assert SECRET not in json.dumps([c for c in world.calls])


# --------------------------------------------------------------------------- HTTP


@pytest.fixture
def http(tmp_path: Path):
    from fastapi.testclient import TestClient

    from test_b7_jobs import SESSION, Env, FakeRunner
    from utteran_gui.api import SESSION_HEADER, create_app
    from utteran_gui.job_runtime import JobRuntime, SetupComponents
    from utteran_gui.operation_queue import OperationQueue

    (tmp_path / "env").mkdir()
    (tmp_path / "world").mkdir()
    env = Env(tmp_path / "env", None)
    world = SmokeWorld(tmp_path / "world")
    world.install("cpu")
    _model_ok(world, TURBO)
    queue = OperationQueue()
    service = diagnostics(tmp_path, lambda p, r: devices_json(True), profiles=("cpu",))
    runtime = JobRuntime(
        store_path=env.store.path,
        settings_path=env.settings_path,
        legacy_queue=queue,
        provider=env.provider,
        runner=FakeRunner(env),
        job_dir=env.tmp / "jobs",
        autostart_dispatcher=False,
        setup=SetupComponents(
            observer=world.observer,
            adapter=SmokeAdapter(world),
            runner=world,
            work_dir=world.tmp,
            diagnostics=service,
        ),
    )
    authority = "127.0.0.1:8765"
    app = create_app(
        SESSION,
        repo_root=Path(__file__).parents[1],
        operation_queue=queue,
        enable_v1_settings=True,
        v1_authority=authority,
        v1_store_path=env.settings_path,
        enable_v1_jobs=True,
        v1_jobs_runtime=runtime,
        enable_v1_setup=True,
    )
    with TestClient(app, base_url="http://" + authority, headers={SESSION_HEADER: SESSION}) as c:
        c.env = env  # type: ignore[attr-defined]
        c.diagnostics = service  # type: ignore[attr-defined]
        yield c


def test_http_diagnostics_refresh_returns_at_once_and_settles(http) -> None:
    first = http.get("/api/v1/setup-diagnostics")
    assert first.status_code == 200 and first.json()["phase"] == "idle"
    started = http.post(
        "/api/v1/setup-diagnostics/refresh", json={"schema_version": 1, "runtime": True}
    )
    assert started.status_code == 202
    assert http.diagnostics.wait(5)
    view = http.get("/api/v1/setup-diagnostics").json()
    assert view["phase"] == "completed" and view["vcredist"]["state"] == "present"
    bad = http.post("/api/v1/setup-diagnostics/refresh", json={"schema_version": 1, "x": 1})
    assert bad.status_code == 422


def test_http_plan_with_verification_hides_the_settings(http) -> None:
    body = {
        "schema_version": 1,
        "profile_kind": "cpu",
        "model_refs": [TURBO],
        "diarization_enabled": False,
        "verification_settings": asr_settings(initial_prompt=SENTINEL).to_wire(),
    }
    created = http.post(
        "/api/v1/setup-plans", json=body, headers={"Idempotency-Key": "plan-f-http-00001"}
    )
    assert created.status_code == 201, created.text
    assert SENTINEL not in created.text
    assert created.json()["steps"][-1]["kind"] == "capability_smoke"
    got = http.get(f"/api/v1/setup-plans/{created.json()['id']}")
    assert SENTINEL not in got.text
    refused = http.post(
        "/api/v1/setup-plans",
        json={**body, "profile_kind": "intel"},
        headers={"Idempotency-Key": "plan-f-http-00002"},
    )
    assert refused.status_code in {409, 422}


def test_http_readiness_check_is_read_only(http) -> None:
    env = http.env
    body = {
        "schema_version": 1,
        "settings": asr_settings().to_wire(),
        "policy_ref": env.policy_ref,
    }
    result = http.post("/api/v1/readiness-checks", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["state"] in {"ready", "not_ready"}
    assert http.post("/api/v1/readiness-checks", json={"schema_version": 1}).status_code == 422
