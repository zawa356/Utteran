"""B4 smoke evidence through B7 admission and prestart (0.1.50).

The real chain is exercised in-process: B7 ``JobAdmission`` → ``BridgeResolutionProvider``
→ GUI ``CapabilityInventoryAdapter`` (scope checks) → profile-side
``bridge.resolve_payload`` → ``local_provider`` → ``apply_smoke_evidence`` → pure
``make_inventory``/``resolve``. Only the subprocess boundary, the profile interpreter
prefix and the runtime/hardware fingerprints are substituted. Evidence is written by
``run_smoke`` with a fake runtime (synthetic, not a real inference result).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from test_b4_smoke_executor import FakeRuntime, fixed
from test_b7_jobs import SESSION, Env
from utteran.capabilities import bridge
from utteran.capabilities import fingerprints as fingerprint_module
from utteran.capabilities.evidence import (
    EVIDENCE_TTL,
    evidence_directory,
    inspect_evidence,
    seal_evidence,
    write_evidence,
)
from utteran.capabilities.smoke import SmokeRequest, run_smoke
from utteran.models.catalog import get_model
from utteran.models.manager import ModelManager
from utteran.profiles import PROFILE_EXTRAS
from utteran_contracts import ProcessingSettings
from utteran_control.store import IdempotencyScope
from utteran_gui.capability_inventory import CapabilityInventoryAdapter
from utteran_gui.cli import CliAdapter, ProfileInfo
from utteran_gui.job_admission import AdmissionError, BridgeResolutionProvider
from utteran_paths import DEV_MARKER_CONTENT, DEV_MARKER_NAME, DataPaths

MODEL = "large-v3-turbo"


def worker_settings(settings: dict[str, Any]) -> None:
    settings["asr"].update(backend="faster-whisper", model=MODEL, glossary=[])
    settings["asr"].pop("extra_arguments", None)
    settings["diarization"]["enabled"] = False


class World:
    def __init__(self, tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp = tmp
        self.root = tmp / "dev-root"
        self.root.mkdir()
        (self.root / DEV_MARKER_NAME).write_text(DEV_MARKER_CONTENT, encoding="utf-8")
        self.paths = DataPaths(root=self.root, project_root=self.root)
        self.repo = tmp / "repo"
        self.repo.mkdir()
        lock = self.repo / "uv.lock"
        lock.write_text("synthetic lock", encoding="utf-8")
        self.profile_root = tmp / "profile"
        (self.profile_root / "bin").mkdir(parents=True)
        (self.profile_root / "Scripts").mkdir()
        (self.profile_root / "bin/python").touch()
        (self.profile_root / "Scripts/python.exe").touch()
        (self.profile_root / ".utteran-profile.json").write_text(
            json.dumps(
                dict(
                    schema_version=1,
                    lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
                    extras=list(PROFILE_EXTRAS["cpu"]),
                    venv_path=str(self.profile_root.resolve()),
                )
            ),
            encoding="utf-8",
        )
        self.model_dir = ModelManager(self.paths.models).managed_path(
            get_model(MODEL, backend="faster-whisper")
        )
        self.model_dir.mkdir(parents=True)
        for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
            (self.model_dir / name).write_text("{}", encoding="utf-8")
        self.directory = evidence_directory(self.paths)
        assert self.directory is not None
        # The profile-side process: its data root, interpreter prefix and cwd.
        monkeypatch.setenv("UTTERAN_DATA_ROOT", str(self.root))
        monkeypatch.setattr(sys, "prefix", str(self.profile_root))
        monkeypatch.chdir(self.repo)
        monkeypatch.setattr(fingerprint_module, "current_fingerprints", fixed)
        self.requests: list[dict[str, Any]] = []
        cli = CliAdapter(self.repo, tmp / "venvs")
        info = ProfileInfo("cpu", self.profile_root, True, self.profile_root / "utteran", True)
        monkeypatch.setattr(
            cli,
            "profile_info",
            lambda profile: (
                info
                if profile == "cpu"
                else ProfileInfo(profile, tmp / "none", False, tmp / "none")
            ),
        )
        self.adapter = CapabilityInventoryAdapter(cli, runner=self.runner)
        self.provider = BridgeResolutionProvider(self.adapter)

    def runner(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        """Stand-in for the profile subprocess: the real metadata-only bridge action."""
        assert command[-2:] == ["utteran.capabilities.bridge", "resolve"]
        self.requests.append(json.loads(kwargs["input"]))
        payload = bridge.resolve_payload(kwargs["input"])
        return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b"")

    def settings(self) -> ProcessingSettings:
        return ProcessingSettings.model_validate(self.requests[-1]["refresh"]["settings"])

    def smoke(self, runtime: FakeRuntime | None = None, *, now: datetime | None = None):
        work = self.tmp / "work"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        request = SmokeRequest.model_validate(
            dict(
                schema_version=1,
                profile_id="cpu",
                recipe_id="fw-cpu",
                model_id=MODEL,
                settings=self.settings().to_wire(),
                work_dir=str(work),
            )
        )
        moment = now or datetime.now(UTC)
        return run_smoke(
            request,
            runtime=runtime or FakeRuntime(),
            now=lambda: moment,
            paths=self.paths,
            profile_root=self.profile_root,
            repo_root=self.repo,
            fingerprints=fixed,
        )


@pytest.fixture
def world(tmp_path, monkeypatch) -> tuple[World, Env]:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    env = Env(jobs, worker_settings)
    w = World(tmp_path, monkeypatch)
    env.provider = w.provider
    env.admission.provider = w.provider
    return w, env


def refused(env: Env, *paths: Path) -> set[str]:
    with pytest.raises(AdmissionError) as caught:
        env.resolution(env.session(*paths))
    assert caught.value.code == "execution_unavailable"
    return {issue.code for issue in caught.value.issues}


def accepted(env: Env, path: Path):
    body = env.submit_body(env.session(path))
    result = env.admission.submit(
        IdempotencyScope("/api/v1/jobs", env.key()), body, session=SESSION
    )
    (job,) = result.batch.jobs
    return env.store.get_snapshot(job.id)


def test_without_evidence_admission_is_refused_and_nothing_runs(world) -> None:
    w, env = world
    before = sorted(p.as_posix() for p in w.root.rglob("*"))
    codes = refused(env, env.file())
    assert codes and codes <= {"no_runnable_candidate", "candidate_not_ready"}
    assert sorted(p.as_posix() for p in w.root.rglob("*")) == before  # read-only
    assert not w.directory.exists()


def test_smoke_evidence_enables_admission_and_the_same_rule_at_prestart(world) -> None:
    w, env = world
    source = env.file()
    refused(env, source)
    assert w.smoke().outcome == "passed"
    snapshot = accepted(env, source)
    assert snapshot.resolved.recipe_id == "fw-cpu"
    assert env.admission.prestart_check(snapshot) is None
    # Admission and prestart both reached the profile-side resolver; no smoke ran there.
    assert len(w.requests) >= 3
    assert len(list(w.directory.glob("*.json"))) == 1


def _expire(w: World) -> None:
    (path,) = w.directory.glob("*.json")
    wire = json.loads(path.read_text(encoding="utf-8"))
    past = datetime.now(UTC) - EVIDENCE_TTL - timedelta(minutes=1)
    fields = {k: v for k, v in wire.items() if k not in {"format", "version", "checksum"}}
    fields.update(observed_at=past, expires_at=past + EVIDENCE_TTL)
    write_evidence(w.directory, seal_evidence(**fields))


@pytest.mark.parametrize(
    "change", ["expired", "model_changed", "profile_changed", "resmoke_failed"]
)
def test_prestart_stops_when_evidence_no_longer_applies(world, change) -> None:
    w, env = world
    source = env.file()
    refused(env, source)
    assert w.smoke().outcome == "passed"
    snapshot = accepted(env, source)
    assert env.admission.prestart_check(snapshot) is None
    if change == "expired":
        _expire(w)
    elif change == "model_changed":
        target = w.model_dir / "model.bin"
        target.write_text('{"changed": true}', encoding="utf-8")
        os.utime(target, ns=(1, 1))
    elif change == "profile_changed":
        manifest = w.profile_root / ".utteran-profile.json"
        manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    elif change == "resmoke_failed":
        from utteran.capabilities.smoke import SmokeFailure

        result = w.smoke(FakeRuntime(**{"raise": SmokeFailure("inference_failed")}))
        assert result.outcome == "inference_failed"
    # Never a different candidate, CPU substitute or legacy path: the job is blocked.
    assert env.admission.prestart_check(snapshot) in {"candidate_unavailable", "candidate_changed"}


def test_reading_evidence_never_extends_it(world) -> None:
    w, env = world
    source = env.file()
    refused(env, source)
    moment = datetime.now(UTC) - timedelta(hours=1)
    assert w.smoke(now=moment).outcome == "passed"
    (path,) = w.directory.glob("*.json")
    before = path.read_bytes()
    accepted(env, source)
    again = env.resolution(env.session(source))
    assert again.files
    assert path.read_bytes() == before
    scope = path.stem
    status, record = inspect_evidence(w.directory, scope, now=datetime.now(UTC))
    assert status == "adopted" and record is not None
    assert record.expires_at == moment + EVIDENCE_TTL


def test_bridge_refresh_reads_marked_root_evidence_without_side_effects(world, tmp_path) -> None:
    """Fresh process, real fingerprints: refresh/resolve with evidence present never writes,
    spawns, connects or imports inference code (audit hook), and leaves files intact."""
    w, env = world
    refused(env, env.file())
    assert w.smoke().outcome == "passed"
    request = dict(w.requests[-1]["refresh"])
    fixture = tmp_path / "request.json"
    fixture.write_text(json.dumps(request), encoding="utf-8")
    before = {p.as_posix(): p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    code = r"""
