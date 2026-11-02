from __future__ import annotations

import ast
import json
import wave
from pathlib import Path
from typing import Any

import pytest

from utteran.application import (
    execute_batch,
    execute_single,
    preflight_dependencies,
    prepare_request,
)
from utteran.asr.base import ASRBackend
from utteran.config import Config, TokenProvider
from utteran.errors import (
    CancelledError,
    ConfigurationError,
    ModelNotFoundError,
    UnsupportedInputError,
)
from utteran.exporters import export_all
from utteran.jobs import JobStore
from utteran.memory import CalibrationStore
from utteran.types import (
    ASROptions,
    CancelToken,
    DeviceInfo,
    ExportOptions,
    PipelineResult,
    ProgressCallback,
    ProgressEvent,
    Segment,
    TranscriptionResult,
    Word,
)


class SyntheticASR(ASRBackend):
    name = "synthetic"

    def __init__(self) -> None:
        self.calls = 0
        self.loads = 0
        self.prompts: list[str | None] = []

    @classmethod
    def is_available(cls) -> bool:
        return True

    @classmethod
    def available_devices(cls) -> list[DeviceInfo]:
        return []

    def load(self, model_id: str, device: str, compute_type: str) -> None:
        self.loads += 1

    def unload(self) -> None:
        pass

    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        self.calls += 1
        self.prompts.append(options.initial_prompt)
        return TranscriptionResult(
            [Segment(0, 1, "synthetic words", [Word(0, 1, "synthetic words")])],
            "en",
            1,
            self.name,
            "synthetic",
            "cpu",
        )


def normalize(_input: Path, output: Path, **kw: Any) -> Path:
    with wave.open(str(output), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 16000)
    return output


def base_config(root: Path) -> Config:
    return Config.model_validate(
        {
            "general": {"output_dir": root / "output", "job_dir": root / "jobs"},
            "asr": {
                "backend": "faster-whisper",
                "device": "cpu",
                "compute_type": "int8",
                "language": "en",
            },
            "diarization": {"enabled": False},
            "output": {"formats": ["srt", "vtt", "json", "txt", "md"]},
        }
    )


def test_actual_pipeline_resume_force_hash_invalidation_and_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("utteran.pipeline.normalize_audio", normalize)
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"synthetic")
    cfg = base_config(tmp_path)
    asr = SyntheticASR()
    events: list[ProgressEvent] = []
    kwargs = {
        "asr_backend": asr,
        "progress": events.append,
        "calibration_store": CalibrationStore(tmp_path / "memory.json"),
    }
    first = execute_single(audio, cfg, **kwargs)
    assert asr.calls == 1 and {p.suffix for p in first.output_paths} == {
        ".srt",
        ".vtt",
        ".json",
        ".txt",
        ".md",
    }
    assert first.result.transcription.backend == "synthetic"
    assert events[0].event_type == "job_resolved"
    starts = [e.stage for e in events if e.event_type == "stage_start"]
    ends = [e.stage for e in events if e.event_type == "stage_done"]
    assert starts == ends
    assert execute_single(audio, cfg, **kwargs).executed_stages == ()
    cfg.output.formats = ["txt"]
    assert execute_single(audio, cfg, **kwargs).executed_stages == ("export",)
    cfg.asr.initial_prompt = "SYNTHETIC_PRIVATE_PROMPT"
    changed = execute_single(audio, cfg, **kwargs)
    assert changed.executed_stages == ("asr", "merge", "export") and asr.calls == 2
    assert asr.prompts[-1] == cfg.asr.initial_prompt
    assert cfg.asr.initial_prompt not in caplog.text
    cfg.alignment.merge_gap = 0.75
    assert execute_single(audio, cfg, **kwargs).executed_stages == ("merge", "export")
    execute_single(audio, cfg, force=True, **kwargs)
    execute_single(audio, cfg, resume=False, **kwargs)
    assert asr.calls == 4
    job = JobStore(cfg.effective_job_dir).get(first.job_id or "")
    assert job.manifest.version == 1 and job.manifest.status == "done"
    assert json.loads(job.intermediate_path("merge").read_text())["schema_version"] == 1
    restored = PipelineResult.from_dict(job.read_merged_payload())
    regenerated = export_all(restored, tmp_path / "reexport", ["txt"], ExportOptions())
    assert regenerated[0].is_file() and asr.calls == 4


def test_cancellation_and_errors_propagate_without_ui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"synthetic")
    cfg = base_config(tmp_path)
    cancel = CancelToken()
    cancel.cancel()
    monkeypatch.setattr("utteran.pipeline.normalize_audio", normalize)
    with pytest.raises(CancelledError):
        execute_single(audio, cfg, cancel=cancel, asr_backend=SyntheticASR())
    for error in (ModelNotFoundError("synthetic"), KeyboardInterrupt()):

        def fail(*args: Any, failure: BaseException = error, **kw: Any) -> Any:
            raise failure

        with pytest.raises(type(error)) as caught:
            execute_single(audio, cfg, runner=fail)
        assert caught.value is error


