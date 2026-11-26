from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from test_b8_setup import World
from test_f_setup import asr_settings
from utteran.asr.whisper_cpp import _private_route, _run_process
from utteran.diarization import community
from utteran.errors import BackendUnavailableError, ModelNotFoundError
from utteran.pipeline import BackendPool
from utteran_contracts import ProcessingSettings
from utteran_contracts.setup import SetupPlanRequest
from utteran_gui.setup_planner import build_plan


def test_native_private_pipe_excludes_arguments_and_stderr_values(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "utteran.capabilities.fingerprints.windows_display_adapter",
        lambda: ("Synthetic GPU", "PCI\\VEN_8086", "0"),
    )
    real_popen = subprocess.Popen
    transcript = "synthetic line, never a diagnostic"
    prompt = "複数行\nprivate prompt"
    capture = tmp_path / "capture.bin"

    def launch(command, **kwargs):
        assert command == [sys.executable, "--utteran-stdin"]
        assert kwargs["cwd"] == tmp_path
        script = (
            "import sys,pathlib; "
            "pathlib.Path('capture.bin').write_bytes(sys.stdin.buffer.read()); "
            "print('utteran encoder Vulkan0', file=sys.stderr); "
            f"print({transcript!r}, file=sys.stderr)"
        )
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr("utteran.asr.whisper_cpp.subprocess.Popen", launch)
    result = _run_process(
        [sys.executable, "--prompt", prompt, "-f", "audio.wav"],
        "vulkan",
        None,
        None,
        private_cwd=tmp_path,
    )
    assert capture.read_bytes().decode().split("\0") == [
        "--prompt",
        prompt,
        "-f",
        "audio.wav",
        "",
    ]
    assert result == "utteran encoder Vulkan0\n"


def test_native_route_attests_both_components_and_single_device(monkeypatch):
    monkeypatch.setattr(
        "utteran.capabilities.fingerprints.windows_display_adapter",
        lambda: ("Synthetic GPU", "PCI\\VEN_8086", "0"),
    )
    route = (
        "utteran gpu count 1\nutteran device Vulkan0|Synthetic GPU\n"
        "utteran encoder OpenVINO-GPU\nutteran decoder Vulkan0\n"
    )
    assert _private_route(route, "openvino_vulkan") == ("openvino-gpu", "vulkan")
    for changed in (
        route.replace("count 1", "count 2"),
        route.replace("decoder Vulkan0", "decoder CPU"),
        route.replace("Vulkan0|Synthetic GPU", "Vulkan0|Other GPU"),
        route + "utteran decoder unknown\n",
    ):
        with pytest.raises(BackendUnavailableError):
            _private_route(changed, "openvino_vulkan")


