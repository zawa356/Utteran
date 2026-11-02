"""B8 setup plan / approval / durable execution (isolated temp dirs, fake children only)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from test_b7_jobs import Clock
from utteran_contracts.control import ProcessIdentity
from utteran_contracts.setup import (
    SETUP_MODELS,
    ApprovalRequest,
    SetupPlanRequest,
    SetupStart,
)
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.job_repository import JobControlStore
from utteran_control.recovery import Liveness
from utteran_control.setup_repository import SetupControlStore, plan_hash
from utteran_control.store import ControlStore, IdempotencyScope
from utteran_gui.process_verifier import current_owner
from utteran_gui.setup_executor import (
    ChildResult,
    ChildUnconfirmed,
    ProcessChildRunner,
    ProductionStepAdapter,
    SetupExecutor,
)
from utteran_gui.setup_planner import PlanRejected, SetupObserver, build_plan

E = ErrorCode
SECRET = "hf_" + "S" * 34  # synthetic token-shaped sentinel; must never be persisted
FW = "faster-whisper:small"
PYA = "pyannote:pyannote/speaker-diarization-community-1"


# --------------------------------------------------------------------------- fakes


@dataclass
class Info:
    exists: bool
    compatible: bool | None
    compatibility_reason: str | None


class FakeCli:
    def __init__(self, root: Path) -> None:
        self.repo_root = root
        (root / "uv.lock").write_text("lock-v1")
        self.profiles: dict[str, Info] = {
            name: Info(False, None, None) for name in ("cpu", "cuda", "intel", "vulkan")
        }

    def profile_info(self, profile: str) -> Info:
        return self.profiles[profile]

    def environment(self, profile: str) -> dict[str, str]:
        return {"UTTERAN_PROFILE": profile}

    def command(self, profile: str, arguments: list[str]) -> list[str]:
        if not self.profiles[profile].exists:
            raise RuntimeError("profile missing")
        return [f"utteran-{profile}", *arguments]


class FakeProcess:
    counter = 2_000_000_000  # never a real PID (Linux pid_max <= 2**22)

    def __init__(self, alive: bool = False) -> None:
        FakeProcess.counter += 1
        self.pid = FakeProcess.counter
        self.alive = alive

    def poll(self) -> int | None:
        return None if self.alive else 0


class World:
    """Isolated machine model: fake CLI, models dir, credential flag, scripted children."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.repo.mkdir()
        self.cli = FakeCli(self.repo)
        self.models = tmp / "models"
        self.models.mkdir()
        self.credential = False
        self.calls: list[list[str]] = []
        self.fail: dict[str, int] = {}  # argv marker -> exit code
        self.token_access = "available"
        self.verify_ok_after_download = True
        self.hang_on: str | None = None
        self.raise_on: str | None = None
        self.during: dict[str, Any] = {}
        self.observer = SetupObserver(
            self.cli,
            models_root=self.models,
            credential_present=lambda: self.credential,
            platform_name="nt",
        )

    def model_dir(self, ref: str) -> Path:
        model = SETUP_MODELS[ref]
        return self.models / model.backend / model.managed_component

    def install(self, profile: str) -> None:
        self.cli.profiles[profile] = Info(True, True, None)

    # ChildRunner protocol
    def run(self, argv, *, cwd, env, on_spawn, capture_status) -> ChildResult:
        assert isinstance(argv, list) and all(isinstance(a, str) for a in argv)
        self.calls.append(list(argv))
        joined = " ".join(argv)
        process = FakeProcess(alive=self.hang_on is not None and self.hang_on in joined)
        on_spawn(process)
        for marker, action in self.during.items():
            if marker in joined:
                action()
        if self.raise_on and self.raise_on in joined:
            raise OSError("pipe broke")
        if process.alive:
            raise ChildUnconfirmed
        for marker, code in self.fail.items():
            if marker in joined:
                return ChildResult(code, None)
        status = None
        if argv[0] == "powershell.exe":
            self.install(argv[argv.index("-Profile") + 1])
        elif "verify" in argv:
            ref = argv[argv.index("verify") + 1]
            return ChildResult(0 if (self.model_dir(ref) / "ok").is_file() else 1, None)
        elif "download" in argv:
            ref = argv[argv.index("download") + 1]
            self.model_dir(ref).mkdir(parents=True, exist_ok=True)
            if self.verify_ok_after_download:
                (self.model_dir(ref) / "ok").write_text("x")
        elif "token-status" in argv:
            status = json.dumps({"access": self.token_access, "token": SECRET})
        elif "transcribe" in argv:
            out = Path(argv[argv.index("--output-dir") + 1])
            (out / "smoke.txt").write_text("")
        return ChildResult(0, status if capture_status else None)


