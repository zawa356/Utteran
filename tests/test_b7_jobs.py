"""B7 immutable job submission, durable queue controls, safe dispatch and recovery.

Every readiness fact in this file is synthetic test evidence (not a real inference run).
The worker runner is a fake that spawns a real local child process for launch journaling.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from utteran.capabilities.evaluator import digest, evidence_fingerprint, make_inventory
from utteran.capabilities.recipes import get_recipe
from utteran.capabilities.resolver import resolve
from utteran_contracts.capabilities import (
    CapabilityObservation,
    DeploymentInventory,
    ExecutionIdentity,
)
from utteran_contracts.control import OwnerIdentity, ProcessIdentity
from utteran_contracts.jobs import JobSnapshot
from utteran_contracts.settings import Policy, PolicyWrite, PresetWrite, ProcessingSettings
from utteran_contracts.worker import (
    WorkerArtifact,
    WorkerCompleted,
    WorkerErrorCode,
    WorkerRequest,
)
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.job_repository import JobControlStore
from utteran_control.recovery import Liveness
from utteran_control.store import ControlStore, IdempotencyScope
from utteran_gui.job_admission import (
    AdmissionError,
    JobAdmission,
    ProviderResult,
    ResolutionTokens,
    capture_input,
)
from utteran_gui.job_dispatcher import JobDispatcher, plan_request
from utteran_gui.process_verifier import OsProcessVerifier, current_owner, start_identity
from utteran_gui.worker_client import WorkerClientError
from utteran_settings.repository import Repository

ROOT = Path(__file__).parents[1]
NOW = datetime(2026, 11, 2, tzinfo=UTC)
LATER = NOW + timedelta(minutes=10)
FP = digest("synthetic B7 identity only")
SENTINEL = "PRIVATE_B7_PROMPT_秘密"
SESSION = "synthetic-session"
E = ErrorCode
LINUX = sys.platform.startswith("linux")


# --------------------------------------------------------------------------- fixtures


def examples() -> dict[str, Any]:
    return json.loads((ROOT / "docs/architecture/phase-a/examples.json").read_text("utf-8"))


def deployment(settings: ProcessingSettings, recipe_id: str = "fw-cpu", **updates: Any):
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
            platform="Linux",
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
    fp = evidence_fingerprint(identity, settings)
    actual = recipe.requested_route.to_wire()
    for component in ("encoder", "decoder"):
        if actual[component] == "selected-device":
            actual[component] = identity.device
    measured = {k: v if v != "backend-default" else "unknown" for k, v in actual.items()}
    observations = []
    for kind in ("hardware", "runtime", "venv", "model", "native", "smoke", "memory"):
        data: dict[str, Any] = dict(
            kind=kind,
            state="passed",
            source="provider",
            observed_at=NOW,
            expires_at=LATER,
            fingerprint=fp,
            reason_code="supported",
        )
        if kind == "smoke":
            data.update(
                initialized_route=actual,
                encoder_observed=measured["encoder"],
                decoder_observed=measured["decoder"],
            )
        observations.append(CapabilityObservation.model_validate(data))
    return DeploymentInventory(
        identity=identity,
        observations=tuple(observations),
        required_memory_bytes=100,
        available_memory_bytes=200,
    )


class FakeProvider:
    """Synthetic B4 evidence through the real pure evaluator/resolver."""

    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self.recipe = "fw-cpu"
        self.updates: dict[str, Any] = {}
        self.fail = False
        self.calls = 0

    def resolve(self, settings: ProcessingSettings, policy: Policy, *, revision: int):
        self.calls += 1
        if self.fail:
            raise RuntimeError("bridge down")
        inventory = make_inventory(
            (deployment(settings, self.recipe, **self.updates),),
            settings,
            "worker",
            revision=revision,
            now=NOW,
        )
        selection = resolve(inventory, policy, now=NOW)
        models = {a.candidate.id: str(self.model_dir) for a in selection.alternatives if a.eligible}
        return ProviderResult("supported", selection, models)


class Clock:
    def __init__(self) -> None:
        self.value = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class Env:
    def __init__(self, tmp: Path, settings_updates: Callable[[dict[str, Any]], None] | None):
        self.tmp = tmp
        self.inputs = tmp / "in"
        self.inputs.mkdir()
        self.output = tmp / "out"
        self.output.mkdir()
        self.model = tmp / "model"
        self.model.mkdir()
        self.settings_path = tmp / "settings" / "resources.sqlite3"
        repo = Repository(self.settings_path)
        data = examples()
        settings = data["preset"]["settings"]
        settings["asr"]["initial_prompt"] = SENTINEL
        if settings_updates:
            settings_updates(settings)
        preset = PresetWrite.model_validate({**data["preset"], "settings": settings})
        _, _, created = repo.post(
            "/api/v1/presets", "preset-key-000001", preset.to_wire(), payload=preset
        )
        policy = PolicyWrite.model_validate(
            data["policy"] | {"mode": "auto", "fixed_candidate_id": None, "allowed_profile_ids": []}
        )
        _, _, pol = repo.post(
            "/api/v1/execution-policies", "policy-key-000001", policy.to_wire(), payload=policy
        )
        self.preset_ref = {"id": created["id"], "revision": 1}
        self.policy_ref = {"id": pol["id"], "revision": 1}
        self.clock = Clock()
        self.store = JobControlStore(tmp / "control" / "operations.sqlite3")
        self.provider = FakeProvider(self.model)
        self.admission = JobAdmission(
            store=self.store,
            settings_path=self.settings_path,
            provider=self.provider,
            clock=self.clock,
        )
        self.counter = 0

    def file(self, name: str = "a.wav", content: bytes = b"RIFF synthetic audio") -> Path:
        path = self.inputs / name
        path.write_bytes(content)
        return path

    def session(self, *paths: Path) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "preset_ref": self.preset_ref,
            "policy_ref": self.policy_ref,
            "common_overrides": [],
            "files": [
                {
                    "client_file_id": f"f{index}",
                    "input_path": str(path),
                    "output_dir": str(self.output),
                    "overrides": [],
                }
                for index, path in enumerate(paths)
            ],
        }

    def key(self, prefix: str = "key") -> str:
        self.counter += 1
        return f"{prefix}-{self.counter:012d}"

    def resolution(self, session: dict[str, Any], who: str = SESSION):
        return self.admission.resolve(self.key("res"), session, session=who)[1]

    def submit_body(self, session: dict[str, Any], token: str | None = None) -> dict[str, Any]:
        token = token or self.resolution(session).token
        return {
            "schema_version": 1,
            "resolution_token": token,
            "editing_session": session,
            "resume_mode": "resume",
        }

    def submit(self, *paths: Path, key: str | None = None):
        body = self.submit_body(self.session(*paths))
        return self.admission.submit(
            IdempotencyScope("/api/v1/jobs", key or self.key()), body, session=SESSION
        )

    def count(self, table: str) -> int:
        with sqlite3.connect(self.store.path) as db:
            return int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path, None)


def code(excinfo: pytest.ExceptionInfo[BaseException]) -> str:
    value = excinfo.value
    if isinstance(value, ControlError):
        return value.code.value
    return getattr(value, "code", "")


def tables(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    with sqlite3.connect(path) as db:
        return {
            name: db.execute(f"SELECT * FROM {name}").fetchall()
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def scope(endpoint: str, key: str) -> IdempotencyScope:
    return IdempotencyScope(endpoint, key)


# --------------------------------------------------------------------------- batch atomicity


def test_single_and_multi_file_batches_commit_atomically(env: Env) -> None:
    one = env.submit(env.file("a.wav"))
    assert one.status == 202 and not one.replayed and len(one.batch.jobs) == 1
    assert one.headers["Location"] == "/api/v1/jobs"
    assert one.headers["ETag"] == f'"{one.batch.queue_revision}"'
    files = [env.file(f"m{i}.wav", f"audio {i}".encode()) for i in range(3)]
    many = env.submit(*files)
    assert len(many.batch.jobs) == 3 and many.batch.queue_revision == one.batch.queue_revision + 1
    queue = env.store.queue()
    assert [item.resource_id for item in queue.items] == [
        one.batch.jobs[0].id,
        *(job.id for job in many.batch.jobs),
    ]
    events = env.store.read_events().events
    # 4 job state_changed + 2 queue_changed (one per batch, not one per job).
    assert [e.type for e in events].count("state_changed") == 4
    assert [e.type for e in events].count("queue_changed") == 2
    job = env.store.get_job(many.batch.jobs[1].id)
    assert job.status == "queued" and job.snapshot.client_file_id == "f1"
    assert job.snapshot.input_identity.startswith("in_")
    wire = json.dumps(many.batch.to_wire(), ensure_ascii=False)
    assert SENTINEL not in wire and str(env.tmp) not in wire


def test_one_invalid_file_rejects_the_whole_batch(env: Env) -> None:
    good = env.file("good.wav")
    session = env.session(good, env.inputs / "missing.wav")
    with pytest.raises(AdmissionError) as excinfo:
        env.resolution(session)
    assert excinfo.value.status == 422
    assert [i.path for i in excinfo.value.issues] == ["/files/1/input_path"]
    assert all(str(env.tmp) not in i.to_wire()["path"] for i in excinfo.value.issues)
    assert env.count("operations") == 0 and env.count("outbox") == 0


@pytest.mark.parametrize("fault", ["batch_before_replay", "before_commit"])
def test_crash_before_commit_leaves_nothing(env: Env, fault: str) -> None:
    body = env.submit_body(env.session(env.file("a.wav"), env.file("b.wav", b"other")))
    before = tables(env.store.path)

    def hook(where: str) -> None:
        if where == fault:
            raise ControlError(E.STORE_FULL)

    env.store._fault = hook
    with pytest.raises(ControlError):
        env.admission.submit(scope("/api/v1/jobs", "crash-key-000001"), body, session=SESSION)
    env.store._fault = None
    assert tables(env.store.path) == before
    # The token was released, so the same submission can be retried and succeeds once.
    accepted = env.admission.submit(
        scope("/api/v1/jobs", "crash-key-000001"), body, session=SESSION
    )
    assert len(accepted.batch.jobs) == 2


def test_lost_response_after_commit_replays_original_batch(env: Env) -> None:
    body = env.submit_body(env.session(env.file("a.wav"), env.file("b.wav", b"x")))
    target = scope("/api/v1/jobs", "lost-key-0000001")

    class Lost(Exception):
        pass

    def hook(where: str) -> None:
        if where == "after_commit":
            raise Lost  # committed, but the response never reaches the client

    env.store._fault = hook
    with pytest.raises(Lost):
        env.admission.submit(target, body, session=SESSION)
    env.store._fault = None
    replay = env.admission.submit(target, body, session=SESSION)
    restarted = JobAdmission(
        store=JobControlStore(env.store.path),
        settings_path=env.settings_path,
        provider=env.provider,
    )
    again = restarted.submit(target, body, session="another-session-after-restart")
    assert replay.replayed and again.replayed
    assert replay.batch.to_wire() == again.batch.to_wire()
    assert dict(replay.headers) == dict(again.headers)
    assert env.count("operations") == 2 and env.count("batch_replays") == 1
    changed = dict(body, resume_mode="fresh")
    with pytest.raises(ControlError) as excinfo:
        env.admission.submit(target, changed, session=SESSION)
    assert code(excinfo) == "idempotency_conflict"


def test_concurrent_resends_create_one_batch(env: Env) -> None:
    body = env.submit_body(env.session(env.file("a.wav")))
    target = scope("/api/v1/jobs", "parallel-key-0001")
    stores = [JobControlStore(env.store.path, timeout=10) for _ in range(6)]
    barrier = threading.Barrier(6)

    def send(index: int) -> Any:
        admission = env.admission if index % 2 == 0 else None
        barrier.wait()
        if admission is None:
            # Other "processes": separate store connections sharing the same token registry.
            other = JobAdmission(
                store=stores[index],
                settings_path=env.settings_path,
                provider=env.provider,
                tokens=env.admission.tokens,
            )
            try:
                return other.submit(target, body, session=SESSION)
            except AdmissionError as exc:
                return exc.code
        try:
            return admission.submit(target, body, session=SESSION)
        except AdmissionError as exc:
            return exc.code

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(send, range(6)))
    batches = [r for r in results if not isinstance(r, str)]
    assert batches and {tuple(j.id for j in r.batch.jobs) for r in batches} == {
        tuple(j.id for j in batches[0].batch.jobs)
    }
    assert all(r in {"resolution_consumed"} for r in results if isinstance(r, str))
    assert env.count("operations") == 1
    # After the commit every resend replays, independent of the consumed token.
    assert env.admission.submit(target, body, session=SESSION).replayed


def test_two_processes_resend_same_key(env: Env, tmp_path: Path) -> None:
    body = env.submit_body(env.session(env.file("a.wav")))
    accepted = env.admission.submit(
        scope("/api/v1/jobs", "proc-key-00000001"), body, session=SESSION
    )
    script = (
        "import json,sys\nfrom pathlib import Path\n"
        "from utteran_control.job_repository import JobControlStore\n"
        "from utteran_control.store import IdempotencyScope\n"
        "s=JobControlStore(Path(sys.argv[1]))\nb=json.loads(sys.argv[2])\n"
        "r=s.lookup_batch(IdempotencyScope('/api/v1/jobs','proc-key-00000001'),s.request_digest(b))\n"
        "print(json.dumps([j.id for j in r.batch.jobs]))\n"
    )
    out = [
        subprocess.run(
            [sys.executable, "-c", script, str(env.store.path), json.dumps(body)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for _ in range(2)
    ]
    assert {json.dumps([j.id for j in accepted.batch.jobs]) + "\n"} == set(out)


def test_operation_and_batch_replay_scopes_are_not_shared(env: Env) -> None:
    env.store.submit(
        "/api/v1/jobs",
        "shared-key-000001",
        {"schema_version": 1, "kind": "job", "private_payload": {}},
    )
    body = env.submit_body(env.session(env.file("a.wav")))
    with pytest.raises(ControlError) as excinfo:
        env.admission.submit(scope("/api/v1/jobs", "shared-key-000001"), body, session=SESSION)
    assert code(excinfo) == "idempotency_conflict"
    # The B6 operation (no snapshot) is not a B7 job and is never dispatched.
    assert env.store.list_jobs().items == () and env.store.next_dispatchable() is None


def test_snapshots_are_immutable_in_the_database(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    with sqlite3.connect(env.store.path) as db:
        for statement in (
            "UPDATE job_snapshots SET body = '{}'",
            "DELETE FROM job_snapshots",
            "UPDATE batch_replays SET status = 200",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(statement)
    snapshot = env.store.get_snapshot(job.id)
    assert snapshot.resolved.settings.asr.initial_prompt == SENTINEL
    with sqlite3.connect(env.store.path) as db:
        db.execute("DROP TRIGGER job_snapshots_immutable_UPDATE")
        db.execute("UPDATE job_snapshots SET body = replace(body, 'resume', 'fresh')")
    with pytest.raises(ControlError) as excinfo:
        env.store.get_job(job.id)
    assert code(excinfo) == "store_corrupt"  # digest mismatch is detected, not trusted


# --------------------------------------------------------------------------- migration


def test_v2_store_migrates_to_v3_and_b6_code_refuses_v3(tmp_path: Path) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    old = ControlStore(path)
    created = old.submit(
        "/api/v1/jobs",
        "v2-operation-key-1",
        {"schema_version": 1, "kind": "job", "private_payload": {"note": "x"}},
    )
    before = tables(path)
    store = JobControlStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    after = tables(path)
    assert all(after[name] == rows for name, rows in before.items())
    assert store.submit(
        "/api/v1/jobs",
        "v2-operation-key-1",
        {"schema_version": 1, "kind": "job", "private_payload": {"note": "x"}},
    ).replayed
    assert store.get(created.operation.id).status == "queued"
    snapshot = path.read_bytes()
    with pytest.raises(ControlError) as excinfo:
        ControlStore(path)
    assert code(excinfo) == "store_schema_unsupported" and path.read_bytes() == snapshot
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=4")
    with pytest.raises(ControlError) as excinfo:
        JobControlStore(path)
    assert code(excinfo) == "store_schema_unsupported"


def test_v1_store_migrates_through_v2_to_v3(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "b6_v1_for_b7", ROOT / "tests/fixtures/b6/store_v1.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["b6_v1_for_b7"] = module
    spec.loader.exec_module(module)
    path = tmp_path / "v1" / "operations.sqlite3"
    module.ControlStore(path).submit(
        "/api/v1/jobs",
        "v1-operation-key-1",
        {"schema_version": 1, "kind": "job", "private_payload": {}},
    )
    JobControlStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"queue_replays", "job_snapshots", "batch_replays", "job_replays"} <= names


def test_failed_v3_migration_keeps_v2(tmp_path: Path) -> None:
    path = tmp_path / "c" / "operations.sqlite3"
    ControlStore(path)

    def hook(where: str) -> None:
        if where == "before_commit":
            raise ControlError(E.STORE_FULL)

    with pytest.raises(ControlError):
        JobControlStore(path, _fault=hook)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    ControlStore(path)  # still a valid B6 store
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE job_snapshots (x)")  # v2 header with v3 table: refuse
    with pytest.raises(ControlError) as excinfo:
        JobControlStore(path)
    assert code(excinfo) == "store_corrupt"


# --------------------------------------------------------------------------- admission


def test_editing_inheritance_provenance_is_frozen(env: Env) -> None:
    session = env.session(env.file("a.wav"))
    session["common_overrides"] = [{"path": "/asr/beam_size", "value": 3}]
    session["files"][0]["overrides"] = [{"path": "/asr/language", "value": " auto "}]
    resolution = env.resolution(session)
    body = env.submit_body(session, resolution.token)
    job = env.admission.submit(scope("/api/v1/jobs", env.key()), body, session=SESSION).batch.jobs[
        0
    ]
    snapshot = env.store.get_snapshot(job.id)
    sources = {p.path: (p.source, p.input_source, p.input_status) for p in snapshot.provenance}
    assert sources["/asr/beam_size"] == ("common", "common", "value")
    assert sources["/asr/language"] == ("normalization", "file", "value")
    assert sources["/asr/initial_prompt"] == ("preset", "preset", "value")
    assert sources["/asr/vad/threshold"][2] == "absent"
    assert snapshot.resolved.settings.asr.beam_size == 3
    assert snapshot.resolved.settings.asr.language == "auto"
    assert snapshot.common_overrides[0].path == "/asr/beam_size"
    assert snapshot.policy.id == env.policy_ref["id"] and snapshot.resume_mode == "resume"


def test_new_preset_revision_does_not_change_frozen_reference(env: Env) -> None:
    session = env.session(env.file("a.wav"))
    token = env.resolution(session).token
    repo = Repository(env.settings_path)
    current = repo.get("presets", env.preset_ref["id"])
    data = current.to_wire()
    data["settings"]["asr"]["beam_size"] = 9
    repo.mutate(
        "presets",
        current.id,
        1,
        PresetWrite.model_validate({k: data[k] for k in ("schema_version", "name", "settings")}),
    )
    job = env.admission.submit(
        scope("/api/v1/jobs", env.key()), env.submit_body(session, token), session=SESSION
    ).batch.jobs[0]
    assert env.store.get_snapshot(job.id).resolved.settings.asr.beam_size == 5


@pytest.mark.parametrize(
    "mutate, expected",
    [
        ("expire", "resolution_expired"),
        ("session", "resolution_invalid"),
        ("forge", "resolution_invalid"),
        ("edit", "resolution_stale"),
        ("input", "resolution_stale"),
        ("candidate", "resolution_stale"),
        ("model_dir", "resolution_stale"),
        ("route", "resolution_stale"),
    ],
)
def test_stale_tokens_are_refused_without_rows(env: Env, mutate: str, expected: str) -> None:
    path = env.file("a.wav")
    session = env.session(path)
    token = env.resolution(session).token
    who = SESSION
    if mutate == "expire":
        env.clock.advance(301)
    elif mutate == "session":
        who = "other-session"
    elif mutate == "forge":
        token = token[:-1] + ("0" if token[-1] != "0" else "1")
    elif mutate == "edit":
        session["files"][0]["overrides"] = [{"path": "/asr/beam_size", "value": 2}]
    elif mutate == "input":
        path.write_bytes(b"RIFF changed content!")
    elif mutate == "candidate":
        env.provider.updates = {"model_files_fingerprint": digest("other files")}
    elif mutate == "model_dir":
        env.provider.model_dir = env.tmp / "elsewhere"
    elif mutate == "route":
        env.provider.updates = {"profile_generation": "generation-2"}
    body = {
        "schema_version": 1,
        "resolution_token": token,
        "editing_session": session,
        "resume_mode": "resume",
    }
    with pytest.raises(AdmissionError) as excinfo:
        env.admission.submit(scope("/api/v1/jobs", env.key()), body, session=who)
    assert excinfo.value.code == expected and excinfo.value.status == 409
    assert env.count("operations") == 0 and env.count("batch_replays") == 0


def test_tokens_are_single_use_and_opaque(env: Env) -> None:
    session = env.session(env.file("a.wav"))
    resolution = env.resolution(session)
    assert SENTINEL not in resolution.token and "a.wav" not in resolution.token
    wire = json.dumps(resolution.to_wire(), ensure_ascii=False)
    assert SENTINEL not in wire and str(env.tmp) not in wire
    body = env.submit_body(session, resolution.token)
    env.admission.submit(scope("/api/v1/jobs", env.key()), body, session=SESSION)
    with pytest.raises(AdmissionError) as excinfo:
        env.admission.submit(scope("/api/v1/jobs", env.key()), body, session=SESSION)
    assert excinfo.value.code == "resolution_consumed"
    for bad in ("v1.zz", "eyJzZXR0aW5ncyI6e319", "v1." + "0" * 32 + ".0000000000000." + "0" * 64):
        with pytest.raises(AdmissionError):
            env.admission.tokens.reserve(bad, ResolutionTokens.binding(SESSION), "0" * 64)
    restarted = ResolutionTokens()
    with pytest.raises(AdmissionError):  # restart invalidates every token
        restarted.reserve(resolution.token, ResolutionTokens.binding(SESSION), "0" * 64)


def test_resolution_replay_is_keyed(env: Env) -> None:
    session = env.session(env.file("a.wav"))
    first = env.admission.resolve("resolve-key-0000001", session, session=SESSION)
    again = env.admission.resolve("resolve-key-0000001", session, session=SESSION)
    assert again[2] and again[1].token == first[1].token
    with pytest.raises(AdmissionError) as excinfo:
        env.admission.resolve(
            "resolve-key-0000001", env.session(env.file("b.wav", b"z")), session=SESSION
        )
    assert excinfo.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    "updates, status",
    [
        (lambda s: s["diarization"].update(enabled=True), 422),
        (lambda s: s["asr"].update(glossary=["term"]), 422),
        (
            lambda s: (
                s["asr"].update(backend="whisper-cpp", faster_whisper=None)
                or s["asr"].pop("faster_whisper")
            ),
            422,
        ),
    ],
)
def test_worker_unsupported_settings_are_refused_declaratively(
    tmp_path: Path, updates, status
) -> None:
    env = Env(tmp_path, updates)
    with pytest.raises(AdmissionError) as excinfo:
        env.resolution(env.session(env.file("a.wav")))
    assert excinfo.value.status == status
    assert env.provider.calls == 0  # never resolved, never routed to the legacy CLI


@pytest.mark.parametrize(
    "recipe, updates, issue",
    [
        ("wc-cpu", {}, "no_runnable_candidate"),
        ("fw-cpu", {"local_complete_model": False}, "no_runnable_candidate"),
    ],
)
def test_unrunnable_candidates_are_execution_unavailable(env: Env, recipe, updates, issue) -> None:
    env.provider.recipe = recipe
    env.provider.updates = updates
    with pytest.raises(AdmissionError) as excinfo:
        env.resolution(env.session(env.file("a.wav")))
    assert excinfo.value.code == "execution_unavailable" and excinfo.value.status == 409
    assert {i.code for i in excinfo.value.issues} == {issue}


def test_fixed_policy_without_candidate_and_missing_model_dir(env: Env) -> None:
    repo = Repository(env.settings_path)
    data = examples()["policy"] | {
        "mode": "fixed",
        "fixed_candidate_id": "nope",
        "fallback": "none",
    }
    write = PolicyWrite.model_validate(data)
    _, _, pol = repo.post(
        "/api/v1/execution-policies", "policy-key-000002", write.to_wire(), payload=write
    )
    session = env.session(env.file("a.wav"))
    session["policy_ref"] = {"id": pol["id"], "revision": 1}
    with pytest.raises(AdmissionError) as excinfo:
        env.resolution(session)
    assert {i.code for i in excinfo.value.issues} == {"fixed_candidate_unavailable"}
    original = env.provider.resolve

    def no_models(*args: Any, **kwargs: Any) -> ProviderResult:
        result = original(*args, **kwargs)
        return ProviderResult(result.reason_code, result.selection, {})

    env.provider.resolve = no_models  # type: ignore[method-assign]
    with pytest.raises(AdmissionError) as excinfo:
        env.resolution(env.session(env.file("b.wav", b"b")))
    assert "worker_model_not_local" in {i.code for i in excinfo.value.issues}


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_input_identity_refuses_links_dirs_empty_and_relative(env: Env) -> None:
    real = env.file("real.wav")
    link = env.inputs / "link.wav"
    link.symlink_to(real)
    (env.inputs / "empty.wav").write_bytes(b"")
    for path, expected in (
        (str(link), "input_link"),
        (str(env.inputs), "input_not_file"),
        (str(env.inputs / "empty.wav"), "input_empty"),
        ("relative.wav", "input_path_invalid"),
        ("//server/share/a.wav", "input_network"),
    ):
        with pytest.raises(AdmissionError) as excinfo:
            capture_input(path)
        assert excinfo.value.issues[0].code == expected
    identity = capture_input(str(real))
    assert identity.sha256 == hashlib.sha256(real.read_bytes()).hexdigest()
    os.utime(real, ns=(identity.mtime_ns, identity.mtime_ns))
    real.write_bytes(b"RIFF synthetic audiX")  # same size, different content
    os.utime(real, ns=(identity.mtime_ns, identity.mtime_ns))
    assert capture_input(str(real)) != identity  # content digest, not mtime/inode only


def test_batch_size_limit(env: Env) -> None:
    env.admission.max_files = 2
    files = [env.file(f"x{i}.wav", bytes([i + 1])) for i in range(3)]
    with pytest.raises(AdmissionError) as excinfo:
        env.resolution(env.session(*files))
    assert excinfo.value.issues[0].code == "batch_too_large"


# --------------------------------------------------------------------------- queue


def items(env: Env) -> list[str]:
    return [item.id for item in env.store.queue().items]


def test_reorder_full_permutation_with_queue_cas(env: Env) -> None:
    env.submit(*(env.file(f"q{i}.wav", bytes([i + 1])) for i in range(3)))
    queue = env.store.queue()
    ids = items(env)
    reordered = env.store.reorder(
        queue.revision, ids[::-1], scope("/api/v1/queue/order", "order-key-0000001")
    )
    assert [i.id for i in reordered.queue.items] == ids[::-1]
    assert reordered.queue.revision == queue.revision + 1
    assert [i.position for i in reordered.queue.items] == [1, 2, 3]
    replay = env.store.reorder(
        queue.revision, ids[::-1], scope("/api/v1/queue/order", "order-key-0000001")
    )
    assert replay.replayed and replay.queue == reordered.queue  # replay precedes CAS
    noop = env.store.reorder(
        reordered.queue.revision, ids[::-1], scope("/api/v1/queue/order", "order-key-0000002")
    )
    assert noop.queue.revision == reordered.queue.revision
    rev = noop.queue.revision
    for bad, expected in (
        ([ids[0], ids[0], ids[1]], "request_invalid"),
        (ids[:2], "request_invalid"),
        ([*ids[:2], "qi_" + "f" * 32], "request_invalid"),
    ):
        with pytest.raises(ControlError) as excinfo:
            env.store.reorder(rev, bad, scope("/api/v1/queue/order", env.key()))
        assert code(excinfo) == expected
    with pytest.raises(ControlError) as excinfo:
        env.store.reorder(rev - 1, ids, scope("/api/v1/queue/order", env.key()))
    assert code(excinfo) == "revision_conflict"
    assert env.store.queue().revision == rev


def test_active_item_in_order_is_409(env: Env) -> None:
    env.submit(env.file("a.wav"), env.file("b.wav", b"b"))
    first, revision = env.store.next_dispatchable()  # type: ignore[misc]
    env.store.begin_attempt(first, revision, current_owner())
    ids = items(env)
    with pytest.raises(ControlError) as excinfo:
        env.store.reorder(env.store.queue().revision, ids, scope("/api/v1/queue/order", env.key()))
    assert code(excinfo) == "invalid_transition"


def test_item_hold_uses_queue_revision_and_keeps_blocks(env: Env) -> None:
    jobs = env.submit(env.file("a.wav"), env.file("b.wav", b"b")).batch.jobs
    queue = env.store.queue()
    item = queue.items[0].id
    with pytest.raises(ControlError) as excinfo:
        env.store.set_item_hold(
            item,
            env.store.get_job(jobs[0].id).revision + 99,
            True,
            scope("/api/v1/queue/items/" + item, env.key()),
        )
    assert code(excinfo) == "revision_conflict"
    held = env.store.set_item_hold(
        item, queue.revision, True, scope("/api/v1/queue/items/" + item, "hold-key-00000001")
    )
    assert held.queue.items[0].held and env.store.get_job(jobs[0].id).status == "held"
    again = env.store.set_item_hold(
        item, queue.revision, True, scope("/api/v1/queue/items/" + item, "hold-key-00000001")
    )
    assert again.replayed and again.queue == held.queue
    noop = env.store.set_item_hold(
        item, held.queue.revision, True, scope("/api/v1/queue/items/" + item, env.key())
    )
    assert noop.queue.revision == held.queue.revision
    # The held item is skipped; the next eligible item is dispatched first.
    assert env.store.next_dispatchable()[0] == jobs[1].id  # type: ignore[index]
    blocked = env.store.block_job(
        jobs[0].id, env.store.get_job(jobs[0].id).revision, "input_changed"
    )
    assert blocked.status == "blocked" and blocked.held
    released = env.store.set_item_hold(
        item, env.store.queue().revision, False, scope("/api/v1/queue/items/" + item, env.key())
    )
    job = env.store.get_job(jobs[0].id)
    assert not released.queue.items[0].held and job.status == "blocked" and job.blockers
    with pytest.raises(ControlError) as excinfo:
        env.store.set_item_hold(
            "qi_" + "0" * 32,
            env.store.queue().revision,
            True,
            scope("/api/v1/queue/items/x", env.key()),
        )
    assert code(excinfo) == "resource_not_found"


def test_queue_replay_and_dispatch_control(env: Env) -> None:
    env.submit(env.file("a.wav"))
    queue = env.store.queue()
    paused = env.store.set_dispatch(
        queue.revision, "paused", idempotency=scope("/api/v1/queue/control", "ctl-key-000000001")
    )
    assert env.store.next_dispatchable() is None
    replay = env.store.set_dispatch(
        queue.revision, "paused", idempotency=scope("/api/v1/queue/control", "ctl-key-000000001")
    )
    assert replay.replayed and replay.queue == paused.queue


def test_parallel_reorder_hold_and_dispatch_serialize(env: Env) -> None:
    env.submit(*(env.file(f"p{i}.wav", bytes([i + 1])) for i in range(4)))
    revision = env.store.queue().revision
    ids = items(env)
    owner = current_owner()
    results: list[str] = []

    def reorder() -> None:
        try:
            env.store.reorder(
                revision, ids[::-1], scope("/api/v1/queue/order", "race-key-00000001")
            )
            results.append("reorder")
        except ControlError as exc:
            results.append(exc.code.value)

    def hold() -> None:
        try:
            env.store.set_item_hold(
                ids[0], revision, True, scope("/api/v1/queue/items/" + ids[0], "race-key-00000002")
            )
            results.append("hold")
        except ControlError as exc:
            results.append(exc.code.value)

    def dispatch() -> None:
        nxt = env.store.next_dispatchable()
        try:
            if nxt:
                env.store.begin_attempt(nxt[0], nxt[1], owner)
                results.append("dispatch")
        except ControlError as exc:
            results.append(exc.code.value)

    threads = [threading.Thread(target=t) for t in (reorder, hold, dispatch)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly one queue CAS winner; at most one active item; order stays a permutation.
    assert results.count("reorder") + results.count("hold") <= 1
    queue = env.store.queue()
    assert sum(1 for i in queue.items if i.position is None) <= 1
    assert sorted(i.id for i in queue.items) == sorted(ids)


# --------------------------------------------------------------------------- dispatch


class FakeRunner:
    """Spawns a real local child, then reports a synthetic, verified-looking result."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self.mode = "success"
        self.requests: list[WorkerRequest] = []
        self.started = threading.Event()
        self.child_script = "import time; time.sleep(0)"
        self.keep_alive = False
        self.children: list[subprocess.Popen[bytes]] = []

    def execute(
        self, request: WorkerRequest, *, cancel=None, on_event=None, on_spawn=None
    ) -> WorkerCompleted:
        self.requests.append(request)
        if self.mode == "start_failed":
            raise WorkerClientError(WorkerErrorCode.START_FAILED)
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                self.child_script if not self.keep_alive else "import time; time.sleep(30)",
            ]
        )
        self.children.append(child)
        if on_spawn is not None:
            on_spawn(child)
        self.started.set()
        if self.mode in {"wait_cancel", "complete_after_cancel"}:
            assert cancel is not None
            cancel.wait(10)
        if self.mode == "change_input":
            Path(request.input_path).write_bytes(b"RIFF modified during run")
        if not self.keep_alive:
            child.wait()
        if self.mode == "wait_cancel":
            raise WorkerClientError(WorkerErrorCode.CANCELLED)
        if self.mode == "crash":
            raise WorkerClientError(WorkerErrorCode.WORKER_CRASH)
        if self.mode == "artifact_invalid":
            raise WorkerClientError(WorkerErrorCode.ARTIFACT_INVALID)
        artifact = WorkerArtifact(path=str(self.env.output / "a.srt"), size=1, sha256="a" * 64)
        manifest = WorkerArtifact(path=str(self.env.tmp / "manifest.json"), size=1, sha256="b" * 64)
        return WorkerCompleted(
            sequence=5,
            request_id=request.request_id,
            job_id=request.job_id,
            attempt_id=request.attempt_id,
            core_job_id="0123456789abcdef",
            artifacts=(artifact,),
            manifest=manifest,
        )


