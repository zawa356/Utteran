"""PR #48 P1: the B7 frozen snapshot reaches the B3 execution Config without mixing
the catalogue model ID and the local model directory.

Windows acceptance (fc71099) failed every v1 job with ``unsupported_setting`` before
inference: B7 sent the absolute model directory in ``context.asr_model`` and B3 compared
it with the preset's catalogue ID. These tests drive the *real* chain -- B7 admission
snapshot -> ``plan_request`` -> private IPC framing -> ``explicit_config`` ->
``offline_preflight`` (and, for one case, the full in-process B3 ``execute``) -- with
synthetic fixtures only. Only inference itself (SyntheticASR) and the faster_whisper
import probe are replaced; the ID/path conversion is never mocked.
"""

from __future__ import annotations

import io
import socket
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_application import SyntheticASR, normalize
from test_b7_api import AUTHORITY, runtime_for
from test_b7_jobs import SESSION, Env, FakeRunner
from utteran import application, worker_runtime
from utteran.config import Config
from utteran.jobs import stage_config_hashes
from utteran.types import CancelToken, PipelineOutcome
from utteran.worker_runtime import execute, explicit_config, offline_preflight
from utteran_contracts.worker import (
    REQUEST_LIMIT,
    WorkerCompleted,
    WorkerErrorCode,
    WorkerRequest,
)
from utteran_contracts.worker_framing import ProtocolError, encode_record, read_record
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.job_dispatcher import plan_request
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.worker_client import WorkerClientError, verify_result

ROOT = Path(__file__).parents[1]
MODEL_FILES = ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json")


def model_files(directory: Path, names: tuple[str, ...] = MODEL_FILES) -> None:
    for name in names:
        (directory / name).write_text("synthetic", encoding="utf-8")


def over_ipc(request: WorkerRequest) -> WorkerRequest:
    """The same framing the GUI client writes and the B3 worker reads."""
    return WorkerRequest.model_validate(
        read_record(io.BytesIO(encode_record(request, REQUEST_LIMIT)), REQUEST_LIMIT)
    )


def planned(env: Env, *paths: Path) -> tuple[Any, WorkerRequest]:
    job = env.submit(*(paths or (env.file("a.wav"),))).batch.jobs[0]
    snapshot = env.store.get_snapshot(job.id)
    request = plan_request(
        snapshot, job_id=job.id, attempt_id="att_" + "1" * 32, job_dir=env.tmp / "jobs"
    )
    return snapshot, over_ipc(request)


def with_context(request: WorkerRequest, **changes: Any) -> WorkerRequest:
    data = request.model_dump(mode="json")
    data["context"].update(changes)
    return WorkerRequest.model_validate(data)


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """No network; faster_whisper's import probe only; core preflight recorded."""

    # Loopback stays allowed: Windows asyncio builds its self-pipe with a 127.0.0.1
    # socketpair. Anything else (Hub, catalogue download, DNS name) is a failure.
    loopback = {"127.0.0.1", "::1"}
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def connect(sock: socket.socket, address: Any) -> None:
        if not (isinstance(address, tuple) and address[0] in loopback):
            raise AssertionError("network access attempted")
        real_connect(sock, address)

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        if address[0] not in loopback:
            raise AssertionError("network access attempted")
        return real_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    real = worker_runtime.importlib.util.find_spec
    monkeypatch.setattr(
        worker_runtime.importlib.util,
        "find_spec",
        lambda name, *a: object() if name == "faster_whisper" else real(name, *a),
    )
    monkeypatch.setattr(Config, "load", lambda **kw: pytest.fail("ambient config"))
    calls: list[Any] = []
    monkeypatch.setattr(application, "preflight_dependencies", lambda *a: calls.append(a))
    return calls


# --------------------------------------------------------------------------- P1 body