class Adapter(ProductionStepAdapter):
    def __init__(self, world: World) -> None:
        super().__init__(world.cli)


def make_store(path: Path, clock: Clock | None = None, **kw: Any) -> SetupControlStore:
    return SetupControlStore(path, clock=clock, **kw)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path, clock: Clock) -> SetupControlStore:
    return make_store(tmp_path / "control" / "operations.sqlite3", clock)


def executor(store: SetupControlStore, world: World, **kw: Any) -> SetupExecutor:
    return SetupExecutor(
        store,
        adapter=Adapter(world),
        observer=world.observer,
        runner=world,
        identity_of=lambda pid: f"fake:{pid}",
        work_dir=world.tmp,
        lease_seconds=kw.pop("lease_seconds", 30),
        **kw,
    )


def request(profile: str = "cuda", *refs: str, diarization: bool | None = None) -> SetupPlanRequest:
    refs = refs or (FW,)
    return SetupPlanRequest(
        schema_version=1,
        profile_kind=profile,  # type: ignore[arg-type]
        model_refs=refs,
        diarization_enabled=PYA in refs if diarization is None else diarization,
    )


_keys = iter(range(10**6))


def key(prefix: str = "k") -> str:
    return f"{prefix}-key-{next(_keys):012d}"


def plan(store: SetupControlStore, world: World, req: SetupPlanRequest | None = None):
    content = build_plan(req or request(), world.observer.observe())
    scope = IdempotencyScope("/api/v1/setup-plans", key("plan"))
    return store.create_plan(scope, store.setup_digest(content.to_wire()), content)


def approve(store, world, accepted, agreements=()):
    body = accepted.body
    obs = world.observer.observe()
    return store.approve_plan(
        body["id"],
        body["revision"],
        IdempotencyScope(f"/api/v1/setup-plans/{body['id']}/approve", key("apr")),
        ApprovalRequest(
            schema_version=1, plan_hash=body["plan_hash"], accepted_agreement_ids=agreements
        ),
        fingerprint=obs.fingerprint,
        credentials=obs.credentials,
    )


def start(store, world, approval, *, k: str | None = None):
    body = {
        "schema_version": 1,
        "plan_ref": {"id": approval.body["plan_id"], "revision": approval.body["plan_revision"]},
        "approval_id": approval.body["id"],
    }
    scope = IdempotencyScope("/api/v1/setup-operations", k or key("start"))
    return store.start_operation(
        scope,
        store.setup_digest(body),
        SetupStart.model_validate(body),
        fingerprint=world.observer.observe().fingerprint,
    )


def queued_setup(store, world, req=None, agreements=()):
    accepted = plan(store, world, req)
    approval = approve(store, world, accepted, agreements)
    return start(store, world, approval)


def counts(store: ControlStore) -> dict[str, int]:
    with sqlite3.connect(store.path) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def run_once(store, world) -> SetupExecutor:
    ex = executor(store, world)
    candidate = store.next_dispatchable()
    assert candidate is not None
    ex.dispatch(*candidate, current_owner())
    return ex


# --------------------------------------------------------------------------- 1. plan


def test_catalog_subset_matches_core_catalog() -> None:
    from utteran.models.catalog import get_model
    from utteran.models.manager import ModelManager

    for ref, model in SETUP_MODELS.items():
        entry = get_model(ref)
        assert (entry.backend, entry.model_id, entry.format, entry.repository_id) == (
            model.backend,
            model.model_id,
            model.format,
            model.repository_id,
        )
        assert entry.approximate_size_bytes == model.approximate_size_bytes
        assert entry.gated == model.gated
        managed = ModelManager(Path("/x")).managed_path(entry)
        assert managed.name == model.managed_component


def test_new_cuda_plan_is_side_effect_free_and_hash_stable(store, world, tmp_path) -> None:
    def tree() -> list[Path]:
        return sorted(
            p.relative_to(tmp_path) for p in tmp_path.rglob("*") if "control" not in p.parts
        )

    before = tree()
    first = build_plan(request(), world.observer.observe())
    second = build_plan(request(), world.observer.observe())
    assert plan_hash(first) == plan_hash(second)
    assert [c.action for c in first.changes] == ["create", "create"]
    assert [s.kind for s in first.steps] == ["venv_sync", "model_download", "smoke"]
    assert first.steps[1].depends_on == ("venv",) and first.steps[2].depends_on == (
        "venv",
        "model-1",
    )
    # Venv dependency download size is unknown -> null, never invented.
    assert first.estimate.download_bytes is None and first.estimate.duration_seconds is None
    accepted = plan(store, world)
    assert accepted.status == 201 and accepted.body["state"] == "proposed"
    assert accepted.headers["Location"] == f"/api/v1/setup-plans/{accepted.body['id']}"
    assert world.calls == []  # observation and planning never start a process
    assert tree() == before