def test_preparation_and_preflight_keep_existing_rules(tmp_path: Path) -> None:
    cfg = base_config(tmp_path)
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"synthetic")
    with pytest.raises(ConfigurationError):
        prepare_request(audio, cfg, recursive=True)
    with pytest.raises(UnsupportedInputError):
        execute_single(tmp_path, cfg)
    cfg.asr.backend = "openvino-genai"
    cfg.asr.language = "ja"
    cfg.diarization.enabled = True
    with pytest.raises(ConfigurationError):
        prepare_request(audio, cfg)
    order: list[str] = []

    class Provider(TokenProvider):
        def get_token(self) -> str | None:
            return None

    preflight_dependencies(
        cfg,
        Provider(),
        ffmpeg_finder=lambda path: order.append("ffmpeg") or Path("ffmpeg"),
        diarization_preflight=lambda *a: order.append("diarization"),
    )
    assert order == ["diarization", "ffmpeg"]


def test_batch_uses_existing_pool_and_skip_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("utteran.pipeline.normalize_audio", normalize)
    directory = tmp_path / "input"
    directory.mkdir()
    for name in ("a.wav", "b.wav"):
        (directory / name).write_bytes(name.encode())
    cfg = base_config(tmp_path)
    backend = SyntheticASR()
    first = execute_batch(directory, cfg, asr_backend=backend)
    assert first.success_count == 2 and first.exit_code == 0
    assert backend.loads == 1 and backend.calls == 2
    second = execute_batch(directory, cfg, asr_backend=backend)
    assert second.skipped_count == 2 and backend.calls == 2


def export_result() -> PipelineResult:
    return PipelineResult(
        Path("synthetic.wav"),
        TranscriptionResult([], "en", 1, "synthetic", "synthetic", "cpu"),
        None,
        [Segment(0, 1, "synthetic words", speaker="SPEAKER_00")],
        "2026-10-30T00:00:00+00:00",
    )


def test_all_export_formats_match_unchanged_pre_b2_exporters(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/b2/exports.json").read_text())
    output = export_all(
        export_result(),
        tmp_path / "output",
        ["srt", "vtt", "json", "txt", "md"],
        ExportOptions(srt_bom=True, newline="crlf", speaker_labels={"SPEAKER_00": "Synthetic"}),
    )
    assert {p.suffix: p.read_bytes().hex() for p in output} == fixture["formats"]


def test_core_service_dependency_direction() -> None:
    root = Path(__file__).parents[1] / "src/utteran"
    for name in ("application.py", "config_adapter.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(
                name == banned or name.startswith(banned + ".")
                for name in names
                for banned in ("utteran.cli", "utteran_gui", "typer", "rich")
            )


def test_structured_adapter_and_legacy_share_identical_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from utteran.config_adapter import ExecutionContext, adapt_settings
    from utteran_contracts import ProcessingSettings

    monkeypatch.setattr("utteran.pipeline.normalize_audio", normalize)
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"synthetic")
    cfg = base_config(tmp_path)
    cfg.asr.initial_prompt = None
    source = Path(__file__).parents[1] / "docs/architecture/phase-a/examples.json"
    data = json.loads(source.read_text())["preset"]["settings"]
    data["asr"]["language"] = "en"
    data["output"]["formats"] = cfg.output.formats
    converted = adapt_settings(
        ProcessingSettings.model_validate(data),
        ExecutionContext(cfg, "faster-whisper", "large-v3-turbo", "cpu", "auto", "int8"),
    ).config
    assert converted.model_dump() == cfg.model_dump()
    backend = SyntheticASR()
    calibration = CalibrationStore(tmp_path / "memory.json")
    legacy = execute_single(audio, cfg, asr_backend=backend, calibration_store=calibration)
    structured = execute_single(
        audio, converted, asr_backend=backend, calibration_store=calibration
    )
    assert structured.job_id == legacy.job_id and structured.executed_stages == ()
    assert structured.result.to_dict() == legacy.result.to_dict()
    assert backend.calls == 1


def test_cli_rich_and_json_progress_share_service_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import io

    import utteran.cli as cli
    from utteran.progress import JsonProgressReporter
    from utteran.types import PipelineOutcome

    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"synthetic")
    cfg = base_config(tmp_path)
    outcome = PipelineOutcome(export_result(), [], "0123456789abcdef")

    def run(*args: Any, **kw: Any) -> PipelineOutcome:
        assert kw["cancel"] is not None
        kw["progress"](ProgressEvent("asr", 0, event_type="stage_start"))
        kw["progress"](ProgressEvent("asr", 1, 1))
        kw["progress"](ProgressEvent("asr", 1, event_type="stage_done"))
        return outcome

    class Provider(TokenProvider):
        def get_token(self) -> str | None:
            return None

    monkeypatch.setattr(cli, "run_pipeline", run)
    stream = io.StringIO()
    assert (
        cli._run_with_progress(
            audio,
            cfg,
            Provider(),
            False,
            JsonProgressReporter(stream),
            resume=True,
            force=False,
            force_unlock=False,
        )
        is outcome
    )
    assert [json.loads(line)["event"] for line in stream.getvalue().splitlines()] == [
        "stage_start",
        "progress",
        "stage_done",
    ]