def test_b7_snapshot_reaches_b3_config_with_id_checked_and_path_loaded(
    tmp_path: Path, offline: list[Any]
) -> None:
    env = Env(tmp_path, None)
    model_files(env.model)
    snapshot, request = planned(env)
    spec = snapshot.resolved
    assert spec.settings.asr.model == "large-v3-turbo" == spec.model_id
    assert Path(spec.worker_model_dir).is_absolute()
    # The P1 regression: before the fix this raised unsupported_setting (context_conflict).
    config = explicit_config(request)
    # Two meanings, two fields: the ID is compared, the frozen local directory is loaded.
    assert request.context.asr_model == spec.model_id
    assert request.context.asr_model_dir == spec.worker_model_dir == str(env.model)
    assert config.asr.model == spec.worker_model_dir
    assert config.asr.backend == "faster-whisper" and config.asr.compute_type == "int8"
    offline_preflight(config)
    assert len(offline) == 1 and offline[0][0].asr.model == str(env.model)


def test_auto_preset_model_also_uses_the_frozen_snapshot(
    tmp_path: Path, offline: list[Any]
) -> None:
    env = Env(tmp_path, lambda s: s["asr"].update(model="auto"))
    model_files(env.model)
    snapshot, request = planned(env)
    assert snapshot.resolved.settings.asr.model == "auto"
    assert request.context.asr_model == "large-v3-turbo"
    config = explicit_config(request)
    assert config.asr.model == str(env.model)
    offline_preflight(config)


# --------------------------------------------------------------------------- refusals


def _code(excinfo: pytest.ExceptionInfo[ProtocolError]) -> str:
    return excinfo.value.code.value


@pytest.mark.parametrize(
    "change,expected",
    [
        # Preset/snapshot catalogue ID disagreement (adapter context_conflict).
        ({"asr_model": "large-v3"}, "unsupported_setting"),
        # Unresolved or non-ID values in the ID field.
        ({"asr_model": "auto"}, "invalid_context"),
        ({"asr_model": "PATH"}, "invalid_context"),
        ({"asr_model": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}, "invalid_context"),
        ({"asr_model": "not-a-catalogue-id"}, "invalid_context"),
        # The directory must be an explicit absolute local path (no Hub ID / relative).
        ({"asr_model_dir": "relative/model"}, "invalid_context"),
        ({"asr_model_dir": "large-v3-turbo"}, "invalid_context"),
        ({"asr_model_dir": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}, "invalid_context"),
        ({"asr_model_dir": " PATH"}, "invalid_context"),
        ({"asr_model_dir": "PATH/../model"}, "invalid_context"),
    ],
)
def test_id_and_directory_refusals_are_fixed_codes(
    tmp_path: Path, offline: list[Any], change: dict[str, str], expected: str
) -> None:
    env = Env(tmp_path, None)
    model_files(env.model)
    _, request = planned(env)
    change = {k: v.replace("PATH", str(env.model)) for k, v in change.items()}
    with pytest.raises(ProtocolError) as caught:
        explicit_config(with_context(request, **change))
    assert _code(caught) == expected
    assert str(env.model) not in str(caught.value) and offline == []


@pytest.mark.parametrize("value", ["", None])
def test_empty_or_missing_directory_is_refused_by_the_private_schema(
    tmp_path: Path, value: str | None
) -> None:
    env = Env(tmp_path, None)
    _, request = planned(env)
    data = request.model_dump(mode="json")
    if value is None:
        del data["context"]["asr_model_dir"]
    else:
        data["context"]["asr_model_dir"] = value
    with pytest.raises(ValueError):
        WorkerRequest.model_validate(data)


@pytest.mark.parametrize("missing", ["directory", *MODEL_FILES])
def test_missing_local_model_is_model_missing_before_core_preflight(
    tmp_path: Path, offline: list[Any], missing: str
) -> None:
    env = Env(tmp_path, None)
    if missing == "directory":
        env.model.rmdir()
    else:
        model_files(env.model, tuple(n for n in MODEL_FILES if n != missing))
    _, request = planned(env)
    config = explicit_config(request)
    with pytest.raises(ProtocolError) as caught:
        offline_preflight(config)
    assert _code(caught) == "model_missing" and offline == []