def dispatcher(env: Env, runner: FakeRunner, **kwargs: Any) -> JobDispatcher:
    return JobDispatcher(
        env.store,
        prestart=env.admission.prestart_check,
        runner=runner,
        owner=current_owner(),
        job_dir=env.tmp / "jobs",
        **kwargs,
    )


def test_supported_path_succeeds_with_launch_journal(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    runner = FakeRunner(env)
    assert dispatcher(env, runner).run_once()
    done = env.store.get_job(job.id)
    assert done.status == "succeeded" and done.result is not None
    assert done.result.legacy_core_job_id == "0123456789abcdef" and done.result.artifact_count == 1
    assert done.attempts[0].launch == "confirmed" and done.attempts[0].state == "released"
    request = runner.requests[0]
    assert request.context.asr_model == str(env.model) and request.job_id == job.id
    assert request.attempt_id == done.attempts[0].id and request.resume and not request.force
    with sqlite3.connect(env.store.path) as db:
        child_pid, child_start = db.execute(
            "SELECT child_pid, child_start FROM attempt_private"
        ).fetchone()
    assert child_pid == runner.children[0].pid
    if LINUX:
        assert child_start.startswith("linux:")
    assert env.store.queue().items == () and env.store.active_job() is None


def test_paused_held_blocked_are_not_dispatched(env: Env) -> None:
    jobs = env.submit(env.file("a.wav"), env.file("b.wav", b"b")).batch.jobs
    runner = FakeRunner(env)
    d = dispatcher(env, runner)
    queue = env.store.queue()
    env.store.set_dispatch(queue.revision, "paused")
    assert not d.run_once() and runner.requests == []
    queue = env.store.set_dispatch(env.store.queue().revision, "running").queue
    env.store.set_item_hold(
        queue.items[0].id, queue.revision, True, scope("/api/v1/queue/items/x", env.key())
    )
    assert d.run_once()
    assert runner.requests[0].job_id == jobs[1].id
    assert env.store.get_job(jobs[0].id).status == "held"


def test_prestart_revalidation_blocks_and_revalidate_restores(env: Env) -> None:
    path = env.file("a.wav")
    job = env.submit(path).batch.jobs[0]
    path.write_bytes(b"RIFF replaced before start")
    runner = FakeRunner(env)
    assert dispatcher(env, runner).run_once() and runner.requests == []
    blocked = env.store.get_job(job.id)
    assert blocked.status == "blocked" and blocked.block_reason == "input_changed"
    with pytest.raises(AdmissionError) as excinfo:
        env.admission.revalidate(
            job.id, blocked.revision, scope(f"/api/v1/jobs/{job.id}/revalidate", env.key())
        )
    assert excinfo.value.code == "revalidation_failed"
    assert env.store.get_job(job.id).status == "blocked"  # same snapshot, still blocked


def test_revalidate_never_reselects_a_new_candidate(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    env.provider.updates = {"model_revision": "revision-2"}
    runner = FakeRunner(env)
    dispatcher(env, runner).run_once()
    blocked = env.store.get_job(job.id)
    # A new model revision is a different candidate: never silently re-selected.
    assert blocked.status == "blocked" and blocked.block_reason == "candidate_unavailable"
    with pytest.raises(AdmissionError):
        env.admission.revalidate(
            job.id, blocked.revision, scope(f"/api/v1/jobs/{job.id}/revalidate", env.key())
        )
    snapshot = env.store.get_snapshot(job.id)
    assert snapshot.resolved.model_revision == "revision-1"  # unchanged snapshot
    env.provider.updates = {}
    env.provider.fail = True
    assert env.admission.prestart_check(snapshot) == "resolver_unavailable"
    env.provider.fail = False
    restored = env.admission.revalidate(
        job.id,
        env.store.get_job(job.id).revision,
        scope(f"/api/v1/jobs/{job.id}/revalidate", env.key()),
    )
    assert restored.job.status == "queued"


def run_async(d: JobDispatcher) -> threading.Thread:
    thread = threading.Thread(target=d.run_once)
    thread.start()
    return thread


def test_cancel_running_converges_to_cancelled(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    runner = FakeRunner(env)
    runner.mode = "wait_cancel"
    d = dispatcher(env, runner)
    thread = run_async(d)
    assert runner.started.wait(10)
    running = env.store.get_job(job.id)
    accepted = env.store.cancel_job(
        job.id, running.revision, scope(f"/api/v1/jobs/{job.id}/cancel", "cancel-key-000001")
    )
    assert accepted.status == 202 and accepted.job.status == "cancelling"
    assert env.store.active_job() is not None  # 202 is not "stopped": lease kept
    d.notify_cancel(job.id)
    thread.join(10)
    final = env.store.get_job(job.id)
    assert final.status == "cancelled" and final.result is None and env.store.active_job() is None
    replay = env.store.cancel_job(
        job.id, running.revision, scope(f"/api/v1/jobs/{job.id}/cancel", "cancel-key-000001")
    )
    assert replay.replayed and replay.job.status == "cancelling"
    terminal = env.store.cancel_job(
        job.id, final.revision, scope(f"/api/v1/jobs/{job.id}/cancel", env.key())
    )
    assert terminal.status == 200 and terminal.job.status == "cancelled"


def test_cancel_before_completion_wins_even_on_success(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    runner = FakeRunner(env)
    runner.mode = "complete_after_cancel"
    d = dispatcher(env, runner)
    thread = run_async(d)
    assert runner.started.wait(10)
    env.store.cancel_job(
        job.id,
        env.store.get_job(job.id).revision,
        scope(f"/api/v1/jobs/{job.id}/cancel", env.key()),
    )
    d.notify_cancel(job.id)
    thread.join(10)
    final = env.store.get_job(job.id)
    assert final.status == "cancelled" and final.result is None
    assert env.count("job_results") == 0


def test_completion_before_cancel_keeps_success(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    dispatcher(env, FakeRunner(env)).run_once()
    done = env.store.get_job(job.id)
    late = env.store.cancel_job(
        job.id, done.revision, scope(f"/api/v1/jobs/{job.id}/cancel", env.key())
    )
    assert late.status == 200 and late.job.status == "succeeded"


def test_waiting_cancel_is_immediate(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    accepted = env.store.cancel_job(job.id, 1, scope(f"/api/v1/jobs/{job.id}/cancel", env.key()))
    assert accepted.status == 202 and accepted.job.status == "cancelled"
    assert env.store.queue().items == ()
    with pytest.raises(ControlError) as excinfo:
        env.store.cancel_job(job.id, 1, scope(f"/api/v1/jobs/{job.id}/cancel", env.key()))
    assert code(excinfo) == "revision_conflict"


@pytest.mark.parametrize(
    "mode, error",
    [
        ("crash", "worker_crash"),
        ("artifact_invalid", "artifact_invalid"),
        ("start_failed", "start_failed"),
        ("change_input", "input_changed"),
    ],
)
def test_failures_never_succeed(env: Env, mode: str, error: str) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    runner = FakeRunner(env)
    runner.mode = mode
    dispatcher(env, runner).run_once()
    final = env.store.get_job(job.id)
    assert final.status == "failed" and final.error is not None and final.error.code == error
    assert final.result is None and env.store.active_job() is None
    if mode == "start_failed":
        assert final.attempts[0].launch == "failed"


def test_unobserved_child_exit_keeps_slot_and_requires_recovery(env: Env) -> None:
    jobs = env.submit(env.file("a.wav"), env.file("b.wav", b"b")).batch.jobs
    runner = FakeRunner(env)
    runner.keep_alive = True
    try:
        d = dispatcher(env, runner)
        d.run_once()
        job = env.store.get_job(jobs[0].id)
        assert job.status == "running" and job.recovery_required == "termination_failed"
        assert env.store.active_job() is not None
        assert not d.run_once()  # second job is never started while the slot is held
        report = env.store.recover(OsProcessVerifier(), current_owner=None)
        if LINUX:
            # Owner (this test process) is alive under another identity record: the stored
            # owner identity is current_owner() of this process, so it is observed alive.
            assert jobs[0].id in report.owner_alive + report.recovery_required
        assert env.store.active_job() is not None
    finally:
        for child in runner.children:
            child.kill()
            child.wait()


def test_stale_fencing_token_is_rejected(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    grant = env.store.begin_attempt(job.id, 1, current_owner())
    with pytest.raises(ControlError) as excinfo:
        env.store.finish_attempt(job.id, grant.fencing_token + 1, "failed", error="worker_crash")
    assert code(excinfo) == "fencing_token_stale"
    with pytest.raises(ControlError):
        env.store.finish_attempt(job.id, grant.fencing_token, "succeeded")  # no evidence


def test_single_heavy_slot_between_two_dispatchers(env: Env) -> None:
    env.submit(env.file("a.wav"), env.file("b.wav", b"b"))
    nxt = env.store.next_dispatchable()
    assert nxt is not None
    other = JobControlStore(env.store.path)
    env.store.begin_attempt(nxt[0], nxt[1], current_owner())
    with pytest.raises(ControlError):
        other.begin_attempt(nxt[0], nxt[1], current_owner())
    assert other.next_dispatchable() is None


def test_retry_copies_snapshot_and_refuses_changed_input(env: Env) -> None:
    path = env.file("a.wav")
    job = env.submit(path).batch.jobs[0]
    runner = FakeRunner(env)
    runner.mode = "crash"
    dispatcher(env, runner).run_once()
    failed = env.store.get_job(job.id)
    with pytest.raises(ControlError) as excinfo:
        env.admission.retry(
            job.id, failed.revision + 1, scope(f"/api/v1/jobs/{job.id}/retry", env.key())
        )
    assert code(excinfo) == "revision_conflict"
    retry = env.admission.retry(
        job.id, failed.revision, scope(f"/api/v1/jobs/{job.id}/retry", "retry-key-0000001")
    )
    assert (
        retry.status == 202
        and retry.job.retry_of == job.id
        and retry.headers["Location"].endswith(retry.job.id)
    )
    with sqlite3.connect(env.store.path) as db:
        bodies = {r[0] for r in db.execute("SELECT body FROM job_snapshots")}
    assert len(bodies) == 1  # identical snapshot body
    assert env.store.get_job(job.id).status == "failed"  # terminal never rewound
    assert env.admission.retry(
        job.id, failed.revision, scope(f"/api/v1/jobs/{job.id}/retry", "retry-key-0000001")
    ).replayed
    path.write_bytes(b"RIFF other content")
    with pytest.raises(AdmissionError) as excinfo:
        env.admission.retry(
            job.id, failed.revision, scope(f"/api/v1/jobs/{job.id}/retry", env.key())
        )
    assert excinfo.value.code == "input_changed"


def test_retry_of_success_and_rerun_rules(env: Env) -> None:
    path = env.file("a.wav")
    job = env.submit(path).batch.jobs[0]
    with pytest.raises(ControlError):
        env.admission.retry(job.id, 1, scope(f"/api/v1/jobs/{job.id}/retry", env.key()))  # queued
    session = env.session(path)
    body = {"schema_version": 1, "submission": env.submit_body(session)}
    with pytest.raises(ControlError) as excinfo:
        env.admission.rerun(
            job.id, 1, scope(f"/api/v1/jobs/{job.id}/rerun", env.key()), body, session=SESSION
        )
    assert code(excinfo) == "invalid_transition"  # rerun only from a terminal job
    dispatcher(env, FakeRunner(env)).run_once()
    done = env.store.get_job(job.id)
    with pytest.raises(ControlError):
        env.admission.retry(job.id, done.revision, scope(f"/api/v1/jobs/{job.id}/retry", env.key()))
    body = {"schema_version": 1, "submission": env.submit_body(session)}
    rerun = env.admission.rerun(
        job.id,
        done.revision,
        scope(f"/api/v1/jobs/{job.id}/rerun", env.key()),
        body,
        session=SESSION,
    )
    assert rerun.batch.jobs[0].rerun_of == job.id and rerun.batch.jobs[0].status == "queued"
    two = env.session(path, env.file("b.wav", b"b"))
    with pytest.raises(AdmissionError):
        env.admission.rerun(
            job.id,
            done.revision,
            scope(f"/api/v1/jobs/{job.id}/rerun", env.key()),
            {"schema_version": 1, "submission": env.submit_body(two)},
            session=SESSION,
        )


def test_shutdown_cancels_cooperatively(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    runner = FakeRunner(env)
    runner.mode = "wait_cancel"
    d = dispatcher(env, runner, poll_interval=0.01)
    d.start()
    assert runner.started.wait(10)
    assert d.stop(10)
    assert env.store.get_job(job.id).status == "cancelled" and env.store.active_job() is None


def test_plan_request_refuses_unsupported_snapshots(env: Env) -> None:
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    snapshot = env.store.get_snapshot(job.id)
    request = plan_request(
        snapshot, job_id=job.id, attempt_id="att_" + "1" * 32, job_dir=env.tmp / "jobs"
    )
    assert request.context.base_config["general"]["output_dir"] == str(env.output)
    data = snapshot.to_wire()
    data["resolved"]["recipe_id"] = "wc-cpu"
    with pytest.raises(Exception, match="worker_plan_unsupported"):
        plan_request(
            JobSnapshot.model_validate(data),
            job_id=job.id,
            attempt_id="att_" + "1" * 32,
            job_dir=env.tmp / "jobs",
        )


# --------------------------------------------------------------------------- process verifier


def test_os_verifier_with_real_child_process() -> None:
    verifier = OsProcessVerifier()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        identity = start_identity(child.pid)
        if identity is None:
            pytest.skip("platform cannot provide a creation identity")
        assert (
            verifier.check(ProcessIdentity(pid=child.pid, start_identity=identity))
            is Liveness.ALIVE
        )
        assert (
            verifier.check(ProcessIdentity(pid=child.pid, start_identity=identity + "0"))
            is Liveness.PID_REUSED
        )
        assert (
            verifier.check(ProcessIdentity(pid=child.pid, start_identity=None)) is Liveness.UNKNOWN
        )
    finally:
        child.kill()
        child.wait()
    assert verifier.check(ProcessIdentity(pid=child.pid, start_identity=identity)) in {
        Liveness.GONE,
        Liveness.PID_REUSED,
    }
    assert current_owner().process.pid == os.getpid()


def test_verifier_exceptions_and_unreachable_pids_are_conservative(monkeypatch) -> None:
    import utteran_gui.process_verifier as module

    def boom(pid: int) -> Any:
        raise PermissionError

    monkeypatch.setattr(module, "_linux_stat", boom)
    if LINUX:
        assert (
            OsProcessVerifier().check(ProcessIdentity(pid=1, start_identity="x"))
            is Liveness.UNKNOWN
        )


def make_lease(
    env: Env, owner: OwnerIdentity, *, launch: str | None, child: ProcessIdentity | None
) -> str:
    job = env.submit(env.file(f"{os.urandom(4).hex()}.wav", os.urandom(8))).batch.jobs[0]
    grant = env.store.begin_attempt(job.id, env.store.get_job(job.id).revision, owner)
    if launch == "confirmed":
        env.store.record_launch(job.id, grant.fencing_token, outcome="confirmed", child=child)
    elif launch == "failed":
        env.store.record_launch(job.id, grant.fencing_token, outcome="failed")
    return job.id


@pytest.mark.skipif(not LINUX, reason="Linux /proc identity")
@pytest.mark.parametrize(
    "launch, child_alive, expected",
    [
        ("failed", None, "interrupted"),
        ("intended", None, "recovery_launch_unconfirmed"),
        ("confirmed", False, "interrupted"),
        ("confirmed", True, "recovery_child_alive"),
    ],
)
def test_recovery_with_real_dead_owner(env: Env, launch, child_alive, expected) -> None:
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_identity = start_identity(dead.pid) or "linux:none:0"
    dead.wait()
    owner = OwnerIdentity(
        owner_id="own_" + "a" * 32,
        nonce="b" * 64,
        process=ProcessIdentity(pid=dead.pid, start_identity=dead_identity),
    )
    child_process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)" if child_alive else "pass"]
    )
    child = ProcessIdentity(pid=child_process.pid, start_identity=start_identity(child_process.pid))
    if not child_alive:
        child_process.wait()
    try:
        job_id = make_lease(
            env, owner, launch=None if launch == "intended" else launch, child=child
        )
        report = env.store.recover(OsProcessVerifier())
        job = env.store.get_job(job_id)
        if expected == "interrupted":
            assert job.status == "interrupted" and job_id in report.interrupted
            assert env.store.active_job() is None
        else:
            assert job.recovery_required == expected and env.store.active_job() is not None
        assert report.queue_paused and env.store.queue().dispatch == "paused"
    finally:
        if child_alive:
            child_process.kill()
            child_process.wait()


# --------------------------------------------------------------------------- privacy


def test_public_tables_responses_and_logs_have_no_private_values(env: Env, caplog) -> None:
    caplog.set_level("DEBUG")
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    runner = FakeRunner(env)
    runner.mode = "crash"
    dispatcher(env, runner).run_once()
    env.admission.retry(
        job.id, env.store.get_job(job.id).revision, scope(f"/api/v1/jobs/{job.id}/retry", env.key())
    )
    env.store.set_dispatch(
        env.store.queue().revision, "paused", idempotency=scope("/api/v1/queue/control", env.key())
    )
    dump = tables(env.store.path)
    private = {"job_snapshots", "attempt_private"}
    public_text = json.dumps(
        {k: v for k, v in dump.items() if k not in private}, ensure_ascii=False, default=str
    )
    for secret in (SENTINEL, str(env.tmp), str(env.model), "a.wav"):
        assert secret not in public_text
        assert secret not in caplog.text
        assert secret not in json.dumps(env.store.list_jobs().to_wire(), ensure_ascii=False)
        assert secret not in json.dumps(env.store.read_events().to_wire(), ensure_ascii=False)
    assert SENTINEL in json.dumps(dump["job_snapshots"], ensure_ascii=False)  # private only


def test_list_jobs_cursor_is_stable_and_signed(env: Env) -> None:
    env.submit(*(env.file(f"l{i}.wav", bytes([i + 1])) for i in range(3)))
    first = env.store.list_jobs(limit=2)
    assert len(first.items) == 2 and first.next_cursor
    rest = env.store.list_jobs(first.next_cursor, limit=2)
    assert len(rest.items) == 1 and rest.next_cursor is None
    with pytest.raises(ControlError) as excinfo:
        env.store.list_jobs(first.next_cursor[:-1] + "0", limit=2)
    assert code(excinfo) == "cursor_invalid"


def test_bridge_resolve_payload_fails_closed() -> None:
    from utteran.capabilities.bridge import resolve_payload

    for raw in (b"{", b"[]", b'{"refresh": {}, "policy": {}}', b"x" * (1024 * 1024 + 1)):
        payload = resolve_payload(raw)
        assert payload["selection"] is None and payload["reason_code"] == "bridge_invalid"


def test_gui_bridge_resolve_rechecks_scope(env: Env, tmp_path: Path, monkeypatch) -> None:
    from utteran_contracts.capabilities import InventoryRefreshRequest
    from utteran_gui.capability_inventory import CapabilityInventoryAdapter
    from utteran_gui.cli import CliAdapter, ProfileInfo
    from utteran_gui.job_admission import BridgeResolutionProvider

    cli = CliAdapter(tmp_path)
    root = tmp_path / "profile"
    (root / "bin").mkdir(parents=True)
    (root / "Scripts").mkdir()
    (root / "bin/python").touch()
    (root / "Scripts/python.exe").touch()
    monkeypatch.setattr(
        cli, "profile_info", lambda _: ProfileInfo("cpu", root, True, root / "u", compatible=True)
    )
    settings = ProcessingSettings.model_validate(examples()["preset"]["settings"])
    policy = Policy.model_validate(
        examples()["policy"]
        | {
            "id": "p",
            "revision": 1,
            "mode": "auto",
            "fixed_candidate_id": None,
            "allowed_profile_ids": [],
        }
    )
    good = env.provider.resolve(settings, policy, revision=7)
    calls: list[list[str]] = []
    payload: dict[str, Any] = {
        "schema_version": 1,
        "reason_code": "supported",
        "selection": good.selection.to_wire() if good.selection else None,
        "worker_models": dict(good.worker_models),
    }

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload).encode(), b"")

    adapter = CapabilityInventoryAdapter(cli, runner=run)
    request = InventoryRefreshRequest(
        schema_version=1, profile_id="cpu", settings=settings, execution_path="worker", revision=7
    )
    result = adapter.resolve(request, policy)
    assert result.selection is not None and calls[0][-1] == "resolve"
    assert "initial_prompt" not in " ".join(calls[0])
    wrong = request.model_copy(update={"revision": 8})
    assert adapter.resolve(wrong, policy).reason_code == "profile_scope_mismatch"
    payload["worker_models"] = {"other-candidate": "/x"}
    assert adapter.resolve(request, policy).reason_code == "profile_scope_mismatch"
    payload["worker_models"] = {k: "relative/path" for k in good.worker_models}
    assert adapter.resolve(request, policy).reason_code == "profile_scope_mismatch"
    payload["worker_models"] = dict(good.worker_models)
    provider = BridgeResolutionProvider(adapter)
    assert provider.resolve(settings, policy, revision=7).selection is not None
    payload["selection"] = None
    payload["reason_code"] = "probe_error"
    assert provider.resolve(settings, policy, revision=7).selection is None