def test_cuda_then_intel_preserves_existing_assets(store, world) -> None:
    world.install("cuda")
    world.model_dir(FW).mkdir(parents=True)
    (world.model_dir(FW) / "ok").write_text("x")
    content = build_plan(request("intel"), world.observer.observe())
    assert content.preserved_environment_ids == ("cuda",)
    assert {(c.resource_kind, c.action) for c in content.changes} == {
        ("profile", "create"),
        ("model", "verify"),
    }
    accepted = queued_setup(store, world, request("intel"))
    run_once(store, world)
    view = store.get_setup(accepted.body["id"])
    assert view.status == "succeeded", view.to_wire()
    # CUDA never touched: no command targets the cuda profile.
    assert not any(call[0] == "utteran-cuda" or "cuda" in call for call in world.calls)
    assert not any("download" in call for call in world.calls)  # shared model reused
    assert not any("remove" in call for call in world.calls)
    steps = {s.step_id: (s.status, s.reason_code) for s in view.steps}
    assert steps["model-1"] == ("skipped", "validated_reuse")


def test_model_only_estimate_uses_catalog_sizes(world) -> None:
    world.install("cpu")
    content = build_plan(request("cpu"), world.observer.observe())
    size = SETUP_MODELS[FW].approximate_size_bytes
    assert content.estimate.download_bytes == size and content.estimate.confidence == "low"
    assert [s.kind for s in content.steps] == ["model_download", "smoke"]
    assert content.preserved_environment_ids == ("cpu",)


@pytest.mark.parametrize(
    "profile, refs, code, status",
    [
        ("cuda", ("openvino-genai:large-v3-turbo-int8",), "unsupported_combination", 409),
        ("cpu", ("whisper-cpp:base",), "unsupported_combination", 409),
        ("intel", ("whisper-cpp:base",), "native_build_unsupported", 409),
        ("intel", ("openvino-genai:large-v3-turbo-int8",), "setup_backend_unsupported", 409),
        ("cuda", ("faster-whisper:nonexistent",), "model_unknown", 422),
        ("cuda", ("bogus:model",), "model_unknown", 422),
        ("cuda", (FW, FW), "model_refs_duplicate", 422),
        ("cuda", (PYA,), "asr_model_required", 422),
    ],
)
def test_unsupported_plans_are_rejected(world, profile, refs, code, status) -> None:
    with pytest.raises(PlanRejected) as excinfo:
        build_plan(request(profile, *refs), world.observer.observe())
    assert (excinfo.value.code, excinfo.value.status) == (code, status)


def test_plan_platform_rebuild_and_diarization_rules(world, tmp_path) -> None:
    with pytest.raises(PlanRejected) as excinfo:
        build_plan(request("cuda", FW, diarization=True), world.observer.observe())
    assert excinfo.value.code == "diarization_model_mismatch"
    world.cli.profiles["cuda"] = Info(True, False, "dependency_lock_changed")
    with pytest.raises(PlanRejected) as excinfo:
        build_plan(request(), world.observer.observe())
    assert excinfo.value.code == "profile_rebuild_unsupported"  # never rebuilt in place
    posix = SetupObserver(
        world.cli, models_root=world.models, credential_present=lambda: True, platform_name="posix"
    )
    with pytest.raises(PlanRejected) as excinfo:
        build_plan(request("cpu"), posix.observe())
    assert excinfo.value.code == "venv_build_platform_unsupported"
    gated = build_plan(request("cpu", FW, PYA), world.observer.observe())
    assert gated.required_agreements == ("hf-gated:pyannote/speaker-diarization-community-1",)
    assert gated.missing_credentials == ("hf_token",)
    assert [s.id for s in gated.steps] == ["venv", "preflight", "model-1", "model-2", "smoke"]
    assert gated.steps[3].depends_on == ("venv", "preflight")


# --------------------------------------------------------------------------- 2. approve


