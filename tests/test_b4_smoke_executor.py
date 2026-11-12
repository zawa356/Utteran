"""B4 explicit smoke: child orchestration with fake runtimes, and the GUI-side launcher.

No model, GPU, network or real inference: the runtime is a fake, model files are empty
placeholders below ``tmp_path`` and the WAV fixture is generated synthetic tones.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from utteran.capabilities.collector import collect_managed_deployments
from utteran.capabilities.evaluator import digest, evaluate_candidate
from utteran.capabilities.evidence import (
    apply_smoke_evidence,
    evidence_directory,
    inspect_evidence,
    seal_evidence,
    smoke_identity,
    write_evidence,
)
from utteran.capabilities.smoke import (
    FIXTURE_NAME,
    RuntimeReport,
    SmokeFailure,
    SmokeRequest,
    fixture_bytes,
    run_smoke,
    write_fixture,
)
from utteran.models.catalog import get_model
from utteran.models.manager import ModelManager
from utteran.profiles import PROFILE_EXTRAS
from utteran_contracts import ProcessingSettings
from utteran_gui import capability_smoke
from utteran_gui.capability_smoke import SmokeLauncher, main
from utteran_gui.capability_transport import BridgeReadError, read_bridge
from utteran_gui.cli import CliAdapter, ProfileInfo
from utteran_gui.instance_guard import GuiInstanceLock, lock_directory
from utteran_paths import DEV_MARKER_CONTENT, DEV_MARKER_NAME, DataPaths

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 11, 12, 3, 0, tzinfo=UTC)
RUNTIME = digest("synthetic runtime identity")
HARDWARE = digest("synthetic hardware identity")


def fixed(device: str) -> tuple[str | None, str | None]:
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


class Env:
    """A marked dev root with a compatible synthetic profile and a placeholder model."""

    def __init__(self, tmp_path: Path, profile: str = "cpu", model: str = "tiny") -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "dev-root"
        self.root.mkdir()
        (self.root / DEV_MARKER_NAME).write_text(DEV_MARKER_CONTENT, encoding="utf-8")
        self.paths = DataPaths(root=self.root, project_root=self.root)
        self.repo = tmp_path / "repo"
        self.repo.mkdir()
        lock = self.repo / "uv.lock"
        lock.write_text("synthetic lock", encoding="utf-8")
        self.profile = profile
        self.profile_root = tmp_path / "profile"
        self.profile_root.mkdir()
        (self.profile_root / ".utteran-profile.json").write_text(
            json.dumps(
                dict(
                    schema_version=1,
                    lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
                    extras=list(PROFILE_EXTRAS[profile]),
                    venv_path=str(self.profile_root.resolve()),
                )
            ),
            encoding="utf-8",
        )
        self.model = model
        self.model_dir = ModelManager(self.paths.models).managed_path(
            get_model(model, backend="faster-whisper")
        )
        self.model_dir.mkdir(parents=True)
        for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
            (self.model_dir / name).write_text("{}", encoding="utf-8")
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.directory = evidence_directory(self.paths)
        assert self.directory is not None

    def request(self, settings: ProcessingSettings, recipe: str = "fw-cpu", **updates: Any):
        return SmokeRequest.model_validate(
            dict(
                schema_version=1,
                profile_id=self.profile,
                recipe_id=recipe,
                model_id=self.model,
                settings=settings.to_wire(),
                work_dir=str(self.work),
            )
            | updates
        )

    def smoke(self, settings, runtime, recipe="fw-cpu", clock=None, fresh=True, **updates):
        if fresh:  # the launcher creates a new empty work directory for every run
            shutil.rmtree(self.work)
            self.work.mkdir()
        times = iter(clock or [NOW + timedelta(seconds=i) for i in range(10)])
        return run_smoke(
            self.request(settings, recipe, **updates),
            runtime=runtime,
            now=lambda: next(times),
            paths=self.paths,
            profile_root=self.profile_root,
            repo_root=self.repo,
            fingerprints=fixed,
        )

    def candidate(self, settings, recipe="fw-cpu", now=NOW + timedelta(minutes=1)):
        deployments = collect_managed_deployments(
            self.profile,
            settings,
            "worker",
            now=now,
            profile_root=self.profile_root,
            repo_root=self.repo,
            model_root=self.paths.models,
            native_root=self.paths.native,
        )
        merged = apply_smoke_evidence(
            deployments, settings, now=now, directory=self.directory, fingerprints=fixed
        )
        (d,) = [
            d
            for d in merged
            if d.identity.recipe_id == recipe and d.identity.model_id == self.model
        ]
        return evaluate_candidate(d, settings, "worker", now=now)

    def scope(self, settings, recipe="fw-cpu"):
        from utteran.capabilities.evaluator import evidence_fingerprint

        deployments = collect_managed_deployments(
            self.profile,
            settings,
            "worker",
            now=NOW,
            profile_root=self.profile_root,
            repo_root=self.repo,
            model_root=self.paths.models,
            native_root=self.paths.native,
        )
        (d,) = [
            x
            for x in deployments
            if x.identity.recipe_id == recipe and x.identity.model_id == self.model
        ]
        identity = smoke_identity(d.identity, fixed)
        assert identity is not None
        return evidence_fingerprint(identity, settings)


class FakeRuntime:
    """Explicit synthetic read-back. NOT a real inference result."""

    def __init__(self, **report: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.report = (
            dict(
                device="cpu",
                device_indices=(0,),
                compute_type="int8",
                duration_seconds=3.0,
                required_memory_bytes=400_000_000,
                available_memory_bytes=8_000_000_000,
            )
            | report
        )
        self.during: Any = None

    def run(self, **kwargs: Any) -> RuntimeReport:
        self.calls.append(kwargs)
        if self.during is not None:
            self.during(kwargs)
        failure = self.report.get("raise")
        if failure is not None:
            raise failure
        return RuntimeReport(**{k: v for k, v in self.report.items() if k != "raise"})


def issue_codes(candidate) -> set[str]:
    return {i.code for i in candidate.issues}


# --------------------------------------------------------------------------- fixture


def test_fixture_is_deterministic_short_synthetic_pcm(tmp_path) -> None:
    import wave

    first = write_fixture(tmp_path / "a.wav")
    second = write_fixture(tmp_path / "b.wav")
    assert first == second == hashlib.sha256((tmp_path / "a.wav").read_bytes()).hexdigest()
    with wave.open(str(tmp_path / "a.wav"), "rb") as source:
        assert (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (
            1,
            2,
            16000,
        )
        assert source.getnframes() == 48000
    pcm = fixture_bytes()
    assert len(pcm) == 96000
    peak = max(
        abs(int.from_bytes(pcm[k : k + 2], "little", signed=True)) for k in range(0, 96000, 2)
    )
    assert peak > 1000  # not silence


# --------------------------------------------------------------------------- child: success


def test_cpu_smoke_records_attested_route_and_reaches_ready(settings, tmp_path) -> None:
    env = Env(tmp_path)
    runtime = FakeRuntime()
    result = env.smoke(settings, runtime)
    assert (result.outcome, result.evidence) == ("passed", "recorded")
    (call,) = runtime.calls
    assert call["device"] == "cpu" and call["compute_type"] == "int8"
    assert call["model_dir"] == env.model_dir and call["audio"] == env.work / FIXTURE_NAME
    assert call["language"] == "en" and call["beam_size"] == settings.asr.beam_size
    candidate = env.candidate(settings)
    assert candidate.runnable == "ready", issue_codes(candidate)
    smoke = next(o for o in candidate.observations if o.kind == "smoke")
    assert (smoke.encoder_observed, smoke.decoder_observed) == ("cpu", "cpu")
    raw = next(env.directory.glob("*.json")).read_text(encoding="utf-8")
    for private in ("tiny", str(tmp_path), "initial_prompt", "language"):
        assert private not in raw


def test_cuda_smoke_attests_index_but_stays_unknown(tmp_path, settings) -> None:
    env = Env(tmp_path, profile="cuda")
    runtime = FakeRuntime(
        device="cuda",
        device_indices=(0,),
        compute_type="int8",
        required_memory_bytes=None,
        available_memory_bytes=None,
    )
    result = env.smoke(settings, runtime, recipe="fw-cuda")
    assert (result.outcome, result.evidence) == ("passed", "recorded")
    assert runtime.calls[0]["device"] == "cuda" and runtime.calls[0]["device_index"] == 0
    candidate = env.candidate(settings, "fw-cuda")
    assert candidate.runnable == "unknown"
    assert {"evidence_scope_incomplete", "memory_unknown"} <= issue_codes(candidate)
    smoke = next(o for o in candidate.observations if o.kind == "smoke")
    assert smoke.state == "passed"
    assert (smoke.encoder_device_index_observed, smoke.decoder_device_index_observed) == (0, 0)


# --------------------------------------------------------------------------- child: route


@pytest.mark.parametrize(
    "report,outcome,runnable,code",
    [
        (dict(device=""), "route_unverified", "unknown", "smoke_route_unverified"),
        (dict(device="cuda"), "route_mismatch", "blocked", "smoke_route_mismatch"),
    ],
)
def test_route_readback_is_never_replaced_by_the_request(
    settings, tmp_path, report, outcome, runnable, code
) -> None:
    env = Env(tmp_path)
    result = env.smoke(settings, FakeRuntime(**report))
    assert result.outcome == outcome
    candidate = env.candidate(settings)
    assert candidate.runnable == runnable and code in issue_codes(candidate)


def test_only_the_documented_cpu_int8_alias_is_accepted(settings, tmp_path) -> None:
    env = Env(tmp_path)
    assert env.smoke(settings, FakeRuntime(compute_type="int8_float32")).outcome == "passed"
    assert env.smoke(settings, FakeRuntime(compute_type="int16")).outcome == "compute_mismatch"
    (tmp_path / "cuda").mkdir()
    cuda = Env(tmp_path / "cuda", profile="cuda")
    report = dict(
        device="cuda", device_indices=(0,), required_memory_bytes=None, available_memory_bytes=None
    )
    result = cuda.smoke(settings, FakeRuntime(compute_type="int8_float32", **report), "fw-cuda")
    assert result.outcome == "compute_mismatch"


@pytest.mark.parametrize("indices", [(), (0, 1)])
def test_cuda_index_not_single_is_unverified(settings, tmp_path, indices) -> None:
    env = Env(tmp_path, profile="cuda")
    result = env.smoke(
        settings,
        FakeRuntime(
            device="cuda",
            device_indices=indices,
            required_memory_bytes=None,
            available_memory_bytes=None,
        ),
        recipe="fw-cuda",
    )
    assert result.outcome == "route_unverified"
    assert "smoke_route_unverified" in issue_codes(env.candidate(settings, "fw-cuda"))


# --------------------------------------------------------------------------- child: failures


@pytest.mark.parametrize(
    "report,outcome",
    [
        (dict(raise_=SmokeFailure("model_load_failed")), "model_load_failed"),
        (dict(raise_=SmokeFailure("out_of_memory")), "out_of_memory"),
        (dict(raise_=SmokeFailure("backend_unavailable")), "backend_unavailable"),
        (dict(raise_=SmokeFailure("runtime_mismatch")), "runtime_mismatch"),
        (
            dict(
                raise_=RuntimeError(
                    "PRIVATE_TRACE " + "/".join(("", "ho" + "me", "x")) + " transcript"
                )
            ),
            "inference_failed",
        ),
        (dict(compute_type="float32"), "compute_mismatch"),
        (dict(duration_seconds=0.0), "inference_failed"),
    ],
)
def test_failures_are_recorded_and_replace_an_earlier_success(
    settings, tmp_path, report, outcome
) -> None:
    env = Env(tmp_path)
    assert env.smoke(settings, FakeRuntime()).outcome == "passed"
    assert env.candidate(settings).runnable == "ready"
    report = {("raise" if k == "raise_" else k): v for k, v in report.items()}
    result = env.smoke(
        settings, FakeRuntime(**report), clock=[NOW + timedelta(seconds=30 + i) for i in range(10)]
    )
    assert (result.outcome, result.evidence) == (outcome, "invalidated")
    assert "PRIVATE" not in repr(result) and "PRIVATE" not in json.dumps(result.to_wire())
    scope = env.scope(settings)
    assert inspect_evidence(env.directory, scope, now=NOW + timedelta(minutes=1))[0] == "not_passed"
    assert env.candidate(settings).runnable != "ready"


def test_pending_record_is_written_before_inference(settings, tmp_path) -> None:
    env = Env(tmp_path)
    env.smoke(settings, FakeRuntime())
    seen: list[str] = []
    runtime = FakeRuntime()

    def during(_kwargs: Any) -> None:
        seen.append(inspect_evidence(env.directory, env.scope(settings), now=NOW)[0])
        # Simulate a kill/timeout here: the run never reaches the final write.
        raise KeyboardInterrupt

    runtime.during = during
    with pytest.raises(KeyboardInterrupt):
        env.smoke(settings, runtime)
    assert seen == ["not_passed"]
    assert env.candidate(settings).runnable != "ready"


@pytest.mark.parametrize(
    "mutation,outcome",
    [
        ("model_missing", "model_missing"),
        ("model_incomplete", "model_missing"),
        ("lock_changed", "profile_incompatible"),
        ("diarization", "settings_unsupported"),
        ("glossary", "settings_unsupported"),
        ("unknown_model", "recipe_unavailable"),
    ],
)
def test_prechecks_fail_closed_without_inference_or_evidence(
    settings, tmp_path, mutation, outcome
) -> None:
    env = Env(tmp_path)
    updates: dict[str, Any] = {}
    if mutation == "model_missing":
        for p in env.model_dir.iterdir():
            p.unlink()
        env.model_dir.rmdir()
    elif mutation == "model_incomplete":
        (env.model_dir / "tokenizer.json").unlink()
    elif mutation == "lock_changed":
        (env.repo / "uv.lock").write_text("other lock", encoding="utf-8")
    elif mutation == "diarization":
        data = settings.to_wire()
        data["diarization"]["enabled"] = True
        settings = ProcessingSettings.model_validate(data)
    elif mutation == "glossary":
        data = settings.to_wire()
        data["asr"]["glossary"] = ["term"]
        settings = ProcessingSettings.model_validate(data)
    elif mutation == "unknown_model":
        updates["model_id"] = "not-in-catalog"
    runtime = FakeRuntime()
    result = env.smoke(settings, runtime, **updates)
    assert (result.outcome, result.evidence) == (outcome, "none")
    assert runtime.calls == []
    assert not env.directory.exists()


def test_root_and_work_dir_requirements(settings, tmp_path) -> None:
    env = Env(tmp_path)
    (env.root / DEV_MARKER_NAME).unlink()
    assert env.smoke(settings, FakeRuntime()).outcome == "dev_root_invalid"
    (env.root / DEV_MARKER_NAME).write_text(DEV_MARKER_CONTENT, encoding="utf-8")
    (env.work / "x").write_text("", encoding="utf-8")
    assert env.smoke(settings, FakeRuntime(), fresh=False).outcome == "request_invalid"
    assert env.smoke(settings, FakeRuntime(), work_dir="relative").outcome == "request_invalid"
    unrooted = run_smoke(
        env.request(settings),
        runtime=FakeRuntime(),
        now=lambda: NOW,
        paths=DataPaths(root=None, project_root=tmp_path),
        profile_root=env.profile_root,
        repo_root=env.repo,
        fingerprints=fixed,
    )
    assert unrooted.outcome == "dev_root_invalid"


def test_evidence_limit_stops_before_inference(settings, tmp_path) -> None:
    env = Env(tmp_path)
    for index in range(64):
        write_evidence(
            env.directory,
            seal_evidence(
                scope=digest(index),
                recipe_id="fw-cpu",
                state="failed",
                outcome="inference_failed",
                observed_at=NOW,
                expires_at=NOW + timedelta(hours=1),
                encoder_observed="unknown",
                decoder_observed="unknown",
                encoder_device_index_observed=None,
                decoder_device_index_observed=None,
                required_memory_bytes=None,
                available_memory_bytes=None,
                fixture_sha256=digest("f"),
            ),
        )
    runtime = FakeRuntime()
    assert env.smoke(settings, runtime).outcome == "evidence_limit"
    assert runtime.calls == []


def test_smoke_writes_only_evidence_and_fixture(settings, tmp_path) -> None:
    env = Env(tmp_path)
    before = {p for p in tmp_path.rglob("*")}
    env.smoke(settings, FakeRuntime())
    added = {p for p in tmp_path.rglob("*")} - before
    assert {p.relative_to(tmp_path).as_posix() for p in added} == {
        "dev-root/cache",
        "dev-root/cache/capability-evidence-v1",
        f"dev-root/cache/capability-evidence-v1/{env.scope(settings)}.json",
        f"work/{FIXTURE_NAME}",
    }


def test_child_entry_is_offline_quiet_and_value_free(settings, tmp_path) -> None:
    """Real child process, fake-free: an unmarked root is refused before any model work.

    Also checks the offline flags are forced, logging is silent and nothing reaches
    the network or spawns a process (audit hook)."""
    root = tmp_path / "unmarked"
    root.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    request = dict(
        schema_version=1,
        profile_id="cpu",
        recipe_id="fw-cpu",
        model_id="tiny",
        settings=settings.to_wire(),
        work_dir=str(work),
    )
    code = r"""
