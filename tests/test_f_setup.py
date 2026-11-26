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


def test_verification_with_diarization_uses_the_same_offline_smoke(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.install("cuda")
    data = asr_settings().to_wire()
    data["diarization"]["enabled"] = True
    plan = build_plan(
        verify_request("cuda", TURBO, PYA, settings=ProcessingSettings.model_validate(data)),
        world.observer.observe(),
    )
    assert [step.kind for step in plan.steps] == [
        "credential_preflight",
        "model_download",
        "model_download",
        "capability_smoke",
    ]


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
    assert service.snapshot()["runtime"]["reason_code"] == "cuda_profile_missing"
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
    intel = recommend(gpu("intel"), RuntimeReport("cuda", None, True, True))
    assert intel["profile"] == "intel" and not intel["confident"]
    assert recommend(gpu("intel"), RuntimeReport("intel", None, vulkan=True))["confident"]
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
    assert calls.count("torch_cuda") == 1  # the one retry of this pass was already used
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


# --------------------------------------------------------------------------- review fixes


class _TimeoutProcess:
    """Fake Popen: the first ``devices`` run outlives its timeout, the next one answers."""

    calls = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).calls += 1
        self.first = type(self).calls == 1
        self.returncode = 0
        self.args = args[0] if args else []
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        if self.first and not self.killed and timeout is not None:
            raise subprocess.TimeoutExpired("devices", timeout)
        return json.dumps(devices_json(True)), ""

    def poll(self) -> int:
        return 0


def test_real_cli_timeout_is_retried_through_the_adapter(tmp_path, monkeypatch) -> None:
    """The production adapter turns a timeout into CliTimeout (a CliError): still retried."""
    from utteran_gui.cli import CliAdapter, CliTimeout

    _TimeoutProcess.calls = 0

    def kill(process: Any) -> None:
        process.killed = True

    cli = CliAdapter(tmp_path, tmp_path / "venvs", popen_factory=_TimeoutProcess)
    cli._tree_killer = kill  # type: ignore[method-assign]
    monkeypatch.setattr(cli, "command", lambda profile, arguments: ["devices"])
    with pytest.raises(CliTimeout):
        cli.run_json("cuda", ["devices", "--json"], timeout=0.01)
    _TimeoutProcess.calls = 0
    # Real ratio of timeout to budget (both scaled down): the second run must still happen.
    service = SetupDiagnostics(cli, runtime_timeout=0.05)
    service.probes = DiagnosticProbes(
        gpu=lambda: gpu("nvidia"),
        memory=lambda: hardware.MemoryReport(None, None),
        disk=lambda _p: hardware.DiskReport(None),
        vcredist=lambda: VcRedist("present"),
        runtime=lambda profile, refresh: service._devices(profile, refresh),
    )
    monkeypatch.setattr(
        cli, "profile_info", lambda name: type("I", (), {"exists": name == "cuda"})()
    )
    service.refresh(runtime=True, force_runtime=True)
    assert service.wait(5)
    runtime = service.snapshot()["runtime"]
    assert runtime["ctranslate2_cuda"] is True and runtime["attempts"] == 2


def test_a_forced_redetect_during_a_pass_runs_after_it(tmp_path) -> None:
    release = threading.Event()
    forced: list[bool] = []

    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        forced.append(refresh)
        release.wait(5)
        return devices_json(True)

    service = diagnostics(tmp_path, runtime)
    service.refresh(runtime=True, force_runtime=False)
    assert service.runtime_busy()
    assert service.refresh(runtime=True, force_runtime=True)["rerun_pending"] is True
    service.refresh(runtime=True, force_runtime=True)  # coalesced into the same pending pass
    release.set()
    assert service.wait(5)
    assert forced == [False, True]
    assert service.snapshot()["generation"] == 2 and not service.runtime_busy()


def test_only_cuda_probe_timeouts_trigger_a_retry() -> None:
    payload = devices_json(True)
    payload["probes"].append({"name": "vulkan", "status": "timeout"})
    assert parse_devices("cuda", payload, 1).reason_code is None