def test_approval_checks(store, world, clock) -> None:
    accepted = plan(store, world)
    body = accepted.body
    obs = world.observer.observe()

    def attempt(**kw: Any):
        params = dict(
            expected=body["revision"],
            hash_=body["plan_hash"],
            agreements=(),
            fingerprint=obs.fingerprint,
            credentials=obs.credentials,
        )
        params.update(kw)
        return store.approve_plan(
            body["id"],
            params["expected"],
            IdempotencyScope(f"/api/v1/setup-plans/{body['id']}/approve", key("apr")),
            ApprovalRequest(
                schema_version=1,
                plan_hash=params["hash_"],
                accepted_agreement_ids=params["agreements"],
            ),
            fingerprint=params["fingerprint"],
            credentials=params["credentials"],
        )

    for kw, code in (
        ({"expected": 9}, E.REVISION_CONFLICT),
        ({"hash_": "0" * 64}, E.PLAN_HASH_MISMATCH),
        ({"fingerprint": "1" * 64}, E.INVENTORY_STALE),
        ({"agreements": ("hf-gated:other/model",)}, E.AGREEMENT_UNKNOWN),
    ):
        with pytest.raises(ControlError) as excinfo:
            attempt(**kw)
        assert excinfo.value.code is code
    assert store.get_plan(body["id"]).state == "proposed"  # failures changed nothing
    approved = attempt()
    assert approved.status == 201 and store.get_plan(body["id"]).state == "approved"
    with pytest.raises(ControlError) as excinfo:
        attempt(expected=approved.body["plan_revision"])
    assert excinfo.value.code is E.PLAN_ALREADY_APPROVED
    clock.advance(24 * 3600 + 1)
    assert store.get_plan(body["id"]).state == "expired"
    with pytest.raises(ControlError) as excinfo:
        start(store, world, approved)
    assert excinfo.value.code is E.PLAN_EXPIRED


def test_gated_plan_requires_real_agreement_and_credential(store, world) -> None:
    world.install("cpu")
    accepted = plan(store, world, request("cpu", FW, PYA))
    with pytest.raises(ControlError) as excinfo:
        approve(store, world, accepted)
    assert excinfo.value.code is E.AGREEMENT_REQUIRED  # never assumed for the user
    agreements = tuple(accepted.body["required_agreements"])
    with pytest.raises(ControlError) as excinfo:
        approve(store, world, accepted, agreements)
    assert excinfo.value.code is E.CREDENTIAL_REQUIRED
    world.credential = True
    approved = approve(store, world, accepted, agreements)
    assert approved.body["accepted_agreement_ids"] == list(agreements)
    assert SECRET not in json.dumps(counts(store))


def test_idempotency_key_reuse_and_kind_isolation(store, world) -> None:
    content = build_plan(request(), world.observer.observe())
    scope = IdempotencyScope("/api/v1/setup-plans", key("plan"))
    first = store.create_plan(scope, store.setup_digest(content.to_wire()), content)
    again = store.create_plan(scope, store.setup_digest(content.to_wire()), content)
    assert (
        again.replayed and again.body == first.body and dict(again.headers) == dict(first.headers)
    )
    with pytest.raises(ControlError) as excinfo:
        store.create_plan(scope, store.setup_digest({"other": 1}), content)
    assert excinfo.value.code is E.IDEMPOTENCY_CONFLICT
    with pytest.raises(ControlError) as excinfo:  # same scope, other response kind
        store.lookup_setup(scope, store.setup_digest(content.to_wire()), "operation")
    assert excinfo.value.code is E.IDEMPOTENCY_CONFLICT


# --------------------------------------------------------------------------- 3. accept


def test_start_consumes_plan_and_replays_exactly(store, world) -> None:
    accepted = plan(store, world)
    approval = approve(store, world, accepted)
    k = key("start")
    first = start(store, world, approval, k=k)
    assert first.status == 202
    assert first.headers["Location"] == f"/api/v1/setup-operations/{first.body['id']}"
    again = start(store, world, approval, k=k)  # lost response -> same operation
    assert (
        again.replayed and again.body == first.body and dict(again.headers) == dict(first.headers)
    )
    assert store.get_plan(accepted.body["id"]).state == "consumed"
    for code in (E.PLAN_CONSUMED,):
        with pytest.raises(ControlError) as excinfo:
            start(store, world, approval)  # other key, approval already used
        assert excinfo.value.code is code
    assert counts(store)["setup_operations"] == 1
    queue = store.queue()
    assert [i.resource_kind for i in queue.items] == ["setup_operation"]


def test_start_failure_before_commit_changes_nothing(tmp_path, world, clock) -> None:
    faults: list[str] = []

    def fault(where: str) -> None:
        if where in faults:
            raise ControlError(E.STORE_FULL)

    store = make_store(tmp_path / "c" / "operations.sqlite3", clock, _fault=fault)
    accepted = plan(store, world)
    approval = approve(store, world, accepted)
    before = counts(store)
    for where in ("setup_before_replay", "before_commit"):
        faults[:] = [where]
        with pytest.raises(ControlError):
            start(store, world, approval)
        assert counts(store) == before
        assert store.get_plan(accepted.body["id"]).state == "approved"
    faults.clear()
    assert start(store, world, approval).status == 202