import sys, importlib.abc
blocked = {'torch', 'torchaudio', 'openvino', 'openvino_genai',
           'faster_whisper', 'ctranslate2', 'pyannote'}
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('heavy import')
sys.meta_path.insert(0, Guard())
def audit(event, args):
    if event.startswith(('subprocess.', 'socket.connect', 'os.system', 'os.fork')):
        raise AssertionError('execution side effect')
    if event in {'os.mkdir','os.remove','os.rename','os.rmdir','os.unlink','os.replace'}:
        raise AssertionError('filesystem mutation')
    if event == 'open' and isinstance(args[2], int) and (args[2] & (64 | 512 | 1024 | 1 | 2)):
        raise AssertionError('file write')
    if event == 'open' and isinstance(args[1], str) and any(c in args[1] for c in 'wax+'):
        raise AssertionError('file write')
sys.addaudithook(audit)
from datetime import UTC, datetime
from pathlib import Path
from utteran.capabilities.collector import refresh_inventory
from utteran_contracts.capabilities import InventoryRefreshRequest
request = InventoryRefreshRequest.model_validate_json(Path(sys.argv[1]).read_bytes())
refresh_inventory(request, now=datetime.now(UTC))
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code, str(fixture)],
        cwd=w.repo,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            "UTTERAN_DATA_ROOT": str(w.root),
        },
        capture_output=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    after = {p.as_posix(): p.read_bytes() for p in w.root.rglob("*") if p.is_file()}
    assert after == before
