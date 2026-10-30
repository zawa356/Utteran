"""Golden captured from unmodified CLI at B1 gate commit 0f80d17, before B2 extraction."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import utteran.cli as cli
from utteran.batch import BatchItemResult, BatchSummary
from utteran.config import Config, TokenProvider
from utteran.errors import CancelledError, ModelNotFoundError
from utteran.jobs import stage_config_hashes
from utteran.types import PipelineOutcome, PipelineResult, ProgressEvent, TranscriptionResult

FIXTURE = Path(__file__).parent / "fixtures/b2/legacy_cli.json"
CASES: dict[str, dict[str, Any]] = {
    "defaults": {},
    "toml": {
        "toml": (
            '[asr]\nbackend="faster-whisper"\nmodel="large-v3"\nlanguage="en"\n'
            'beam_size=7\ninitial_prompt="synthetic prompt"\n'
        )
    },
    "environment": {
        "toml": '[asr]\nlanguage="en"\n',
        "env": {"UTTERAN_ASR__LANGUAGE": "fr", "UTTERAN_ASR__BEAM_SIZE": "8"},
    },
    "dotenv": {"dotenv": "UTTERAN_ASR__LANGUAGE=de\n"},
    "cli_precedence": {
        "toml": '[asr]\nlanguage="en"\n',
        "env": {"UTTERAN_ASR__LANGUAGE": "fr"},
        "args": [
            "--asr-backend",
            "faster-whisper",
            "--asr-model",
            "large-v3",
            "--language",
            "auto",
            "--num-speakers",
            "3",
            "--asr-device",
            "cpu",
            "--diarization-device",
            "cuda",
        ],
    },
    "formats": {"args": ["--format", "md,srt,vtt,json,txt", "--no-diarization"]},
    "no_resume": {"args": ["--no-resume"]},
    "force": {"args": ["--force", "--force-unlock"]},
    "batch": {"folder": True, "args": ["--recursive", "--include", "*.wav", "--exclude", "skip*"]},
    "batch_partial": {"folder": True, "failure": "partial"},
    "dry_run": {"args": ["--dry-run"]},
    "folder_dry_run": {"folder": True, "args": ["--dry-run", "--recursive"]},
    "invalid_file_filter": {"args": ["--recursive"]},
    "missing": {"input": "missing.wav"},
    "invalid_config": {"toml": "[asr]\nbeam_size=0\n"},
    "model_error": {"failure": "model"},
    "cancel": {"failure": "cancel"},
    "keyboard_interrupt": {"failure": "keyboard"},
    "unexpected": {"failure": "unexpected"},
}


class NoToken(TokenProvider):
    def get_token(self) -> str | None:
        return None


def record_case(case: str, directory: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    spec = CASES[case]
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(directory)
    for key in tuple(os.environ):
        if key.startswith("UTTERAN_") or key in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(directory / "private"))
    for key, value in spec.get("env", {}).items():
        monkeypatch.setenv(key, value)
    Path(".env").write_text(spec.get("dotenv", ""), encoding="utf-8")
    Path("config.toml").write_text(spec.get("toml", ""), encoding="utf-8")
    Path("sample.wav").write_bytes(b"synthetic audio")
    Path("folder").mkdir(exist_ok=True)
    Path("folder/sample.wav").write_bytes(b"synthetic audio")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli, "configure_runtime_logging", lambda **kw: None)
    monkeypatch.setattr(cli, "register_secret", lambda *_: None)
    monkeypatch.setattr(cli, "default_token_provider", NoToken)
    monkeypatch.setattr(cli, "preflight_diarization_backend", lambda *_: None)
    monkeypatch.setattr(cli, "find_ffmpeg", lambda *_: Path("ffmpeg"))
    monkeypatch.setattr(cli, "_ensure_configured_models", lambda *a, **kw: None)

    def single(path: Path, config: Config, **kwargs: Any) -> PipelineOutcome:
        calls.append(
            {
                "config": config.model_dump(mode="json"),
                "flags": {key: kwargs[key] for key in ("resume", "force", "force_unlock")},
            }
        )
        failure = spec.get("failure")
        if failure == "model":
            raise ModelNotFoundError("synthetic model missing")
        if failure == "cancel":
            raise CancelledError()
        if failure == "keyboard":
            raise KeyboardInterrupt()
        if failure == "unexpected":
            raise RuntimeError("synthetic failure")
        progress = kwargs.get("progress")
        if progress:
            progress(ProgressEvent("asr", 0, event_type="stage_start"))
            progress(ProgressEvent("asr", 1, event_type="stage_done", duration_seconds=1.25))
        result = PipelineResult(
            path,
            TranscriptionResult([], "en", 1, "synthetic", "synthetic", "cpu"),
            None,
            [],
            "synthetic-time",
        )
        return PipelineOutcome(
            result,
            [Path("output/sample.srt")],
            "0123456789abcdef",
            ("audio", "asr", "merge", "export"),
        )

    def batch(path: Path, config: Config, **kwargs: Any) -> BatchSummary:
        outcome = single(path / "sample.wav", config, **kwargs)
        calls[-1]["batch"] = {
            key: list(kwargs[key]) if isinstance(kwargs[key], tuple) else kwargs[key]
            for key in ("recursive", "include", "exclude")
        }
        return BatchSummary(
            (
                BatchItemResult(path / "sample.wav", "success", "synthetic", outcome),
                BatchItemResult(
                    path / "other.wav",
                    "failed" if spec.get("failure") == "partial" else "skipped",
                    "synthetic",
                ),
            )
        )

    monkeypatch.setattr(cli, "run_pipeline", single)
    monkeypatch.setattr(cli, "run_batch", batch)
    input_path = spec.get("input", "folder" if spec.get("folder") else "sample.wav")
    result = CliRunner().invoke(
        cli.app,
        [
            "transcribe",
            input_path,
            "--quiet",
            "--progress-json",
            "--config",
            "config.toml",
            *spec.get("args", []),
        ],
    )
    events, text = [], []
    for line in result.output.splitlines():
        if line.startswith('{"schema_version"'):
            event = json.loads(line)
            event.pop("timestamp")
            if event["event"] == "done":
                event["duration_seconds"] = "<elapsed>"
            events.append(event)
        else:
            text.append(line)

    def paths(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("folder\\", "folder/").replace("output\\", "output/")
        if isinstance(value, list):
            return [paths(item) for item in value]
        if isinstance(value, dict):
            return {key: paths(item) for key, item in value.items()}
        return value

    return paths(
        {
            "exit_code": result.exit_code,
            "calls": calls,
            "events": events,
            "console": "\n".join(text),
        }
    )


@pytest.mark.parametrize("case", CASES)
def test_legacy_cli_matches_pre_b2_golden(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"][case]
    assert record_case(case, tmp_path, monkeypatch) == expected


def test_pre_b2_stage_hash_golden() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for example in data["hashes"]:
        config = Config.model_validate(example["config"])
        hashes = stage_config_hashes(config, "synthetic-input-hash")
        # export includes the platform-specific absolute output path; it is checked
        # separately by adapter/legacy equivalence and the output/resume tests.
        assert {k: v for k, v in hashes.items() if k != "export"} == example["hashes"]