import os, sys
def audit(event, args):
    if event.startswith(('subprocess.', 'os.system', 'os.fork', 'socket.connect')):
        raise AssertionError(event)
sys.addaudithook(audit)
from utteran.capabilities import smoke
smoke.main(['run'])
for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
    assert os.environ[key] == '1'
assert not {'ctranslate2', 'faster_whisper', 'torch'} & set(sys.modules)
"""
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "UTTERAN_DATA_ROOT": str(root),
        "UTTERAN_PROFILE": "cpu",
    }
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        input=json.dumps(request).encode(),
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "schema_version": 1,
        "outcome": "dev_root_invalid",
        "evidence": "none",
    }
    assert completed.stderr == b""
    assert sorted(p.name for p in root.iterdir()) == []
    bad = subprocess.run(
        [sys.executable, "-B", "-m", "utteran.capabilities.smoke", "run"],
        input=b'{"PRIVATE_TOKEN":"secret"}',
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert json.loads(bad.stdout)["outcome"] == "request_invalid"
    assert b"PRIVATE" not in bad.stdout and bad.stderr == b""
    wrong = subprocess.run(
        [sys.executable, "-B", "-m", "utteran.capabilities.smoke", "run"],
        input=json.dumps(request).encode(),
        env={**environment, "UTTERAN_PROFILE": "cuda"},
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
    )
    assert json.loads(wrong.stdout)["outcome"] == "profile_mismatch"


def test_production_entry_has_no_runtime_injection() -> None:
    tree = ast.parse((ROOT / "src/utteran/capabilities/smoke.py").read_text(encoding="utf-8"))
    main_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    calls = [
        n
        for n in ast.walk(main_fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "run_smoke"
    ]
    (call,) = calls
    runtime = next(k.value for k in call.keywords if k.arg == "runtime")
    assert isinstance(runtime, ast.Call) and runtime.func.id == "FasterWhisperSmokeRuntime"
    assert "fingerprints" not in {k.arg for k in call.keywords}
    parser_args = [
        n
        for n in ast.walk(main_fn)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "add_argument"
    ]
    assert len(parser_args) == 1  # only the fixed "run" action


# --------------------------------------------------------------------------- GUI launcher


def marked_root(tmp_path: Path, name: str = "dev-root") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / DEV_MARKER_NAME).write_text(DEV_MARKER_CONTENT, encoding="utf-8")
    return root


class FakeLauncher:
    instances: ClassVar[list[FakeLauncher]] = []

    def __init__(self, cli: CliAdapter, code: str = "passed", during=None) -> None:
        self.cli = cli
        self.code = code
        self.during = during
        self.calls: list[dict[str, Any]] = []
        FakeLauncher.instances.append(self)

    def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if self.during is not None:
            self.during(kwargs)
        return self.code


def run_main(settings, environ, *, launcher=FakeLauncher, argv=None):
    out = io.StringIO()
    code = main(
        argv or ["--profile", "cpu", "--recipe", "fw-cpu", "--model", "tiny"],
        stdin=io.BytesIO(json.dumps(settings.to_wire()).encode()),
        stdout=out,
        environ=environ,
        launcher_factory=launcher,
        repo_root=ROOT,
    )
    return code, out.getvalue()


def no_distribution(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items() if k != "UTTERAN_DISTRIBUTION"}


@pytest.mark.parametrize(
    "case,expected",
    [
        ("unset", "dev_root_required"),
        ("relative", "dev_root_not_absolute"),
        ("missing", "dev_root_missing"),
        ("empty_unmarked", "dev_root_not_marked"),
        ("data_unmarked", "dev_root_not_marked"),
        ("distribution", "dev_root_distribution"),
    ],
)
def test_launcher_refuses_without_marked_dev_root(settings, tmp_path, case, expected) -> None:
    env = no_distribution(dict(os.environ))
    env.pop("UTTERAN_DATA_ROOT", None)
    target = tmp_path / "root"
    if case == "relative":
        env["UTTERAN_DATA_ROOT"] = "relative/root"
    elif case == "missing":
        env["UTTERAN_DATA_ROOT"] = str(target)
    elif case in {"empty_unmarked", "data_unmarked", "distribution"}:
        target.mkdir()
        if case == "data_unmarked":
            (target / "existing-user-data.txt").write_text("keep", encoding="utf-8")
        if case == "distribution":
            (target / DEV_MARKER_NAME).write_text(DEV_MARKER_CONTENT, encoding="utf-8")
            os.environ["UTTERAN_DISTRIBUTION"] = "portable"
        env["UTTERAN_DATA_ROOT"] = str(target)
    FakeLauncher.instances.clear()
    try:
        code, output = run_main(settings, env)
    finally:
        os.environ.pop("UTTERAN_DISTRIBUTION", None)
    assert code == 2 and f"[{expected}]" in output
    assert FakeLauncher.instances == []
    if target.exists():
        # Never creates a marker or anything else.
        names = sorted(p.name for p in target.iterdir())
        assert DEV_MARKER_NAME not in names or case == "distribution"
        assert "gui-instance.lock" not in names


def test_launcher_refuses_while_gui_holds_root_and_roots_are_independent(
    settings, tmp_path
) -> None:
    first, second = marked_root(tmp_path, "a"), marked_root(tmp_path, "b")
    env = no_distribution(dict(os.environ))
    with GuiInstanceLock(lock_directory(first)):
        FakeLauncher.instances.clear()
        code, output = run_main(settings, {**env, "UTTERAN_DATA_ROOT": str(first)})
        assert code == 2 and "[gui_already_running]" in output and not FakeLauncher.instances
        code, output = run_main(settings, {**env, "UTTERAN_DATA_ROOT": str(second)})
        assert code == 0 and "[passed]" in output


def test_second_smoke_on_same_root_is_refused(settings, tmp_path) -> None:
    root = marked_root(tmp_path)
    env = {**no_distribution(dict(os.environ)), "UTTERAN_DATA_ROOT": str(root)}
    nested: list[tuple[int, str]] = []

    def during(_kwargs: Any) -> None:
        nested.append(run_main(settings, env))
        # The running smoke holds the GUI lock, so a GUI cannot start either.
        with pytest.raises(Exception, match="gui_already_running"):
            GuiInstanceLock(lock_directory(root)).acquire()

    code, _output = run_main(
        settings, env, launcher=lambda cli: FakeLauncher(cli, "passed", during)
    )
    assert code == 0 and nested and nested[0][0] == 2
    assert "[gui_already_running]" in nested[0][1]
    with GuiInstanceLock(lock_directory(root)):
        pass  # released afterwards


def test_launcher_rejects_bad_settings_and_reports_fixed_codes(settings, tmp_path) -> None:
    root = marked_root(tmp_path)
    env = {**no_distribution(dict(os.environ)), "UTTERAN_DATA_ROOT": str(root)}
    out = io.StringIO()
    code = main(
        ["--profile", "cpu", "--recipe", "fw-cpu", "--model", "tiny"],
        stdin=io.BytesIO(b'{"initial_prompt":"PRIVATE"}'),
        stdout=out,
        environ=env,
        launcher_factory=FakeLauncher,
        repo_root=ROOT,
    )
    assert code == 2 and "[smoke_request_invalid]" in out.getvalue()
    assert "PRIVATE" not in out.getvalue()
    code, output = run_main(settings, env, launcher=lambda cli: FakeLauncher(cli, "model_missing"))
    assert code == 1 and output == "utteran capability smoke: [model_missing]\n"
    for argv in (
        ["--profile", "gpu", "--recipe", "fw-cpu", "--model", "tiny"],
        ["--profile", "cpu", "--recipe", "wc-cpu", "--model", "tiny"],
    ):
        with pytest.raises(SystemExit):
            run_main(settings, env, argv=argv)


@pytest.fixture
def profile_cli(tmp_path, monkeypatch) -> CliAdapter:
    cli = CliAdapter(tmp_path)
    root = tmp_path / "profile"
    (root / "bin").mkdir(parents=True)
    (root / "Scripts").mkdir()
    (root / "bin/python").touch()
    (root / "Scripts/python.exe").touch()
    info = ProfileInfo("cpu", root, True, root / "utteran", compatible=True)
    monkeypatch.setattr(cli, "profile_info", lambda _: info)
    return cli


def test_launcher_command_environment_and_cleanup(settings, tmp_path, profile_cli) -> None:
    root = marked_root(tmp_path)
    evidence = root / "cache" / "capability-evidence-v1"
    evidence.mkdir(parents=True)
    leftover = evidence / f".{'a' * 64}.{'b' * 16}.tmp"
    keep = evidence / f"{'c' * 64}.json"
    calls: list[tuple[list[str], dict[str, Any]]] = []
    secret_name = "HF_" + "TOKEN"

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        leftover.write_text("partial", encoding="utf-8")
        keep.write_text("{}", encoding="utf-8")
        work = Path(json.loads(kwargs["input"])["work_dir"])
        assert work.is_dir()
        (work / FIXTURE_NAME).write_bytes(b"RIFF")
        out = {"schema_version": 1, "outcome": "passed", "evidence": "recorded"}
        return subprocess.CompletedProcess(command, 0, json.dumps(out).encode(), b"PRIVATE_TRACE")

    code = SmokeLauncher(profile_cli, runner=runner).run(
        root=root,
        profile="cpu",
        recipe="fw-cpu",
        model="tiny",
        settings=settings,
        timeout=900,
        environ={secret_name: "hf_" + "x" * 30, "PATH": os.environ.get("PATH", "")},
    )
    assert code == "passed"
    command, kwargs = calls[0]
    assert command[1:] == ["-I", "-B", "-m", "utteran.capabilities.smoke", "run"]
    assert "initial_prompt" not in " ".join(command) and b"initial_prompt" in kwargs["input"]
    assert kwargs["env"]["UTTERAN_DATA_ROOT"] == str(root)
    assert kwargs["env"]["HF_HUB_OFFLINE"] == "1" and secret_name not in kwargs["env"]
    assert kwargs["timeout"] == 900
    assert not Path(json.loads(kwargs["input"])["work_dir"]).exists()
    assert not leftover.exists() and keep.exists()  # only writer temporaries are removed


@pytest.mark.parametrize(
    "failure,expected",
    [
        (BridgeReadError("probe_timeout"), "smoke_timeout"),
        (BridgeReadError("bridge_stdout_limit"), "smoke_output_limit"),
        (BridgeReadError("bridge_stderr_limit"), "smoke_output_limit"),
        (BridgeReadError("bridge_unavailable"), "smoke_unavailable"),
        (subprocess.TimeoutExpired("PRIVATE", 1), "smoke_timeout"),
        (b'{"schema_version":1,"outcome":"PRIVATE","evidence":"none"}', "smoke_invalid"),
        (b"not json PRIVATE", "smoke_invalid"),
    ],
)
def test_launcher_maps_transport_failures(settings, tmp_path, profile_cli, failure, expected):
    root = marked_root(tmp_path)

    def runner(command, **kwargs):
        if isinstance(failure, bytes):
            return subprocess.CompletedProcess(command, 0, failure, b"")
        raise failure

    code = SmokeLauncher(profile_cli, runner=runner).run(
        root=root, profile="cpu", recipe="fw-cpu", model="tiny", settings=settings, timeout=5
    )
    assert code == expected and "PRIVATE" not in code


def test_launcher_bounds_timeout_and_missing_profile(settings, tmp_path, profile_cli) -> None:
    root = marked_root(tmp_path)
    launcher = SmokeLauncher(profile_cli, runner=lambda *a, **k: pytest.fail("not started"))
    for value in (0, -1, float("nan"), 3601):
        assert (
            launcher.run(
                root=root,
                profile="cpu",
                recipe="fw-cpu",
                model="tiny",
                settings=settings,
                timeout=value,
            )
            == "smoke_request_invalid"
        )
    missing = CliAdapter(tmp_path, tmp_path / "no-venvs")
    assert (
        SmokeLauncher(missing).run(
            root=root, profile="cpu", recipe="fw-cpu", model="tiny", settings=settings, timeout=5
        )
        == "profile_missing"
    )


def test_real_child_timeout_reaps_tree_and_cleans_work_dir(settings, tmp_path, profile_cli):
    root = marked_root(tmp_path)
    marker = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        f"open({str(marker)!r}, 'w').write(str(p.pid));"
        "time.sleep(60)"
    )
    seen: dict[str, Any] = {}

    def runner(command, **kwargs):
        seen["work"] = Path(json.loads(kwargs["input"])["work_dir"])
        return read_bridge(
            [sys.executable, "-c", script],
            cwd=tmp_path,
            env={**os.environ},
            input=b"",
            timeout=kwargs["timeout"],
        )

    started = time.monotonic()
    code = SmokeLauncher(profile_cli, runner=runner).run(
        root=root, profile="cpu", recipe="fw-cpu", model="tiny", settings=settings, timeout=3
    )
    assert code == "smoke_timeout" and time.monotonic() - started < 30
    assert not seen["work"].exists()
    deadline = time.monotonic() + 10
    pid = int(marker.read_text()) if marker.exists() else None
    while pid is not None and _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert pid is None or not _alive(pid)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        return "\nState:\tZ" not in status
    except OSError:
        return True


# --------------------------------------------------------------------------- boundaries


def test_gui_launcher_imports_no_core_or_inference() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            """
import sys
import utteran_gui.capability_smoke
assert not any(n in sys.modules for n in (
    'utteran', 'torch', 'openvino', 'ctranslate2', 'faster_whisper', 'pyannote'))
""",
        ],
        capture_output=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    tree = ast.parse((ROOT / "src/utteran_gui/capability_smoke.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "utteran"
        for n in ast.walk(tree)
    )


def test_smoke_is_reachable_only_from_the_explicit_entry() -> None:
    """GUI startup, APIs, B7 and the metadata bridge never reference the smoke paths."""
    hits = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        relative = path.relative_to(ROOT / "src").as_posix()
        if relative in {
            "utteran/capabilities/smoke.py",
            "utteran/capabilities/evidence.py",  # the record format/writer itself
            "utteran_gui/capability_smoke.py",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        if "capability_smoke" in text or "capabilities.smoke" in text or "write_evidence" in text:
            hits.append(relative)
    assert hits == []
    assert capability_smoke.SMOKE_MODULE == "utteran.capabilities.smoke"


def test_child_outcomes_match_the_launcher_allowlist() -> None:
    from utteran.capabilities.smoke import SMOKE_OUTCOMES

    assert SMOKE_OUTCOMES == capability_smoke.CHILD_OUTCOMES