def test_start_readonly_store_is_refused_without_change(tmp_path, world, clock) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    store = make_store(path, clock)
    approval = approve(store, world, plan(store, world))
    before = counts(store)
    readonly = make_store(path, clock, _connect_hook=lambda db: db.execute("PRAGMA query_only=1"))
    with pytest.raises(ControlError) as excinfo:
        start(readonly, world, approval)
    assert excinfo.value.code in {E.STORE_READONLY, E.STORE_UNAVAILABLE}
    assert counts(store) == before


def test_same_key_parallel_and_cross_process(tmp_path, world, clock) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    store = make_store(path, clock)
    approval = approve(store, world, plan(store, world))
    k = key("par")
    results: list[Any] = []
    errors: list[BaseException] = []

    def send() -> None:
        try:
            results.append(start(make_store(path, clock, timeout=10), world, approval, k=k))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=send) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len({r.body["id"] for r in results}) == 1
    assert sum(not r.replayed for r in results) == 1
    body = {
        "schema_version": 1,
        "plan_ref": {"id": approval.body["plan_id"], "revision": approval.body["plan_revision"]},
        "approval_id": approval.body["id"],
    }
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "from utteran_control.setup_repository import SetupControlStore\n"
        "from utteran_control.store import IdempotencyScope\n"
        "s=SetupControlStore(Path(sys.argv[1]))\n"
        "b=json.loads(sys.argv[2])\n"
        "r=s.lookup_setup(IdempotencyScope('/api/v1/setup-operations',sys.argv[3]),"
        "s.setup_digest(b),'operation')\nprint(r.body['id'] if r else 'none')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script, str(path), json.dumps(body), k],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == results[0].body["id"]
    assert counts(store)["setup_operations"] == 1


# --------------------------------------------------------------------------- 4. schema


def test_v1_v3_migrate_to_v4_and_older_code_refuses_v4(tmp_path) -> None:
    for name, older in (("b6", ControlStore), ("b7", JobControlStore)):
        path = tmp_path / name / "operations.sqlite3"
        old = older(path)
        old.submit(
            "/api/v1/jobs",
            "migr-key-000000001",
            {"schema_version": 1, "kind": "job", "private_payload": {}},
        )
        before = old.queue()
        migrated = SetupControlStore(path)
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        assert migrated.queue() == before
        for refusing in (ControlStore, JobControlStore):
            with pytest.raises(ControlError) as excinfo:
                refusing(path)
            assert excinfo.value.code is E.STORE_SCHEMA_UNSUPPORTED


