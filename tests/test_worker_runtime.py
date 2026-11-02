from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from test_application import SyntheticASR, normalize
from utteran import application
from utteran.config import Config
from utteran.errors import CancelledError
from utteran.jobs import stage_config_hashes
from utteran.types import CancelToken, PipelineOutcome
from utteran.worker_runtime import execute, explicit_config, offline_preflight
from utteran_contracts.worker import WorkerCompleted, WorkerRequest
from utteran_contracts.worker_framing import ProtocolError
from utteran_gui.worker_client import verify_result


def change(request: WorkerRequest, **changes: Any) -> WorkerRequest:
    return WorkerRequest.model_validate({**request.model_dump(mode="json"), **changes})


def test_real_pipeline_adapter_resume_force_and_artifact_compatibility(
    worker_request: WorkerRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    req = worker_request
    Path(req.input_path).write_bytes(b"synthetic")
    monkeypatch.setattr("utteran.pipeline.normalize_audio", normalize)
    monkeypatch.setattr(Config, "load", lambda **kw: pytest.fail("ambient config"))
    monkeypatch.setattr("utteran.worker_runtime.offline_preflight", lambda cfg: None)
    monkeypatch.setenv("UTTERAN_ASR__MODEL", "must-not-apply")
    backend = SyntheticASR()
    original = application.execute_single

    def runner(*args: Any, **kwargs: Any) -> PipelineOutcome:
        return original(*args, **kwargs, asr_backend=backend)

    monkeypatch.setattr(application, "execute_single", runner)
    events: list[tuple[str, dict[str, Any]]] = []

    def emit(kind: str, **payload: Any) -> None:
        events.append((kind, payload))

    first = execute(req, CancelToken(), emit)
    assert backend.calls == 1
    assert backend.prompts == [req.settings.asr.initial_prompt]
    assert events[0][1]["state"] == "validated"
    assert "PRIVATE_PROMPT" not in str(events)
    done = WorkerCompleted(
        sequence=2, request_id=req.request_id, job_id=req.job_id, attempt_id=req.attempt_id, **first
    )
    verify_result(req, done)
    before = {a.path: Path(a.path).read_bytes() for a in done.artifacts}
    second = execute(req, CancelToken(), emit)
    assert backend.calls == 1
    assert first["core_job_id"] == second["core_job_id"]
    assert first["artifacts"] == second["artifacts"]
    cfg = explicit_config(req)
    legacy = Config.model_validate(cfg.model_dump())
    assert stage_config_hashes(cfg, "synthetic") == stage_config_hashes(legacy, "synthetic")
    legacy_result = original(Path(req.input_path), cfg, asr_backend=backend)
    assert legacy_result.job_id == first["core_job_id"]
    assert before == {str(p): p.read_bytes() for p in legacy_result.output_paths}
    execute(change(req, force=True), CancelToken(), emit)
    assert backend.calls == 2
    execute(change(req, resume=False), CancelToken(), emit)
    assert backend.calls == 3
    Path(done.artifacts[0].path).write_text("tampered")
    with pytest.raises(ProtocolError, match="artifact_invalid"):
        verify_result(req, done)


@pytest.mark.parametrize(
    "section,extra",
    [
        ("general", {"unknown": "PRIVATE_PROMPT"}),
        ("asr", {"unknown": 1}),
        ("asr", {"whisper_cpp": {"unknown": 1}}),
        ("ffmpeg", {"path": "/tmp/arbitrary-command"}),
        ("general", {"raw_subprocess_logs": True}),
        ("general", {"native_dir": "/tmp/native"}),
        ("general", {"venv_dir": "/tmp/venv"}),
        ("general", {"output_dir": "relative"}),
    ],
)
def test_context_unknown_keys_and_execution_paths_are_rejected(
    worker_request: WorkerRequest,
    section: str,
    extra: dict[str, Any],
) -> None:
    data = worker_request.model_dump(mode="json")
    base = data["context"]["base_config"]
    base[section] = {**base.get(section, {}), **extra}
    with pytest.raises(ProtocolError) as caught:
        explicit_config(WorkerRequest.model_validate(data))
    assert "PRIVATE_PROMPT" not in str(caught.value)


def test_model_and_download_policy(worker_request: WorkerRequest, tmp_path: Path) -> None:
    cfg = explicit_config(worker_request)
    with pytest.raises(ProtocolError, match="model_missing"):
        offline_preflight(cfg)
    cfg.diarization.enabled = True
    with pytest.raises(ProtocolError, match="unsupported_setting"):
        offline_preflight(cfg)
    cfg.diarization.enabled = False
    cfg.asr.backend = "whisper-cpp"
    with pytest.raises(ProtocolError, match="unsupported_setting"):
        offline_preflight(cfg)


def test_local_tokenizer_required_before_core_preflight(
    worker_request: WorkerRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    for name in ("model.bin", "config.json", "preprocessor_config.json"):
        (model / name).write_text("synthetic")
    cfg = explicit_config(worker_request)
    cfg.asr.model = str(model)
    monkeypatch.setattr(application, "preflight_dependencies", lambda *a: pytest.fail("too early"))
    with pytest.raises(ProtocolError, match="model_missing"):
        offline_preflight(cfg)
    (model / "tokenizer.json").write_text("synthetic")
    calls: list[Any] = []
    monkeypatch.setattr(application, "preflight_dependencies", lambda *a: calls.append(a))
    offline_preflight(cfg)
    assert len(calls) == 1


def test_cancel_at_safe_boundary(worker_request: WorkerRequest) -> None:
    token = CancelToken()
    token.cancel()
    with pytest.raises(CancelledError):
        execute(worker_request, token, lambda *a, **kw: None)