def test_setup_start_is_refused_while_a_runtime_probe_runs(http) -> None:
    from utteran_gui.setup_diagnostics import SetupDiagnostics as Diag

    http.diagnostics.runtime_busy = lambda: True  # type: ignore[method-assign]
    assert isinstance(http.diagnostics, Diag)
    body = {
        "schema_version": 1,
        "plan_ref": {"id": "plan_" + "0" * 32, "revision": 1},
        "approval_id": "apr_" + "0" * 32,
    }
    refused = http.post(
        "/api/v1/setup-operations", json=body, headers={"Idempotency-Key": "start-f-busy-000001"}
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "diagnostics_running"


# --------------------------------------------------------------------------- PR #91 owner review


def _repo(tmp_path: Path):
    from utteran_settings.repository import Repository

    return Repository(tmp_path / "settings" / "resources.sqlite3")


def _writes(model: str = "large-v3-turbo"):
    from utteran_contracts.settings import PolicyWrite, PresetWrite

    preset = PresetWrite.model_validate(
        {"schema_version": 1, "name": "Setup", "settings": asr_settings(model).to_wire()}
    )
    policy = PolicyWrite.model_validate(
        {
            "schema_version": 1,
            "name": "Setup cpu",
            "mode": "auto",
            "fixed_candidate_id": None,
            "allowed_profile_ids": ["cpu"],
            "objective": "balanced",
            "max_memory_bytes": None,
            "fallback": "none",
            "execution_deadline_seconds": None,
        }
    )
    return preset, policy


def test_defaults_are_one_atomic_pair_into_an_empty_store_and_replayed(tmp_path) -> None:
    repo = _repo(tmp_path)
    first = repo.create_defaults_if_empty("setup-defaults-" + "a" * 32, *_writes())
    assert first["state"] == "created" and not first["replayed"]
    again = repo.create_defaults_if_empty("setup-defaults-" + "a" * 32, *_writes())
    assert again["replayed"] and again["preset"] == first["preset"]
    assert (
        len(repo.list("presets")["items"]) == 1
        and len(repo.list("execution-policies")["items"]) == 1
    )
    other = repo.create_defaults_if_empty("setup-defaults-" + "b" * 32, *_writes())
    assert other["state"] == "kept"  # a second setup never adds or edits defaults
    assert repo.defaults_state("setup-defaults-" + "b" * 32)["state"] == "kept"  # type: ignore[index]
    assert repo.defaults_state("setup-defaults-" + "c" * 32) is None


def test_defaults_keep_a_preset_made_elsewhere_and_never_create_half_a_pair(tmp_path) -> None:
    repo = _repo(tmp_path)
    preset, policy = _writes()
    repo.post("/api/v1/presets", "elsewhere-key-0000001", preset.to_wire(), payload=preset)
    result = repo.create_defaults_if_empty("setup-defaults-" + "d" * 32, preset, policy)
    assert result["state"] == "kept"
    assert repo.list("execution-policies")["items"] == []


def test_concurrent_defaults_for_two_plans_create_exactly_one_pair(tmp_path) -> None:
    repo = _repo(tmp_path)
    results: list[str] = []
    barrier = threading.Barrier(4)

    def worker(n: int) -> None:
        barrier.wait()
        results.append(
            repo.create_defaults_if_empty(f"setup-defaults-{n:032d}", *_writes())["state"]
        )

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(results) == ["created", "kept", "kept", "kept"]
    assert len(repo.list("presets")["items"]) == 1


def _verify_plan_body(apply_defaults: bool | None = True) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "profile_kind": "cpu",
        "model_refs": [TURBO],
        "diarization_enabled": False,
        "verification_settings": asr_settings().to_wire(),
    }
    if apply_defaults is not None:
        body["apply_defaults"] = apply_defaults
    return body