def test_directory_that_is_a_file_is_model_missing(tmp_path: Path, offline: list[Any]) -> None:
    env = Env(tmp_path, None)
    _, request = planned(env)
    target = env.tmp / "not-a-dir"
    target.write_text("synthetic", encoding="utf-8")
    config = explicit_config(with_context(request, asr_model_dir=str(target)))
    with pytest.raises(ProtocolError, match="model_missing"):
        offline_preflight(config)


# --------------------------------------------------------------------------- full B3 run


class B3Runner(FakeRunner):
    """Spawns a real child for launch journaling, then runs the real B3 ``execute``
    in-process on the request decoded from the same framing the worker reads."""

    def __init__(self, env: Env, backend: SyntheticASR) -> None:
        super().__init__(env)
        self.backend = backend
        self.codes: list[WorkerErrorCode] = []

    def execute(
        self, request: WorkerRequest, *, cancel=None, on_event=None, on_spawn=None
    ) -> WorkerCompleted:
        self.requests.append(request)
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        self.children.append(child)
        if on_spawn is not None:
            on_spawn(child)
        child.wait()
        decoded = over_ipc(request)
        try:
            result = execute(decoded, CancelToken(), lambda *a, **kw: None)
        except Exception as exc:  # the worker's own error mapping
            code = worker_runtime.error_code(exc)
            self.codes.append(code)
            raise WorkerClientError(code) from None
        done = WorkerCompleted(
            sequence=3,
            request_id=request.request_id,
            job_id=request.job_id,
            attempt_id=request.attempt_id,
            **result,
        )
        verify_result(request, done)
        return done


def _synthetic_core(monkeypatch: pytest.MonkeyPatch, backend: SyntheticASR) -> list[str]:
    monkeypatch.setattr("utteran.pipeline.normalize_audio", normalize)
    loaded: list[str] = []
    original = application.execute_single

    def runner(*args: Any, **kwargs: Any) -> PipelineOutcome:
        loaded.append(args[1].asr.model)
        return original(*args, **kwargs, asr_backend=backend)

    monkeypatch.setattr(application, "execute_single", runner)
    return loaded