def test_migration_failure_keeps_old_layout_and_unknown_versions_fail_closed(tmp_path) -> None:
    path = tmp_path / "b7" / "operations.sqlite3"
    JobControlStore(path)

    def fault(where: str) -> None:
        if where == "before_commit":
            raise ControlError(E.STORE_FULL)

    with pytest.raises(ControlError):
        SetupControlStore(path, _fault=fault)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert "setup_plans" not in {
            r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    JobControlStore(path)  # still a valid v3 store
    SetupControlStore(path)
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version=5")
    raw.commit()
    raw.close()
    with pytest.raises(ControlError) as excinfo:
        SetupControlStore(path)
    assert excinfo.value.code is E.STORE_SCHEMA_UNSUPPORTED
    # Old header but v4 tables present: refused, never re-migrated.
    corrupt = tmp_path / "x" / "operations.sqlite3"
    JobControlStore(corrupt)
    raw = sqlite3.connect(corrupt)
    raw.execute("CREATE TABLE setup_plans (id TEXT)")
    raw.commit()
    raw.close()
    with pytest.raises(ControlError) as excinfo:
        SetupControlStore(corrupt)
    assert excinfo.value.code is E.STORE_CORRUPT


def test_plan_body_and_journal_are_immutable(store, world) -> None:
    accepted = queued_setup(store, world)
    run_once(store, world)
    with sqlite3.connect(store.path) as db:
        for sql in (
            "UPDATE setup_plans SET body = '{}'",
            "DELETE FROM setup_plans",
            "UPDATE setup_step_journal SET exit_code = 0",
            "DELETE FROM setup_replays",
            "UPDATE setup_operations SET plan_hash = 'x'",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                db.execute(sql)
    assert store.get_setup(accepted.body["id"]).status == "succeeded"


# --------------------------------------------------------------------------- 5. stepper


def test_full_run_journal_orders_intent_before_spawn(store, world) -> None:
    accepted = queued_setup(store, world)
    observed: list[str] = []
    original = world.run

    def watch(argv, **kw):
        journal = store.step_journal(accepted.body["id"])
        observed.append(journal[-1][1])  # last committed event before spawn
        return original(argv, **kw)

    world.run = watch  # type: ignore[method-assign]
    run_once(store, world)
    view = store.get_setup(accepted.body["id"])
    assert view.status == "succeeded" and view.attempt_number == 1
    assert set(observed) == {"intent"}
    events = [(s, e) for s, e, *_ in store.step_journal(accepted.body["id"])]
    assert events[:4] == [
        ("venv", "intent"),
        ("venv", "launched"),
        ("venv", "exited"),
        ("venv", "result"),
    ]
    assert all(s.evidence is not None for s in view.steps)
    assert [c[0] for c in world.calls] == ["powershell.exe"] + ["utteran-cuda"] * 4
    assert store.active_job() is None  # slot released


def test_exit_zero_without_verification_is_not_success(store, world) -> None:
    world.verify_ok_after_download = False  # download exits 0 but leaves an incomplete copy
    accepted = queued_setup(store, world)
    run_once(store, world)
    view = store.get_setup(accepted.body["id"])
    assert view.status == "failed"
    assert view.error is not None and view.error.code == "verification_failed"
    assert view.error.step_id == "model-1"
    steps = {s.step_id: s.status for s in view.steps}
    assert steps == {"venv": "succeeded", "model-1": "failed", "smoke": "pending"}
    with pytest.raises(ControlError):
        op = store.get(accepted.body["id"])
        store.finish_setup(op.id, 1, "succeeded")


def test_failure_retry_creates_new_operation_and_reuses_verified_steps(store, world) -> None:
    world.fail["download"] = 1
    accepted = queued_setup(store, world)
    run_once(store, world)
    failed = store.get_setup(accepted.body["id"])
    assert failed.status == "failed" and failed.error.code == "process_failed"
    del world.fail["download"]
    obs = world.observer.observe()  # venv now exists: fingerprint differs from the plan
    retry = store.retry_setup(
        failed.id,
        failed.revision,
        IdempotencyScope(f"/api/v1/setup-operations/{failed.id}/retry", key("retry")),
        fingerprint=obs.fingerprint,
        credentials=obs.credentials,
    )
    assert retry.status == 202 and retry.body["retry_of"] == failed.id
    world.calls.clear()
    run_once(store, world)
    done = store.get_setup(retry.body["id"])
    assert done.status == "succeeded"
    assert not any(c[0] == "powershell.exe" for c in world.calls)  # venv verified, not rerun
    assert store.get_setup(failed.id).status == "failed"  # source untouched


def test_inventory_change_requires_reapproval(store, world) -> None:
    accepted = queued_setup(store, world)
    world.install("vulkan")  # environment changed after approval
    run_once(store, world)
    view = store.get_setup(accepted.body["id"])
    assert view.status == "waiting_user" and view.waiting_reason == "inventory_changed"
    assert world.calls == []
    obs = world.observer.observe()
    with pytest.raises(ControlError) as excinfo:
        store.resume_setup(
            view.id,
            view.revision,
            IdempotencyScope(f"/api/v1/setup-operations/{view.id}/resume", key("res")),
            fingerprint=obs.fingerprint,
            credentials=obs.credentials,
        )
    assert excinfo.value.code is E.REAPPROVAL_REQUIRED
    cancelled = store.cancel_setup(
        view.id,
        view.revision,
        IdempotencyScope(f"/api/v1/setup-operations/{view.id}/cancel", key("can")),
    )
    assert cancelled.body["status"] == "cancelled"


def test_credential_and_agreement_wait_then_resume(store, world) -> None:
    world.install("cpu")
    world.credential = True
    req = request("cpu", FW, PYA)
    accepted = plan(store, world, req)
    approval = approve(store, world, accepted, tuple(accepted.body["required_agreements"]))
    op = start(store, world, approval)
    world.token_access = "agreement_required"
    run_once(store, world)
    view = store.get_setup(op.body["id"])
    assert view.status == "waiting_user" and view.waiting_reason == "agreement_required"
    assert store.active_job() is None and SECRET not in json.dumps(counts(store))
    with sqlite3.connect(store.path) as db:
        dump = "\n".join(db.iterdump())
    assert SECRET not in dump
    world.token_access = "available"
    obs = world.observer.observe()
    resumed = store.resume_setup(
        view.id,
        view.revision,
        IdempotencyScope(f"/api/v1/setup-operations/{view.id}/resume", key("res")),
        fingerprint=obs.fingerprint,
        credentials=obs.credentials,
    )
    assert resumed.body["status"] == "queued"
    run_once(store, world)
    final = store.get_setup(view.id)
    assert final.status == "succeeded" and final.attempt_number == 2


def test_missing_credential_at_runtime_waits(store, world) -> None:
    world.install("cpu")
    world.credential = True
    accepted = plan(store, world, request("cpu", FW, PYA))
    approval = approve(store, world, accepted, tuple(accepted.body["required_agreements"]))
    op = start(store, world, approval)
    world.credential = False  # token removed after approval
    run_once(store, world)
    view = store.get_setup(op.body["id"])
    assert view.status == "waiting_user" and view.waiting_reason == "credential_required"
    assert not any("token-status" in c for c in world.calls)
    obs = world.observer.observe()
    with pytest.raises(ControlError) as excinfo:
        store.resume_setup(
            view.id,
            view.revision,
            IdempotencyScope(f"/api/v1/setup-operations/{view.id}/resume", key("res")),
            fingerprint=obs.fingerprint,
            credentials=obs.credentials,
        )
    assert excinfo.value.code is E.CREDENTIAL_REQUIRED


def test_cancel_queued_and_running_at_boundary(store, world) -> None:
    accepted = queued_setup(store, world)
    view = store.get_setup(accepted.body["id"])
    cancelled = store.cancel_setup(
        view.id, view.revision, IdempotencyScope("/api/v1/setup-operations/x/cancel", key("c"))
    )
    assert cancelled.status == 202 and cancelled.body["status"] == "cancelled"
    assert store.queue().items == ()
    second = queued_setup(store, world, request("cpu"))
    sid = second.body["id"]

    def cancel_now() -> None:
        current = store.get_setup(sid)
        result = store.cancel_setup(
            sid, current.revision, IdempotencyScope("/api/v1/setup-operations/y/cancel", key("c"))
        )
        assert result.status == 202 and result.body["status"] == "cancelling"
        assert store.active_job() is not None  # lease kept until the child is reconciled

    world.during["powershell.exe"] = cancel_now
    run_once(store, world)
    final = store.get_setup(sid)
    assert final.status == "cancelled" and final.reason_code == "exit_reconciled"
    assert [c[0] for c in world.calls] == ["powershell.exe"]  # no later step started
    assert store.active_job() is None


def test_unconfirmed_child_keeps_slot_and_recovery_is_conservative(tmp_path, world, clock) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    store = make_store(path, clock)
    accepted = queued_setup(store, world)
    world.hang_on = "powershell.exe"
    run_once(store, world)
    view = store.get_setup(accepted.body["id"])
    assert view.status == "running" and view.recovery_required == "termination_failed"
    assert store.active_job() is not None and store.next_dispatchable() is None

    class Verifier:
        def __init__(self, child: Liveness) -> None:
            self.child = child

        def check(self, process: ProcessIdentity) -> Liveness:
            # Simulated restart: the previous owner (this test process) is gone.
            return self.child if process.pid >= 2_000_000_000 else Liveness.GONE

    restarted = make_store(path, clock)
    report = restarted.recover(Verifier(Liveness.ALIVE))
    assert accepted.body["id"] in report.recovery_required
    assert restarted.get_setup(accepted.body["id"]).status == "running"
    report = restarted.recover(Verifier(Liveness.GONE))
    assert accepted.body["id"] in report.interrupted
    interrupted = restarted.get_setup(accepted.body["id"])
    assert interrupted.status == "interrupted"
    assert {s.step_id: s.status for s in interrupted.steps}["venv"] == "interrupted"
    assert restarted.active_job() is None


def test_crash_between_intent_and_spawn_is_recovery_required(store, world) -> None:
    accepted = queued_setup(store, world)
    sid = accepted.body["id"]
    candidate = store.next_dispatchable()
    grant = store.begin_attempt(candidate[0], candidate[1], current_owner())
    store.step_started(sid, grant.fencing_token, "venv")
    store.child_intent(sid, grant.fencing_token, "venv")
    # "crash": owner gone, no child identity recorded yet.

    class Gone:
        def check(self, process: ProcessIdentity) -> Liveness:
            return Liveness.GONE

    report = store.recover(Gone())
    assert sid in report.recovery_required and sid not in report.interrupted
    assert store.get_setup(sid).recovery_required == "recovery_launch_unconfirmed"


def test_spawn_failure_and_crash_after_spawn(store, world) -> None:
    world.raise_on = "powershell.exe"
    accepted = queued_setup(store, world)
    run_once(store, world)
    view = store.get_setup(accepted.body["id"])
    assert view.status == "failed" and view.error.code == "process_failed"
    assert store.active_job() is None


# --------------------------------------------------------------------------- 6. exclusion


def test_setup_and_job_share_one_heavy_slot(tmp_path, world, clock) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    store = make_store(path, clock)
    job = store.submit(
        "/api/v1/jobs",
        "plain-job-key-0001",
        {"schema_version": 1, "kind": "job", "private_payload": {}},
    )
    setup = queued_setup(store, world)
    # A plain B6 job (no B7 snapshot) is first: nothing may skip past it.
    assert store.next_dispatchable() is None
    with pytest.raises(ControlError) as excinfo:
        store.begin_attempt(setup.body["id"], 1, current_owner())
    assert excinfo.value.code is E.NOT_DISPATCHABLE
    store.transition(
        job.operation.id,
        {
            "schema_version": 1,
            "expected_revision": job.operation.revision,
            "to_status": "cancelled",
            "reason_code": "user_cancel",
        },
    )
    candidate = store.next_dispatchable()
    assert candidate is not None and candidate[0] == setup.body["id"]
    grant = store.begin_attempt(candidate[0], candidate[1], current_owner())
    other = make_store(path, clock)  # second connection / runtime
    assert other.next_dispatchable() is None
    second = queued_setup(other, world, request("cpu"))
    with pytest.raises(ControlError) as excinfo:
        other.begin_attempt(second.body["id"], 1, current_owner())
    assert excinfo.value.code in {E.SLOT_OCCUPIED, E.NOT_DISPATCHABLE}
    assert store.active_job() == (setup.body["id"], grant.fencing_token)


def test_paused_queue_and_hold_stop_setup_dispatch(store, world) -> None:
    accepted = queued_setup(store, world)
    store.set_dispatch(store.queue().revision, "paused")
    assert store.next_dispatchable() is None
    store.set_dispatch(store.queue().revision, "running")
    item = store.queue().items[0]
    store.set_item_hold(
        item.id, store.queue().revision, True, IdempotencyScope("/api/v1/queue/items/x", key("h"))
    )
    assert store.next_dispatchable() is None
    assert store.get_setup(accepted.body["id"]).status == "queued"  # setup keeps queued
    assert store.get_setup(accepted.body["id"]).held


# --------------------------------------------------------------------------- 7. adapter


def test_production_adapter_fixed_commands(world) -> None:
    adapter = ProductionStepAdapter(world.cli)
    argv = adapter.venv_command("intel")
    assert argv == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(world.repo / "setup.ps1"),
        "-Profile",
        "intel",
        "-Yes",
    ]
    assert not adapter.profile_verified("intel")
    world.install("intel")
    assert adapter.profile_verified("intel")


def test_process_runner_uses_shell_false_and_keeps_only_status_line(tmp_path) -> None:
    seen: dict[str, Any] = {}

    def factory(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.Popen(argv, **kwargs)

    runner = ProcessChildRunner(factory)
    spawned: list[Any] = []
    code = (
        "import json,sys;print('progress " + SECRET + "');"
        "print(json.dumps({'access':'available'}));sys.stderr.write('err\\n')"
    )
    result = runner.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={"PATH": "", "SYSTEMROOT": ""} if sys.platform == "win32" else {},
        on_spawn=spawned.append,
        capture_status=True,
    )
    assert seen["shell"] is False and len(spawned) == 1
    assert result.exit_code == 0 and json.loads(result.status_line or "") == {"access": "available"}
    assert SECRET not in (result.status_line or "")


def test_default_app_has_no_setup_routes_or_imports(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from utteran_gui.api import SESSION_HEADER, create_app

    root = Path(__file__).parents[1]
    app = create_app(
        "s",
        repo_root=root,
        enable_v1_settings=True,
        v1_authority="127.0.0.1:8765",
        v1_store_path=tmp_path / "s" / "resources.sqlite3",
    )
    with TestClient(app, base_url="http://127.0.0.1:8765", headers={SESSION_HEADER: "s"}) as c:
        assert c.get("/api/v1/setup-operations").status_code == 404
        assert c.get("/api/v1/setup-plans/plan_" + "0" * 32).status_code == 404
    with pytest.raises(ValueError):
        create_app("s", repo_root=root, enable_v1_setup=True)
    script = (
        "import sys\nfrom pathlib import Path\nfrom utteran_gui.api import create_app\n"
        f"create_app('s', repo_root=Path({str(root)!r}))\n"
        "bad=[m for m in ('utteran_control','utteran_control.setup_repository',"
        "'utteran_gui.setup_api','utteran_gui.setup_executor','utteran_gui.setup_planner',"
        "'utteran_contracts.setup','utteran','torch') if m in sys.modules]\nprint(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