def _run_setup_over_http(http, body: dict[str, Any], key: str) -> str:
    created = http.post(
        "/api/v1/setup-plans", json=body, headers={"Idempotency-Key": f"plan-{key}"}
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    approved = http.post(
        f"/api/v1/setup-plans/{plan['id']}/approve",
        json={"schema_version": 1, "plan_hash": plan["plan_hash"], "accepted_agreement_ids": []},
        headers={"Idempotency-Key": f"apr-{key}", "If-Match": f'"{plan["revision"]}"'},
    )
    assert approved.status_code == 201, approved.text
    approval = approved.json()
    started = http.post(
        "/api/v1/setup-operations",
        json={
            "schema_version": 1,
            "plan_ref": {"id": plan["id"], "revision": approval["plan_revision"]},
            "approval_id": approval["id"],
        },
        headers={"Idempotency-Key": f"start-{key}"},
    )
    assert started.status_code == 202, started.text
    return str(started.json()["id"])


def test_http_defaults_follow_the_plan_intent_and_survive_a_lost_answer(http) -> None:
    op_id = _run_setup_over_http(http, _verify_plan_body(True), "f-def-000000001")
    runtime = http.app.state.v1_jobs_runtime
    view = http.get(f"/api/v1/setup-operations/{op_id}/defaults").json()
    assert view["state"] == "waiting"
    assert (
        http.post(
            f"/api/v1/setup-operations/{op_id}/defaults", json={"schema_version": 1}
        ).status_code
        == 409
    )
    store = runtime.store
    queue = http.get("/api/v1/queue").json()
    resumed = http.patch(
        "/api/v1/queue/control",
        json={"schema_version": 1, "dispatch": "running"},
        headers={"If-Match": f'"{queue["revision"]}"', "Idempotency-Key": "queue-f-def-0000001"},
    )
    assert resumed.status_code == 200, resumed.text
    candidate = store.next_dispatchable()
    assert candidate is not None
    runtime.setup_executor.dispatch(*candidate, current_owner())
    assert store.get_setup(op_id).status == "succeeded"
    assert http.get(f"/api/v1/setup-operations/{op_id}/defaults").json()["state"] == "pending"
    before = http.get("/api/v1/presets").json()["items"]
    first = http.post(f"/api/v1/setup-operations/{op_id}/defaults", json={"schema_version": 1})
    # This harness's settings store already holds a Preset and a Policy: they are kept as is
    # (the "created" path into an empty store is covered by the repository tests and F-01).
    assert first.status_code == 200 and first.json()["state"] == "kept"
    assert http.get("/api/v1/presets").json()["items"] == before
    # The answer was "lost": asking again returns the same pair, nothing new is written.
    second = http.post(f"/api/v1/setup-operations/{op_id}/defaults", json={"schema_version": 1})
    assert second.json() == first.json()
    assert http.get(f"/api/v1/setup-operations/{op_id}/defaults").json()["state"] == "kept"


def test_http_verification_plans_never_create_defaults(http) -> None:
    op_id = _run_setup_over_http(http, _verify_plan_body(None), "f-def-000000002")
    assert (
        http.get(f"/api/v1/setup-operations/{op_id}/defaults").json()["state"] == "not_applicable"
    )
    refused = http.post(f"/api/v1/setup-operations/{op_id}/defaults", json={"schema_version": 1})
    assert (
        refused.status_code == 409 and refused.json()["error"]["code"] == "defaults_not_requested"
    )


def test_defaults_intent_needs_a_verification(tmp_path) -> None:
    world = World(tmp_path)
    world.install("cpu")
    with pytest.raises(PlanRejected) as caught:
        build_plan(
            SetupPlanRequest(
                schema_version=1,
                profile_kind="cpu",
                model_refs=(TURBO,),
                diarization_enabled=False,
                apply_defaults=True,
            ),
            world.observer.observe(),
        )
    assert caught.value.code == "defaults_require_verification"


# P2-3: one atomic gate for dispatch and runtime probe


def test_gate_orders_probe_and_dispatch_both_ways(tmp_path) -> None:
    from utteran_gui.execution_gate import ExecutionGate
    from utteran_gui.job_dispatcher import JobDispatcher

    gate = ExecutionGate()
    entered, release = threading.Event(), threading.Event()

    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        entered.set()
        release.wait(5)
        return devices_json(True)

    service = diagnostics(tmp_path, runtime, gate=gate)
    service.refresh(runtime=True)
    assert entered.wait(5) and gate.holder == "probe"

    class Store:
        def next_dispatchable(self):
            return ("setup_" + "0" * 32, 1)

    class Setup:
        calls = 0

        def dispatch(self, *args: Any) -> bool:
            Setup.calls += 1
            assert gate.holder == "dispatch"
            return True

    dispatcher = JobDispatcher(
        Store(),  # type: ignore[arg-type]
        prestart=lambda snapshot: None,
        runner=None,  # type: ignore[arg-type]
        owner=current_owner(),
        job_dir=tmp_path,
        setup=Setup(),
        gate=gate,
    )
    assert dispatcher.run_once() is False and Setup.calls == 0  # queued setup waits
    release.set()
    assert service.wait(5) and gate.holder is None
    assert dispatcher.run_once() is True and Setup.calls == 1 and gate.holder is None
    # The other order: while a dispatch holds the gate, a probe does not start.
    assert gate.try_acquire("dispatch")
    blocked = diagnostics(tmp_path, lambda p, r: pytest.fail("must not probe"), gate=gate)
    blocked.refresh(runtime=True)
    assert blocked.wait(5)
    assert blocked.snapshot()["runtime"]["reason_code"] == "execution_running"
    gate.release("dispatch")


def test_gate_is_released_when_the_probe_or_the_dispatch_raises(tmp_path) -> None:
    from utteran_gui.execution_gate import ExecutionGate
    from utteran_gui.job_dispatcher import JobDispatcher

    gate = ExecutionGate()

    def boom(profile: str, refresh: bool) -> dict[str, Any]:
        raise OSError("synthetic")

    service = diagnostics(tmp_path, boom, gate=gate)
    service.refresh(runtime=True)
    assert service.wait(5) and gate.holder is None

    class Store:
        def next_dispatchable(self):
            return ("setup_" + "0" * 32, 1)

    class Setup:
        def dispatch(self, *args: Any) -> bool:
            raise RuntimeError("synthetic")

    dispatcher = JobDispatcher(
        Store(),  # type: ignore[arg-type]
        prestart=lambda snapshot: None,
        runner=None,  # type: ignore[arg-type]
        owner=current_owner(),
        job_dir=tmp_path,
        setup=Setup(),
        gate=gate,
    )
    with pytest.raises(RuntimeError):
        dispatcher.run_once()
    assert gate.holder is None


# P2-4: CUDA is judged from the cuda profile only


def test_cpu_profile_only_keeps_the_nvidia_recommendation(tmp_path) -> None:
    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        raise AssertionError("a cpu venv must not answer for CUDA")

    service = diagnostics(tmp_path, runtime, profiles=("cpu",))
    service.refresh(runtime=True)
    assert service.wait(5)
    view = service.snapshot()
    assert view["runtime"]["reason_code"] == "cuda_profile_missing"
    assert view["recommendation"]["profile"] == "cuda"
    assert "runtime_not_confirmed" in view["recommendation"]["reason_codes"]


def test_an_incompatible_cuda_profile_is_not_probed(tmp_path) -> None:
    class StaleCli(Cli):
        def profile_info(self, name: str) -> Any:
            return type("Info", (), {"exists": name == "cuda", "compatible": False})()

    service = SetupDiagnostics(
        StaleCli(tmp_path),
        probes=DiagnosticProbes(
            gpu=lambda: gpu("nvidia"),
            memory=lambda: hardware.MemoryReport(None, None),
            disk=lambda _p: hardware.DiskReport(None),
            vcredist=lambda: VcRedist("present"),
            runtime=lambda p, r: pytest.fail("must not probe"),
        ),
    )
    service.refresh(runtime=True)
    assert service.wait(5)
    assert service.snapshot()["runtime"]["reason_code"] == "cuda_profile_incompatible"


def test_a_new_runtime_pass_never_shows_the_previous_answer(tmp_path) -> None:
    answers = iter([devices_json(False), devices_json(True)])
    gate_open = threading.Event()

    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        value = next(answers)
        if value["ctranslate2"]["cuda_devices"][0]["usable"]:
            gate_open.wait(5)
        return value

    service = diagnostics(tmp_path, runtime)
    service.refresh(runtime=True)
    assert service.wait(5)
    assert service.snapshot()["recommendation"]["profile"] == "cpu"  # cuda profile said no
    service.refresh(runtime=True, force_runtime=True)
    during = service.snapshot()
    assert during["runtime"] is None and during["recommendation"]["profile"] == "cuda"
    gate_open.set()
    assert service.wait(5)
    assert service.snapshot()["runtime"]["first_attempt"]["status"] == "completed"


# P2-2: CUDA evidence bound to one provable GPU


def test_cuda_hardware_identity_only_for_one_gpu_without_visibility_variables(monkeypatch) -> None:
    from utteran.capabilities import fingerprints

    current = {"lib": FakeNvml([GPU_A])}
    monkeypatch.setattr(nvml, "load_nvml", lambda: current["lib"])
    for name in fingerprints.CUDA_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    one = fingerprints.hardware_fingerprint("cuda")
    assert one is not None
    current["lib"] = FakeNvml([GPU_B])  # GPU replaced: a different scope
    assert fingerprints.hardware_fingerprint("cuda") not in {None, one}
    current["lib"] = FakeNvml([GPU_A, GPU_B])  # ordinal no longer provable: unknown
    assert fingerprints.hardware_fingerprint("cuda") is None
    current["lib"] = FakeNvml([GPU_A])
    monkeypatch.setenv("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
    assert fingerprints.hardware_fingerprint("cuda") is None


def test_smoke_attests_only_ordinal_zero_of_the_single_gpu(monkeypatch) -> None:
    from utteran.capabilities import fingerprints
    from utteran.capabilities import smoke as smoke_module

    monkeypatch.setattr(nvml, "load_nvml", lambda: FakeNvml([GPU_A]))
    for name in fingerprints.CUDA_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(nvml, "cuda_ordinal_uuid", lambda ordinal: GPU_A[0])
    assert smoke_module._attested_gpu(0) == GPU_A[0]
    assert smoke_module._attested_gpu(1) is None
    monkeypatch.setattr(nvml, "cuda_ordinal_uuid", lambda ordinal: GPU_B[0])  # swapped
    assert smoke_module._attested_gpu(0) is None
    monkeypatch.setattr(nvml, "load_nvml", lambda: FakeNvml([GPU_A, GPU_B]))
    monkeypatch.setattr(nvml, "cuda_ordinal_uuid", lambda ordinal: GPU_A[0])
    assert smoke_module._attested_gpu(0) is None


def test_vram_peak_is_the_maximum_sample_and_unknown_without_samples(monkeypatch) -> None:
    from utteran.capabilities import smoke as smoke_module

    samples = iter([100, 900, 300] + [200] * 1000)
    opened: list[int] = []
    monkeypatch.setattr(nvml.UsageSampler, "__enter__", lambda self: (opened.append(1), self)[1])
    monkeypatch.setattr(nvml.UsageSampler, "__exit__", lambda self, *exc: None)
    monkeypatch.setattr(nvml.UsageSampler, "read", lambda self: next(samples))
    monkeypatch.setattr(smoke_module._VramPeak, "INTERVAL", 0.001)
    with smoke_module._VramPeak(GPU_A[0]) as sampler:
        import time as _time

        _time.sleep(0.05)
    assert sampler.peak == 900 and opened == [1]  # one NVML session for all samples
    monkeypatch.setattr(nvml.UsageSampler, "read", lambda self: None)
    with smoke_module._VramPeak(GPU_A[0]) as none:
        pass
    assert none.peak is None  # WDDM N/A / not measurable -> unknown


def test_devices_never_retries_late_in_a_pass(monkeypatch) -> None:
    from utteran import devices

    calls: list[str] = []

    def probe(name, label, timeout, *, argument=None):
        calls.append(name)
        if name == "ctranslate2_cuda_count":
            return devices._ProbeRun(devices.ProbeOutcome(name, label, "timeout", timeout), None)
        return devices._ProbeRun(devices.ProbeOutcome(name, label, "completed", 0.1), {})

    monkeypatch.setattr(devices, "run_isolated_probe", probe)
    monkeypatch.setattr(devices.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(devices, "RETRY_WINDOW_SECONDS", 10.0)  # a 20 s retry would not fit
    devices._detect_isolated_runtimes(20.0, None)
    assert calls.count("ctranslate2_cuda_count") == 1


def test_a_payload_probe_timeout_is_not_rerun_by_the_diagnostics(tmp_path) -> None:
    calls: list[int] = []

    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        calls.append(1)
        return devices_json(None)  # devices.py already retried inside; reported timeout

    service = diagnostics(tmp_path, runtime)
    service.refresh(runtime=True)
    assert service.wait(5)
    assert len(calls) == 1
    assert service.snapshot()["runtime"]["reason_code"] == "probe_timeout"


def test_real_timeout_to_budget_ratio_allows_the_cold_rerun(tmp_path) -> None:
    import time as _time

    calls: list[int] = []

    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 1:
            _time.sleep(0.05)  # the whole run used its full timeout
            raise subprocess.TimeoutExpired("devices", 0.05)
        return devices_json(True)

    service = diagnostics(tmp_path, runtime, runtime_timeout=0.05)  # default budget formula
    service.refresh(runtime=True)
    assert service.wait(5)
    assert len(calls) == 2 and service.snapshot()["runtime"]["attempts"] == 2


def test_an_empty_cuda_visibility_variable_is_not_unset(monkeypatch) -> None:
    from utteran.capabilities import fingerprints

    monkeypatch.setattr(nvml, "load_nvml", lambda: FakeNvml([GPU_A]))
    monkeypatch.delenv("CUDA_DEVICE_ORDER", raising=False)
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert fingerprints.hardware_fingerprint("cuda") is None


def test_a_held_heavy_slot_without_a_live_executor_blocks_the_probe(http) -> None:
    runtime = http.app.state.v1_jobs_runtime
    http.diagnostics.probes = DiagnosticProbes(
        gpu=lambda: gpu("nvidia"),
        memory=lambda: hardware.MemoryReport(None, None),
        disk=lambda _p: hardware.DiskReport(None),
        vcredist=lambda: VcRedist("present"),
        runtime=lambda p, r: pytest.fail("must not probe beside a possibly live child"),
    )
    http.diagnostics.cli = Cli(Path("."), ("cuda",))
    runtime.store.active_job = lambda: ("setup_" + "1" * 32, 3)  # e.g. ChildUnconfirmed lease
    started = http.post("/api/v1/setup-diagnostics/refresh", json={"schema_version": 1})
    assert started.status_code == 202
    assert http.diagnostics.wait(5)
    assert http.get("/api/v1/setup-diagnostics").json()["runtime"]["reason_code"] == "setup_running"


def test_defaults_view_never_creates_the_settings_store_and_rejects_a_bad_record(tmp_path) -> None:
    from utteran_gui import setup_defaults

    class Op:
        status = "succeeded"
        plan_ref = type("R", (), {"id": "plan_" + "e" * 32})()

    class Content:
        apply_defaults = True
        verification = object()

    class Store:
        def get_setup(self, _id: str) -> Any:
            return Op()

        def get_plan_content(self, _id: str) -> Any:
            return Content()

    missing = tmp_path / "none" / "resources.sqlite3"
    view = setup_defaults.defaults_view(Store(), missing, "setup_x")  # type: ignore[arg-type]
    assert view["state"] == "pending" and not missing.parent.exists()
    repo = _repo(tmp_path)
    repo.create_defaults_if_empty("setup-defaults-" + "e" * 32, *_writes())
    path = tmp_path / "settings" / "resources.sqlite3"
    import sqlite3 as _sqlite

    with _sqlite.connect(path) as db:
        db.execute("UPDATE replays SET body='{}' WHERE endpoint='/api/v1/setup-defaults'")
    from utteran_settings.errors import SettingsError

    with pytest.raises(SettingsError):
        setup_defaults.defaults_view(Store(), path, "setup_x")  # type: ignore[arg-type]


def test_gate_rechecks_lease_in_acquired_window(tmp_path):
    from utteran_gui.execution_gate import ExecutionGate

    world = World(tmp_path)
    world.install("cuda")
    busy = iter((None, "retained-lease"))
    gate = ExecutionGate()
    service = SetupDiagnostics(
        world.cli,
        busy_profile=lambda: next(busy),
        gate=gate,
    )
    service._probe = lambda *_: pytest.fail("probe overlapped retained lease")
    assert service._runtime_stage(0, False).reason_code == "setup_running"
    assert gate.try_acquire("dispatch")
    gate.release("dispatch")