def test_api_vertical_preset_to_succeeded_job_with_three_artifacts(
    tmp_path: Path, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real FastAPI: B5 preset/validation -> B7 resolution -> accept -> queue resume ->
    dispatcher -> WorkerRequest -> B3 conversion/preflight/execute -> succeeded."""
    env = Env(tmp_path, None)
    model_files(env.model)
    backend = SyntheticASR()
    loaded = _synthetic_core(monkeypatch, backend)
    queue = OperationQueue()
    runner = B3Runner(env, backend)
    runtime = runtime_for(env, queue, runner)
    app = create_app(
        SESSION,
        repo_root=ROOT,
        operation_queue=queue,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=env.settings_path,
        enable_v1_jobs=True,
        v1_jobs_runtime=runtime,
    )

    def post(c: TestClient, path: str, body: Any, key: str, **extra: str) -> Any:
        return c.post(path, json=body, headers={"Idempotency-Key": key, **extra})

    with TestClient(app, base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}) as c:
        preset = c.get(
            f"/api/v1/presets/{env.preset_ref['id']}?revision={env.preset_ref['revision']}"
        ).json()
        settings = preset["settings"]
        assert settings["asr"]["model"] == "large-v3-turbo"
        session = {
            "schema_version": 1,
            "preset_ref": env.preset_ref,
            "policy_ref": env.policy_ref,
            "common_overrides": [{"path": "/output/formats", "value": ["srt", "json", "md"]}],
            "files": [
                {
                    "client_file_id": "f-0123456789abcdef",
                    "input_path": str(env.file("a.wav")),
                    "output_dir": str(env.output),
                    "overrides": [],
                }
            ],
        }
        assert post(c, "/api/v1/validations", session, "p1-validate-000001").status_code == 200
        resolved = post(c, "/api/v1/execution-resolutions", session, "p1-resolve-0000001")
        assert resolved.status_code == 201, resolved.text
        body = {
            "schema_version": 1,
            "resolution_token": resolved.json()["token"],
            "editing_session": session,
            "resume_mode": "resume",
        }
        accepted = post(c, "/api/v1/jobs", body, "p1-submit-00000001")
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["jobs"][0]["id"]
        queue_view = c.get("/api/v1/queue")
        assert queue_view.json()["dispatch"] == "paused"
        resumed = c.patch(
            "/api/v1/queue/control",
            json={"schema_version": 1, "dispatch": "running"},
            headers={
                "Idempotency-Key": "p1-resume-00000001",
                "If-Match": f'"{queue_view.json()["revision"]}"',
            },
        )
        assert resumed.status_code == 200
        assert runtime.dispatcher is not None and runtime.dispatcher.run_once()
        job = c.get(f"/api/v1/jobs/{job_id}")
        public = job.text + c.get("/api/v1/jobs").text + c.get("/api/v1/queue").text

    assert job.json()["status"] == "succeeded", (job.json(), runner.codes)
    assert job.json()["result"]["artifact_count"] == 3
    request = runner.requests[0]
    assert request.context.asr_model == "large-v3-turbo"
    assert request.context.asr_model_dir == str(env.model)
    assert loaded == [str(env.model)] and backend.calls == 1 and len(offline) == 1
    produced = sorted(p.suffix for p in env.output.iterdir() if p.is_file())
    assert produced == [".json", ".md", ".srt"]
    # The private directory never reaches the public v1 views.
    assert str(env.model) not in public and str(env.tmp) not in public
    assert "asr_model_dir" not in public


def test_dispatcher_failure_is_unsupported_setting_only_for_a_real_id_conflict(
    tmp_path: Path, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snapshot whose frozen ID disagrees with the preset ID still fails closed."""
    env = Env(tmp_path, None)
    model_files(env.model)
    backend = SyntheticASR()
    _synthetic_core(monkeypatch, backend)
    _, request = planned(env)
    bad = with_context(request, asr_model="large-v3")
    runner = B3Runner(env, backend)
    with pytest.raises(WorkerClientError):
        runner.execute(bad, cancel=threading.Event())
    assert runner.codes == [WorkerErrorCode.UNSUPPORTED_SETTING] and backend.calls == 0


# --------------------------------------------------------------------------- hashes/resume


def test_same_snapshot_retries_get_the_same_execution_config_and_stage_hashes(
    tmp_path: Path,
) -> None:
    """Resume keys on the executed Config: every attempt of one frozen snapshot must
    produce the same asr.model (the frozen directory) and the same stage hashes."""
    env = Env(tmp_path, None)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    snapshot = env.store.get_snapshot(job.id)
    configs = [
        explicit_config(
            over_ipc(
                plan_request(
                    snapshot, job_id=job.id, attempt_id="att_" + n * 32, job_dir=env.tmp / "jobs"
                )
            )
        )
        for n in ("1", "2")
    ]
    first, second = (stage_config_hashes(c, "synthetic") for c in configs)
    assert first == second and configs[0] == configs[1]
    assert configs[0].asr.model == snapshot.resolved.worker_model_dir
    # A different local directory is a different execution identity (B7 blocks it as
    # candidate_changed before dispatch; B3 never reuses the other directory's results).
    request = over_ipc(
        plan_request(snapshot, job_id=job.id, attempt_id="att_" + "3" * 32, job_dir=env.tmp)
    )
    moved = explicit_config(with_context(request, asr_model_dir=str(env.tmp / "elsewhere")))
    assert stage_config_hashes(moved, "synthetic")["asr"] != first["asr"]


def test_offline_guard_refuses_non_loopback_connections(offline: list[Any]) -> None:
    """The guard used above must really refuse remote hosts (TEST-NET-1, never routed)."""
    with socket.socket() as sock, pytest.raises(AssertionError, match="network access"):
        sock.connect(("192.0.2.1", 443))
    with pytest.raises(AssertionError, match="network access"):
        socket.create_connection(("huggingface.co", 443))
