from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import sysconfig
import threading
import venv
from pathlib import Path
from typing import Any

import pytest

from utteran_contracts.worker import WorkerErrorCode as Code
from utteran_contracts.worker import WorkerRequest
from utteran_contracts.worker_framing import ProtocolError
from utteran_gui.cli import PROFILE_EXTRAS, PROFILE_MANIFEST
from utteran_gui.worker_client import WorkerClient

ROOT = Path(__file__).parents[1]
STUB = ROOT / "tests/fixtures/b3/worker_stub.py"


def peer(monkeypatch: pytest.MonkeyPatch, mode: str, *args: str) -> WorkerClient:
    client = WorkerClient(ROOT)
    monkeypatch.setattr(client, "command", lambda _: [sys.executable, "-u", str(STUB), mode, *args])
    return client


@pytest.mark.parametrize(
    "mode,code",
    [
        ("missing", Code.MODULE_MISSING),
        ("hang", Code.HANDSHAKE_TIMEOUT),
        ("version", Code.PROTOCOL_MISMATCH),
        ("unknown", Code.MALFORMED_MESSAGE),
        ("oversized", Code.OVERSIZED_MESSAGE),
        ("invalid_utf8", Code.MALFORMED_MESSAGE),
        ("malformed", Code.MALFORMED_MESSAGE),
        ("empty", Code.UNEXPECTED_EOF),
        ("crash", Code.WORKER_CRASH),
        ("wrong_id", Code.ID_MISMATCH),
        ("duplicate", Code.INVALID_SEQUENCE),
        ("flood", Code.CORE_FAILURE),
        ("error", Code.CORE_FAILURE),
        ("zero_error", Code.INVALID_SEQUENCE),
        ("success_crash", Code.WORKER_CRASH),
        ("wrong_formats", Code.ARTIFACT_INVALID),
        ("terminal_hang", Code.IO_TIMEOUT),
    ],
)
def test_subprocess_protocol_faults(
    worker_request: WorkerRequest,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    code: Code,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = peer(monkeypatch, mode)
    with pytest.raises(ProtocolError) as caught:
        client.execute(worker_request, handshake_timeout=0.5, io_timeout=0.5)
    assert caught.value.code == code
    assert "PRIVATE_PROMPT" not in str(caught.value) + caplog.text


def test_success_exit_and_private_artifact_verification(
    worker_request: WorkerRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = peer(monkeypatch, "success")
    events: list[Any] = []
    result = client.execute(worker_request, on_event=events.append)
    assert result.core_job_id == "0123456789abcdef"
    assert "path" not in str(events) and "PRIVATE_PROMPT" not in str(events)


@pytest.mark.parametrize(
    "mode,deadline,code",
    [
        ("cancel", None, Code.CANCELLED),
        ("cancel_crash", None, Code.WORKER_CRASH),
        ("cancel", 0.1, Code.PROCESS_TIMEOUT),
        ("validate_hang", None, Code.VALIDATION_TIMEOUT),
    ],
)
def test_cancel_ack_is_not_stop_evidence(
    worker_request: WorkerRequest,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    deadline: float | None,
    code: Code,
) -> None:
    client = peer(monkeypatch, mode)
    cancel = threading.Event()
    if deadline is None and mode != "validate_hang":
        cancel.set()
    with pytest.raises(ProtocolError) as caught:
        client.execute(
            worker_request,
            cancel=cancel,
            deadline_seconds=deadline,
            validation_timeout=0.1 if mode == "validate_hang" else 2,
        )
    assert caught.value.code == code


def test_profile_checks_and_no_manifest_mutation(tmp_path: Path) -> None:
    client = WorkerClient(ROOT, tmp_path)
    with pytest.raises(ProtocolError, match="profile_invalid"):
        client.command("../../python")
    with pytest.raises(ProtocolError, match="profile_unavailable"):
        client.command("cpu")
    info = client.adapter.profile_info("cpu")
    info.executable.parent.mkdir(parents=True)
    info.executable.touch()
    python = info.path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.touch()
    manifest = info.path / PROFILE_MANIFEST
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ProtocolError, match="profile_incompatible"):
        client.command("cpu")
    assert manifest.read_text(encoding="utf-8") == "{}"
    manifest.write_text(
        json.dumps(
            dict(
                schema_version=1,
                lock_sha256=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
                extras=list(PROFILE_EXTRAS["cpu"]),
                venv_path=str(info.path),
            )
        ),
        encoding="utf-8",
    )
    command = client.command("cpu")
    assert command == [str(python), "-I", "-m", "utteran.worker", "--protocol", "1"]


def test_actual_profile_python_runs_worker(worker_request: WorkerRequest, tmp_path: Path) -> None:
    client = WorkerClient(ROOT, tmp_path / "venvs")
    profile = client.adapter.profile_info("cpu").path
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(profile)
    site = profile / (
        "Lib/site-packages"
        if os.name == "nt"
        else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    (site / "test-dependencies.pth").write_text(
        str(sysconfig.get_path("purelib")) + "\n" + str(ROOT / "src") + "\n", encoding="utf-8"
    )
    info = client.adapter.profile_info("cpu")
    info.executable.touch()
    (profile / PROFILE_MANIFEST).write_text(
        json.dumps(
            dict(
                schema_version=1,
                lock_sha256=hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
                extras=list(PROFILE_EXTRAS["cpu"]),
                venv_path=str(profile),
            )
        ),
        encoding="utf-8",
    )
    Path(worker_request.input_path).write_bytes(b"synthetic")
    with pytest.raises(ProtocolError, match="model_missing"):
        client.execute(worker_request)


def test_start_failure(worker_request: WorkerRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    client = peer(monkeypatch, "success")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("PRIVATE_PROMPT")

    monkeypatch.setattr("utteran_gui.worker_client.ProcessSupervisor.popen", fail)
    with pytest.raises(ProtocolError, match="start_failed"):
        client.execute(worker_request)


@pytest.mark.skipif(os.name == "nt", reason="POSIX session cleanup; Windows uses Job Object")
@pytest.mark.parametrize("mode", ["tree", "orphan"])
def test_handshake_timeout_cleans_descendants(
    worker_request: WorkerRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    pidfile = tmp_path / "child.pid"
    client = peer(monkeypatch, mode, str(pidfile))
    with pytest.raises(ProtocolError, match="handshake_timeout"):
        client.execute(worker_request, handshake_timeout=0.5)
    pid = int(pidfile.read_text(encoding="utf-8"))
    # Linux containers can retain a killed orphan zombie; it is not a live child.
    stat = Path(f"/proc/{pid}/stat")
    assert not stat.exists() or stat.read_text(encoding="utf-8").split()[2] == "Z"


def test_request_secrets_only_on_stdin(
    worker_request: WorkerRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = peer(monkeypatch, "success")
    from utteran_gui.processes import ProcessSupervisor

    original = ProcessSupervisor.popen
    captured: list[Any] = []

    def popen(self: ProcessSupervisor, command: list[str], **kwargs: Any) -> Any:
        captured.append((command, kwargs))
        return original(self, command, **kwargs)

    monkeypatch.setattr(ProcessSupervisor, "popen", popen)
    client.execute(worker_request)
    command, kwargs = captured[0]
    assert "PRIVATE_PROMPT" not in str(command)
    assert worker_request.input_path not in str(command)
    assert kwargs["shell"] is False and kwargs["stdin"] == subprocess.PIPE
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] == subprocess.PIPE


def core_peer(monkeypatch: pytest.MonkeyPatch, mode: str) -> WorkerClient:
    client = WorkerClient(ROOT)
    script = ROOT / "tests/fixtures/b3/core_worker.py"
    monkeypatch.setattr(client, "command", lambda _: [sys.executable, str(script), mode])
    return client


def test_actual_worker_core_pipeline_and_protocol_isolation(
    worker_request: WorkerRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UTTERAN_PROFILE", "cpu")
    Path(worker_request.input_path).write_bytes(b"synthetic")
    # BaseSettings.model_validate itself reads env; the real worker boundary
    # must remove config-section env, including malformed JSON, before adapting.
    monkeypatch.setenv("UTTERAN_GENERAL__RAW_SUBPROCESS_LOGS", "true")
    monkeypatch.setenv("UTTERAN_OUTPUT__FORMATS", "not-json")
    monkeypatch.setenv("utteran_asr", "not-json")
    client = core_peer(monkeypatch, "success")
    events: list[Any] = []
    first = client.execute(worker_request, on_event=events.append)
    second = client.execute(worker_request)
    assert first.core_job_id == second.core_job_id
    assert first.artifacts == second.artifacts
    assert events[0]["type"] == "accepted" and events[-1]["type"] == "completed"
    assert any(e.get("stage") == "asr" for e in events)
    assert "PRIVATE_PROMPT" not in str(events)
    manifest = Path(second.manifest.path)
    assert not (manifest.parent / ".lock").exists()


@pytest.mark.parametrize("mode,code", [("error", Code.CORE_FAILURE), ("cancel", Code.CANCELLED)])
def test_actual_worker_core_failure_and_cooperative_cleanup(
    worker_request: WorkerRequest,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    code: Code,
) -> None:
    monkeypatch.setenv("UTTERAN_PROFILE", "cpu")
    Path(worker_request.input_path).write_bytes(b"synthetic")
    client = core_peer(monkeypatch, mode)
    cancel = threading.Event()

    def progress(event: dict[str, Any]) -> None:
        if mode == "cancel" and event.get("stage") == "asr":
            cancel.set()

    with pytest.raises(ProtocolError) as caught:
        client.execute(worker_request, cancel=cancel, on_event=progress)
    assert caught.value.code == code
    jobs = Path(worker_request.context.base_config["general"]["job_dir"])
    assert not list(jobs.rglob(".lock"))


@pytest.mark.parametrize(
    "raw,code",
    [
        (b'{"schema_version":1,"protocol_version":2}\n', Code.PROTOCOL_MISMATCH),
        (b'{"schema_version":2,"protocol_version":1}\n', Code.SCHEMA_MISMATCH),
        (b"\xff\n", Code.MALFORMED_MESSAGE),
        (b"{bad}\n", Code.MALFORMED_MESSAGE),
    ],
)
def test_actual_worker_rejects_malformed_input(raw: bytes, code: Code) -> None:
    environment = dict(os.environ, UTTERAN_PROFILE="cpu")
    result = subprocess.run(
        [sys.executable, "-m", "utteran.worker", "--protocol", "1"],
        input=raw,
        capture_output=True,
        env=environment,
        timeout=10,
    )
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["type"] == "hello"
    assert lines[-1]["type"] == "error" and lines[-1]["code"] == code
    assert result.returncode != 0 and result.stderr == b""


def test_blocked_stdin_write_is_bounded(
    worker_request: WorkerRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = worker_request.model_dump(mode="json")
    data["context"]["base_config"]["padding"] = "x" * (2 * 1024 * 1024)
    request = WorkerRequest.model_validate(data)
    client = peer(monkeypatch, "no_read")
    with pytest.raises(ProtocolError, match="io_timeout"):
        client.execute(request, io_timeout=0.2)


def test_completion_callback_waits_for_exit_validation(
    worker_request: WorkerRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []
    client = peer(monkeypatch, "success_crash")
    with pytest.raises(ProtocolError, match="worker_crash"):
        client.execute(worker_request, on_event=events.append)
    assert all(e["type"] != "completed" for e in events)


def test_external_kill_is_not_cancellation(
    worker_request: WorkerRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from utteran_gui.processes import ProcessSupervisor

    original = ProcessSupervisor.popen
    children: list[Any] = []

    def popen(self: ProcessSupervisor, command: list[str], **kwargs: Any) -> Any:
        process = original(self, command, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(ProcessSupervisor, "popen", popen)
    client = peer(monkeypatch, "cancel")

    def progress(event: dict[str, Any]) -> None:
        if event["type"] == "accepted":
            children[0].kill()

    with pytest.raises(ProtocolError, match="worker_crash"):
        client.execute(worker_request, on_event=progress)
    assert children[0].poll() is not None


@pytest.mark.parametrize(
    "corruption,code",
    [
        ("json", Code.MALFORMED_MESSAGE),
        ("utf8", Code.MALFORMED_MESSAGE),
        ("schema", Code.SCHEMA_MISMATCH),
        ("protocol", Code.PROTOCOL_MISMATCH),
        ("settings_schema", Code.MALFORMED_MESSAGE),
        ("unknown", Code.MALFORMED_MESSAGE),
    ],
)
def test_actual_worker_client_preserves_pre_accepted_rejection(
    worker_request: WorkerRequest,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    corruption: str,
    code: Code,
) -> None:
    from utteran_contracts.worker_framing import encode_record

    client = WorkerClient(ROOT)
    monkeypatch.setattr(
        client,
        "command",
        lambda _: [sys.executable, "-I", "-m", "utteran.worker", "--protocol", "1"],
    )
    private = "PRIVATE_PROMPT_秘密 /private/input hf_synthetic123"
    data = worker_request.model_dump(mode="json")
    if corruption in {"schema", "protocol"}:
        data[corruption + "_version"] = 2
    elif corruption == "settings_schema":
        data["settings"]["schema_version"] = 2
    elif corruption == "unknown":
        data["unknown"] = private
    frame = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
    if corruption == "json":
        frame = ('{"invalid":' + private + "\n").encode("utf-8")
    elif corruption == "utf8":
        frame = b"\xff\n"

    def corrupt_request(value: Any, limit: int) -> bytes:
        return frame if isinstance(value, WorkerRequest) else encode_record(value, limit)

    monkeypatch.setattr("utteran_gui.worker_client.encode_record", corrupt_request)
    events: list[Any] = []
    from utteran_gui.worker_client import WorkerClientError

    with pytest.raises(WorkerClientError) as caught:
        client.execute(worker_request, on_event=events.append)
    assert caught.value.code == code
    assert [event["type"] for event in events] == ["error"]
    assert events[0]["sequence"] == 1 and events[0]["code"] == code
    assert "PRIVATE_PROMPT" not in str(caught.value) + str(events) + caplog.text
    assert worker_request.input_path not in str(caught.value) + str(events) + caplog.text