def test_offline_pinned_assets_reject_missing_and_modified_components(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_bytes(b"untrusted configuration")
    with pytest.raises(ModelNotFoundError):
        community.verify_assets(tmp_path)
    monkeypatch.setattr(community, "_configuration", lambda _root: {})
    component = b"synthetic component"
    name = "embedding/pytorch_model.bin"
    monkeypatch.setattr(
        community, "ASSETS", {name: (len(component), hashlib.sha256(component).hexdigest())}
    )
    with pytest.raises(ModelNotFoundError):
        community.verify_assets(tmp_path)
    file = tmp_path / name
    file.parent.mkdir()
    file.write_bytes(component)
    community.verify_assets(tmp_path)
    file.write_bytes(component.upper())
    with pytest.raises(ModelNotFoundError):
        community.verify_assets(tmp_path)


def test_sequential_release_precedes_diarization_memory_guard(monkeypatch):
    from utteran.config import Config, EnvironmentTokenProvider
    from utteran.pipeline import _diarize_with_memory_guard

    calls = []
    asr = type("SyntheticASR", (), {"unload": lambda _self: calls.append("unload")})()
    pool = BackendPool(EnvironmentTokenProvider(), asr_backend=asr, sequential_models=True)

    def memory(**_kwargs):
        assert calls == ["unload"]
        raise StopIteration

    monkeypatch.setattr("utteran.pipeline._wav_minutes", lambda _: 0.1)
    monkeypatch.setattr("utteran.pipeline.plan_diarization_memory", memory)
    config = Config.model_validate({"diarization": {"device": "cpu"}})
    with pytest.raises(StopIteration):
        _diarize_with_memory_guard(pool, config, Path("synthetic.wav"), None, None, None)


@pytest.mark.parametrize(
    ("variant", "recipe"),
    [
        ("vulkan", "wc-vulkan"),
        ("openvino_vulkan", "wc-ov-vk"),
        ("openvino", "wc-ov"),
    ],
)
def test_intel_setup_binds_native_ir_and_offline_diarization(tmp_path, variant, recipe):
    world = World(tmp_path)
    world.install("intel")
    data = asr_settings().to_wire()
    data["diarization"].update(enabled=True, device_preference="xpu")
    data["asr"].update(backend="whisper-cpp", model="base", whisper_cpp={"variant": variant})
    data["asr"].pop("faster_whisper", None)
    request = SetupPlanRequest(
        schema_version=1,
        profile_kind="intel",
        diarization_enabled=True,
        model_refs=(
            "whisper-cpp:base",
            "whisper-cpp-vad:silero-v6.2.0",
            "pyannote:pyannote/speaker-diarization-community-1",
        ),
        verification_settings=ProcessingSettings.model_validate(data),
    )
    plan = build_plan(request, world.observer.observe())
    assert plan.verification is not None and plan.verification.recipe_id == recipe
    steps = {step.kind: step for step in plan.steps}
    assert steps["native_build"].target == variant
    assert "native" in steps["capability_smoke"].depends_on
    assert ("ir_build" in steps) == ("openvino" in variant)
    assert "smoke" not in steps


def test_worker_writes_speaker_labels_through_existing_export_and_resume(
    worker_request, tmp_path, monkeypatch
):
    import json

    from test_application import SyntheticASR, normalize
    from utteran.memory import MemoryReadings
    from utteran.models.catalog import get_model
    from utteran.models.manager import ModelManager
    from utteran.types import CancelToken, DiarizationResult, SpeakerTurn
    from utteran.worker_runtime import execute
    from utteran_contracts.worker import WorkerRequest
    from utteran_paths import resolve_data_paths

    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(tmp_path / "managed"))
    model = ModelManager(resolve_data_paths().models).managed_path(get_model(community.MODEL))
    model.mkdir(parents=True)
    phases = []

    class ASR(SyntheticASR):
        def unload(self):
            phases.append("asr-released")

    class Diarization:
        name = "pyannote"

        def __init__(self, *, offline):
            assert offline

        def load(self, path, device):
            assert phases == ["asr-released"]
            assert (path, device) == (str(model), "cpu")
            phases.append("diarization-loaded")

        def diarize(self, *args):
            turns = [SpeakerTurn(0, 1, "SPEAKER_00")]
            return DiarizationResult(turns, turns, 1, self.name, community.MODEL, "cpu")

        def unload(self):
            pass

    asr = ASR()
    monkeypatch.setattr("utteran.worker_runtime.create_asr_backend", lambda *a, **kw: asr)
    monkeypatch.setattr("utteran.worker_runtime.offline_preflight", lambda _config: None)
    monkeypatch.setattr("utteran.pipeline.normalize_audio", normalize)
    # The backend is synthetic: host RAM pressure must not decide this export test.
    # Keep the real memory guard, with a deterministic ample observation.
    monkeypatch.setattr(
        "utteran.memory.read_memory",
        lambda _device: MemoryReadings(system_available_bytes=16 * 1024**3),
    )
    monkeypatch.setattr("utteran.diarization.pyannote.PyannoteBackend", Diarization)
    payload = worker_request.model_dump(mode="json")
    payload["settings"]["diarization"].update(enabled=True, model=community.MODEL)
    payload["settings"]["output"].update(
        formats=["srt", "vtt", "txt", "md", "json"],
        show_speaker=True,
        speaker_labels={"SPEAKER_00": "Synthetic speaker"},
    )
    request = WorkerRequest.model_validate(payload)
    Path(request.input_path).write_bytes(b"synthetic")
    first = execute(request, CancelToken(), lambda *a, **kw: None)
    assert phases == ["asr-released", "diarization-loaded"]
    files = {Path(record.path).suffix: Path(record.path) for record in first["artifacts"]}
    assert set(files) == {".srt", ".vtt", ".txt", ".md", ".json"}
    body = json.loads(files[".json"].read_text(encoding="utf-8"))
    assert body["segments"][0]["speaker"] == "Synthetic speaker"
    assert "Synthetic speaker" in files[".txt"].read_text(encoding="utf-8")
    second = execute(request, CancelToken(), lambda *a, **kw: None)
    assert first["artifacts"] == second["artifacts"]
    assert asr.calls == 1 and phases.count("diarization-loaded") == 1
