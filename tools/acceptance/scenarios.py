"""Run stateful acceptance scenarios without exposing transcript content."""

from __future__ import annotations

import argparse
import ctypes
import json
import locale
import logging
import os
import re
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from typer.testing import CliRunner

import utteran.cli as cli_module
from utteran.align import align_transcription
from utteran.config import Config
from utteran.jobs import fingerprint_input, job_id_from_input_hash
from utteran.logging import job_log, register_secret
from utteran.types import (
    AlignmentOptions,
    DiarizationResult,
    Segment,
    SpeakerTurn,
    TranscriptionResult,
    Word,
)

STAGES = ("audio", "asr", "diarization", "merge", "export")
TOKEN_PATTERN = re.compile(r"\bhf_[A-Za-z0-9_-]{4,}\b")
TOKEN_BYTES_PATTERN = re.compile(rb"\bhf_[A-Za-z0-9_-]{16,}\b")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"JSON root is not an object: {path.name}")
    return value


def _write(path: Path, value: dict[str, Any] | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8", newline="\n")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def _find_job(jobs: Path, input_path: Path) -> Path:
    fingerprint = fingerprint_input(input_path)
    job = jobs / job_id_from_input_hash(fingerprint.hash)
    if (job / "manifest.json").is_file():
        return job
    raise AssertionError(f"job not found for input: {input_path.name}")


def _command(parts: list[str]) -> list[str]:
    return parts[1:] if parts and parts[0] == "--" else parts


def _run(parts: list[str], expected_exit: int = 0) -> subprocess.CompletedProcess[str]:
    command = _command(parts)
    if not command:
        raise AssertionError("scenario command is empty")
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
    )
    if completed.returncode != expected_exit:
        error_lines = [
            line[:300]
            for line in completed.stderr.splitlines()
            if any(word in line.casefold() for word in ("error", "failed", "エラー", "失敗"))
        ]
        raise AssertionError(
            f"command exit was {completed.returncode}, expected {expected_exit}; "
            f"errors={error_lines[:3]}"
        )
    return completed


def validate_command_output(
    command: list[str],
    *,
    expected_exit: int,
    expected_text: tuple[str, ...],
    absent_text: tuple[str, ...],
    cwd: Path | None,
) -> None:
    """Run a CLI command and validate bounded facts without echoing its output."""
    selected_cwd = cwd
    if selected_cwd is not None:
        selected_cwd.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        _command(command),
        check=False,
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
        cwd=selected_cwd,
    )
    combined = completed.stdout + "\n" + completed.stderr
    if completed.returncode != expected_exit:
        raise AssertionError(f"command exit was {completed.returncode}, expected {expected_exit}")
    for value in expected_text:
        if value not in combined:
            raise AssertionError(f"command output is missing expected text: {value}")
    for value in absent_text:
        if value in combined:
            raise AssertionError(f"command output included forbidden text: {value}")
    if "Traceback (most recent call last)" in combined:
        raise AssertionError("expected CLI error exposed a traceback")
    print(
        json.dumps(
            {
                "exit_code": completed.returncode,
                "expected_text_count": len(expected_text),
                "traceback": False,
            },
            sort_keys=True,
        )
    )


def validate_json_output(
    command: list[str],
    *,
    required_keys: tuple[str, ...],
    expected_values: tuple[str, ...],
) -> None:
    """Validate stable dotted keys and selected scalar values in CLI JSON."""
    completed = _run(command)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError("command did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise AssertionError("JSON root is not an object")

    def lookup(dotted: str) -> object:
        value: object = payload
        for component in dotted.split("."):
            if not isinstance(value, dict) or component not in value:
                raise AssertionError(f"JSON is missing key: {dotted}")
            value = value[component]
        return value

    for key in required_keys:
        lookup(key)
    for item in expected_values:
        key, separator, expected = item.partition("=")
        if not separator:
            raise AssertionError(f"invalid expected JSON value: {item}")
        actual = lookup(key)
        normalized = json.dumps(actual, ensure_ascii=False).strip('"')
        if normalized != expected:
            raise AssertionError(f"JSON value {key} was {normalized}, expected {expected}")
    print(
        json.dumps(
            {"required_keys": len(required_keys), "expected_values": len(expected_values)},
            sort_keys=True,
        )
    )


def validate_config_lifecycle(target: Path, utteran: Path) -> None:
    """Create, preserve, and display an isolated token-free config file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    prefix = [str(utteran), "config"]
    _run([*prefix, "init", "--path", str(target)])
    original = target.read_bytes()
    _run([*prefix, "init", "--path", str(target)], expected_exit=2)
    if target.read_bytes() != original:
        raise AssertionError("config init overwrote an existing file")
    shown = _run([*prefix, "show", "--path", str(target)])
    payload = json.loads(shown.stdout)
    serialized = json.dumps(payload, ensure_ascii=False).casefold()
    if "token" in serialized or "hf_" in serialized:
        raise AssertionError("config show exposed a token field")
    print(json.dumps({"created": True, "overwrite_refused": True, "show_json": True}))


def validate_config_precedence(root: Path) -> None:
    """Verify defaults < TOML < dotenv < environment < CLI precedence."""
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "priority.toml"
    dotenv_path = root / "priority.env"
    _write(config_path, '[asr]\nlanguage = "from-config"\n')
    _write(dotenv_path, "UTTERAN_ASR__LANGUAGE=from-dotenv\n")
    variable = "UTTERAN_ASR__LANGUAGE"
    previous = os.environ.get(variable)
    try:
        os.environ[variable] = "from-environment"
        cli = Config.load(
            config_path=config_path,
            dotenv_path=dotenv_path,
            cli_overrides={"asr": {"language": "from-cli"}},
        )
        environment = Config.load(config_path=config_path, dotenv_path=dotenv_path)
        os.environ.pop(variable, None)
        dotenv = Config.load(config_path=config_path, dotenv_path=dotenv_path)
        config = Config.load(config_path=config_path, dotenv_path=root / "absent.env")
    finally:
        if previous is None:
            os.environ.pop(variable, None)
        else:
            os.environ[variable] = previous
    actual = (cli.asr.language, environment.asr.language, dotenv.asr.language, config.asr.language)
    expected = ("from-cli", "from-environment", "from-dotenv", "from-config")
    if actual != expected:
        raise AssertionError(f"config precedence was {actual}, expected {expected}")
    print(json.dumps({"precedence_levels": 5, "highest": "cli"}, sort_keys=True))


def validate_config_token_warning(root: Path, utteran: Path) -> None:
    """Ensure a dummy token key in TOML is ignored with a warning."""
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "token.toml"
    dummy = "dummy-token-value"
    _write(config_path, f'[general]\nhf_token = "{dummy}"\n')
    completed = _run([str(utteran), "config", "show", "--path", str(config_path)])
    combined = completed.stdout + completed.stderr
    if dummy in combined or "安全のため無視" not in combined:
        raise AssertionError("config token key was not safely ignored with a warning")
    print(json.dumps({"dummy_exposed": False, "warning": True}))


def validate_alignment_setting(root: Path) -> None:
    """Demonstrate that a TOML alignment threshold changes merge output."""
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "alignment.toml"
    _write(config_path, "[alignment]\nmerge_gap = 0.0\n")
    config = Config.load(config_path=config_path, dotenv_path=root / "absent.env")
    transcription = TranscriptionResult(
        segments=[
            Segment(0.0, 1.0, "a", [Word(0.0, 1.0, "a")]),
            Segment(1.2, 2.0, "b", [Word(1.2, 2.0, "b")]),
        ],
        language="ja",
        duration=2.0,
        backend="fake",
        model_id="fake",
        device="cpu",
    )
    diarization = DiarizationResult(
        turns=[SpeakerTurn(0.0, 2.0, "SPEAKER_00")],
        exclusive_turns=None,
        num_speakers=1,
        backend="fake",
        model_id="fake",
        device="cpu",
    )
    default = align_transcription(transcription, diarization)
    configured = align_transcription(
        transcription,
        diarization,
        AlignmentOptions(**config.alignment.model_dump()),
    )
    if len(default) != 1 or len(configured) != 2:
        raise AssertionError("alignment merge_gap did not change the merged result")
    print(json.dumps({"default_segments": 1, "configured_segments": 2}))


def validate_invalid_config(root: Path, utteran: Path) -> None:
    """Require invalid validated settings to return configuration exit code 2."""
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "invalid.toml"
    _write(config_path, "[asr]\nbeam_size = 0\n")
    completed = _run(
        [str(utteran), "config", "show", "--path", str(config_path)],
        expected_exit=2,
    )
    combined = completed.stdout + completed.stderr
    if "設定が不正" not in combined or "Traceback" in combined:
        raise AssertionError("invalid config error was not actionable")
    print(json.dumps({"exit_code": 2, "traceback": False}))


def validate_verbose_error(root: Path) -> None:
    """Expose an unexpected traceback only when --verbose is explicitly set."""
    root.mkdir(parents=True, exist_ok=True)
    input_path = root / "synthetic.wav"
    input_path.write_bytes(b"synthetic media placeholder")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic unexpected detail")

    runner = CliRunner()
    with (
        patch.object(cli_module, "find_ffmpeg", return_value=Path("ffmpeg")),
        patch.object(cli_module, "_ensure_configured_models", return_value=None),
        patch.object(cli_module, "_run_with_progress", side_effect=fail),
    ):
        regular = runner.invoke(
            cli_module.app,
            ["transcribe", str(input_path), "--no-diarization"],
        )
        verbose = runner.invoke(
            cli_module.app,
            ["transcribe", str(input_path), "--no-diarization", "--verbose"],
        )
    if regular.exit_code != 1 or verbose.exit_code != 1:
        raise AssertionError("unexpected-error CLI exit code was not 1")
    if "Traceback" in regular.output or "synthetic unexpected detail" not in regular.output:
        raise AssertionError("regular unexpected error detail policy is incorrect")
    if "Traceback" not in verbose.output or "synthetic unexpected detail" not in verbose.output:
        raise AssertionError("verbose unexpected error did not include diagnostic detail")
    print(json.dumps({"regular_traceback": False, "verbose_traceback": True}))


def validate_security_redaction(root: Path) -> None:
    """Inject a dummy token into verbose exceptions and structured job logs."""
    root.mkdir(parents=True, exist_ok=True)
    input_path = root / "synthetic.wav"
    input_path.write_bytes(b"synthetic media placeholder")
    dummy = "hf_acceptanceDummyToken123456"

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(f"synthetic backend exception included {dummy}")

    runner = CliRunner()
    with (
        patch.dict(os.environ, {"HF_TOKEN": dummy}),
        patch.object(cli_module, "find_ffmpeg", return_value=Path("ffmpeg")),
        patch.object(cli_module, "_ensure_configured_models", return_value=None),
        patch.object(cli_module, "_run_with_progress", side_effect=fail),
    ):
        regular = runner.invoke(
            cli_module.app,
            ["transcribe", str(input_path), "--no-diarization"],
        )
        verbose = runner.invoke(
            cli_module.app,
            ["transcribe", str(input_path), "--no-diarization", "--verbose"],
        )
    register_secret(dummy)
    log_path = root / "redaction.log"
    with job_log(log_path):
        logging.getLogger(__name__).error("synthetic log secret=%s", dummy)
    combined = regular.output + verbose.output + log_path.read_text(encoding="utf-8")
    if dummy in combined or "hf_****" not in combined:
        raise AssertionError("dummy Hugging Face token was not consistently masked")
    print(
        json.dumps(
            {"console_masked": True, "verbose_masked": True, "job_log_masked": True},
            sort_keys=True,
        )
    )


def validate_security_scan(project: Path, roots: tuple[Path, ...]) -> None:
    """Scan generated artifacts for token-shaped bytes and verify Git ignores."""
    scanned = 0
    matches: list[str] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            scanned += 1
            try:
                if TOKEN_BYTES_PATTERN.search(path.read_bytes()):
                    matches.append(str(path.relative_to(project)))
            except OSError:
                continue
    if matches:
        raise AssertionError(f"token-shaped data found in generated files: {matches[:10]}")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    forbidden = [line for line in status.splitlines() if _is_private_status_line(line)]
    if forbidden:
        raise AssertionError("private input/output path appeared in git status")
    for candidate in (
        project / ".env",
        project / "input" / "2026-08-01 11-01-55.mp4",
        project / "output" / "_acceptance" / "results.jsonl",
    ):
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", str(candidate)],
            cwd=project,
            check=False,
        )
        if ignored.returncode != 0:
            raise AssertionError(f"private path is not ignored: {candidate.name}")
    print(json.dumps({"files_scanned": scanned, "token_matches": 0, "git_ignored": 3}))


def _is_private_status_line(line: str) -> bool:
    normalized = line[3:].replace("\\", "/") if len(line) > 3 else line
    return normalized == ".env" or normalized.startswith(("input/", "output/"))


def _stage_signature(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        stage: json.dumps(manifest["stages"][stage], ensure_ascii=False, sort_keys=True)
        for stage in STAGES
    }


def validate_stages(
    jobs: Path,
    input_path: Path,
    expected_stages: tuple[str, ...],
    command: list[str],
) -> None:
    job = _find_job(jobs, input_path)
    before = _stage_signature(_load(job / "manifest.json"))
    _run(command)
    after = _stage_signature(_load(job / "manifest.json"))
    changed = tuple(stage for stage in STAGES if before[stage] != after[stage])
    if set(changed) != set(expected_stages):
        raise AssertionError(f"changed stages were {changed}, expected {expected_stages}")
    print(json.dumps({"changed_stages": changed}, sort_keys=True))


def _wait_for(
    condition: Any,
    *,
    timeout: float,
    description: str,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {description}")


def _start(command: list[str]) -> subprocess.Popen[str]:
    options: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": locale.getpreferredencoding(False),
        "errors": "replace",
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        options["start_new_session"] = True
    return subprocess.Popen(_command(command), **options)


def _interrupt(process: subprocess.Popen[str]) -> tuple[int, str, str]:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.FreeConsole()
        if not kernel32.AttachConsole(process.pid):
            raise OSError(ctypes.get_last_error(), "failed to attach to child console")
        if not kernel32.SetConsoleCtrlHandler(None, True):
            raise OSError(ctypes.get_last_error(), "failed to ignore Ctrl+C in signal sender")
        try:
            if not kernel32.GenerateConsoleCtrlEvent(0, 0):
                raise OSError(ctypes.get_last_error(), "failed to send Ctrl+C to child console")
            stdout, stderr = process.communicate(timeout=60)
        finally:
            kernel32.FreeConsole()
            kernel32.SetConsoleCtrlHandler(None, False)
        return int(process.returncode or 0), stdout, stderr
    else:
        os.killpg(process.pid, signal.SIGINT)
    stdout, stderr = process.communicate(timeout=60)
    return int(process.returncode or 0), stdout, stderr


def interrupt_asr(
    jobs: Path,
    input_path: Path,
    delay_seconds: float,
    command: list[str],
) -> None:
    try:
        existing_job = _find_job(jobs, input_path)
        manifest_path = existing_job / "manifest.json"
        log_path = existing_job / "utteran.log"
        initial_manifest_mtime = manifest_path.stat().st_mtime_ns
        initial_log_size = log_path.stat().st_size if log_path.is_file() else 0
    except AssertionError:
        initial_manifest_mtime = -1
        initial_log_size = 0
    process = _start(command)

    def asr_running() -> bool:
        try:
            job = _find_job(jobs, input_path)
            current_manifest = job / "manifest.json"
            if (
                initial_manifest_mtime >= 0
                and current_manifest.stat().st_mtime_ns == initial_manifest_mtime
            ):
                return False
            if _load(current_manifest)["stages"]["asr"]["status"] != "running":
                return False
            current_log = job / "utteran.log"
            if not current_log.is_file() or current_log.stat().st_size <= initial_log_size:
                return False
            with current_log.open("rb") as stream:
                stream.seek(initial_log_size)
                return b"Processing audio with duration" in stream.read()
        except (AssertionError, KeyError, OSError, json.JSONDecodeError):
            return False

    try:
        _wait_for(asr_running, timeout=120, description="fresh ASR inference log")
        time.sleep(delay_seconds)
        exit_code, _stdout, stderr = _interrupt(process)
    except Exception:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise
    if exit_code != 130:
        error_lines = [
            line[:300]
            for line in stderr.splitlines()
            if any(word in line.casefold() for word in ("error", "failed", "エラー", "失敗"))
        ]
        raise AssertionError(f"SIGINT exit was {exit_code}, expected 130; errors={error_lines[:3]}")
    from utteran.jobs import JobStore

    recovered = JobStore(jobs).open(input_path)
    manifest = recovered.manifest.to_dict()
    if manifest["stages"]["audio"]["status"] != "done":
        raise AssertionError("audio stage was not retained after interruption")
    if manifest["stages"]["asr"]["status"] != "pending":
        raise AssertionError("ASR stage was not returned to pending")
    print(json.dumps({"exit_code": exit_code, "audio": "done", "asr": "pending"}))


def concurrent_lock(jobs: Path, input_path: Path, command: list[str]) -> None:
    job = _find_job(jobs, input_path)
    lock_path = job / ".lock"
    initial_lock = lock_path.read_bytes() if lock_path.is_file() else None
    first = _start(command)

    def lock_exists() -> bool:
        try:
            if first.poll() is not None or not lock_path.is_file():
                return False
            current = lock_path.read_bytes()
            return initial_lock is None or current != initial_lock
        except OSError:
            return False

    try:
        _wait_for(lock_exists, timeout=60, description="job lock")
        second = _run(command, expected_exit=1)
        combined = second.stdout + "\n" + second.stderr
        if "PID" not in combined or "force-unlock" not in combined:
            edge = [line[:300] for line in combined.splitlines() if line.strip()][-6:]
            raise AssertionError(
                f"lock rejection did not identify its owner and recovery option; output_tail={edge}"
            )
        first_exit, _stdout, _stderr = _interrupt(first)
    except Exception:
        if first.poll() is None:
            first.kill()
        first.communicate()
        raise
    if first_exit != 130:
        raise AssertionError(f"lock owner interruption exited {first_exit}, expected 130")
    print(json.dumps({"contender_exit": 1, "owner_exit": first_exit}))


def artificial_lock(
    jobs: Path,
    input_path: Path,
    *,
    live: bool,
    command: list[str],
) -> None:
    job = _find_job(jobs, input_path)
    pid = os.getpid() if live else 2_147_483_647
    _write(
        job / ".lock",
        {"pid": pid, "started_at": datetime.now().astimezone().isoformat()},
    )
    _run(command)
    if (job / ".lock").exists():
        raise AssertionError("artificial lock was not removed after successful run")
    print(json.dumps({"lock_owner_was_live": live, "exit_code": 0}))


def corrupt_manifest(jobs: Path, command: list[str]) -> None:
    corrupt = jobs / "ffffffffffffffff" / "manifest.json"
    _write(corrupt, "{not valid json\n")
    completed = _run(command)
    if "corrupt" not in completed.stdout.casefold():
        raise AssertionError("jobs list did not classify the unreadable manifest as corrupt")
    print(json.dumps({"corrupt_job_id": corrupt.parent.name, "listed": True}))


def _synthetic_manifest(job_id: str, status: str, updated_at: str) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    for stage in STAGES:
        stage_status = "failed" if status == "failed" and stage == "asr" else "pending"
        stages[stage] = {
            "status": stage_status,
            "config_hash": None,
            "finished_at": updated_at if stage_status == "failed" else None,
            "error": "synthetic failure" if stage_status == "failed" else None,
            "artifacts": [],
        }
    return {
        "version": 1,
        "job_id": job_id,
        "input": {"path": f"fixture-{job_id}.wav", "size": 0, "mtime": 0, "hash": job_id},
        "created_at": updated_at,
        "updated_at": updated_at,
        "stages": stages,
    }


def jobs_management(jobs: Path, utteran: Path, config: Path) -> None:
    now = datetime.now(UTC).astimezone().isoformat()
    old = "2000-01-01T00:00:00+00:00"
    identifiers = {
        "failed": "1111111111111111",
        "old": "2222222222222222",
        "fresh": "3333333333333333",
    }
    _write(
        jobs / identifiers["failed"] / "manifest.json",
        _synthetic_manifest(identifiers["failed"], "failed", now),
    )
    _write(
        jobs / identifiers["old"] / "manifest.json",
        _synthetic_manifest(identifiers["old"], "pending", old),
    )
    _write(
        jobs / identifiers["fresh"] / "manifest.json",
        _synthetic_manifest(identifiers["fresh"], "pending", now),
    )
    prefix = [str(utteran), "jobs"]
    suffix = ["--config", str(config)]
    _run([*prefix, "list", *suffix])
    _run([*prefix, "show", identifiers["fresh"], *suffix])
    _run([*prefix, "clean", "--failed", "--yes", *suffix])
    if (jobs / identifiers["failed"]).exists():
        raise AssertionError("jobs clean --failed did not remove the failed job")
    _run([*prefix, "clean", "--older-than", "1", "--yes", *suffix])
    if (jobs / identifiers["old"]).exists():
        raise AssertionError("jobs clean --older-than did not remove the old job")
    _run([*prefix, "clean", "--all", "--yes", *suffix])
    if any(jobs.iterdir()):
        raise AssertionError("jobs clean --all did not empty the isolated job store")
    print(json.dumps({"list": True, "show": True, "failed": True, "older": True, "all": True}))


def validate_log(jobs: Path, input_path: Path) -> None:
    log = _find_job(jobs, input_path) / "utteran.log"
    raw = log.read_text(encoding="utf-8")
    if TOKEN_PATTERN.search(raw):
        raise AssertionError("Hugging Face token-like value found in job log")
    lines = [line for line in raw.splitlines() if line]
    for line in lines:
        json.loads(line)
    if not lines:
        raise AssertionError("job log is empty")
    print(json.dumps({"json_log_lines": len(lines), "token_pattern_found": False}))


def validate_batch_summary(
    command: list[str],
    *,
    jobs: Path,
    directory: Path,
    expected_exit: int,
    expected_success: int,
    expected_skipped: int,
    expected_failed: int,
    expected_order: tuple[str, ...],
) -> None:
    completed = _run(command, expected_exit=expected_exit)
    match = re.search(
        r"集計:\s*成功\s+(\d+)\s*/\s*スキップ\s+(\d+)\s*/\s*失敗\s+(\d+)",
        completed.stdout,
    )
    if match is None:
        raise AssertionError("batch summary line was not found")
    counts = tuple(int(value) for value in match.groups())
    expected = (expected_success, expected_skipped, expected_failed)
    if counts != expected:
        raise AssertionError(f"batch counts were {counts}, expected {expected}")
    updates = tuple(
        str(_load(_find_job(jobs, directory / name) / "manifest.json")["updated_at"])
        for name in expected_order
    )
    if updates != tuple(sorted(updates)):
        raise AssertionError(f"batch job update order was not {expected_order}")
    print(
        json.dumps(
            {
                "exit_code": expected_exit,
                "success": counts[0],
                "skipped": counts[1],
                "failed": counts[2],
            },
            sort_keys=True,
        )
    )


def validate_batch_state(
    jobs: Path,
    directory: Path,
    output: Path,
    expected_order: tuple[str, ...],
) -> None:
    records: list[tuple[str, str, dict[str, Any]]] = []
    for name in expected_order:
        job = _find_job(jobs, directory / name)
        manifest = _load(job / "manifest.json")
        records.append((str(manifest["created_at"]), name, manifest))
    statuses = {
        name: (
            "failed"
            if any(stage["status"] == "failed" for stage in manifest["stages"].values())
            else "done"
            if all(stage["status"] == "done" for stage in manifest["stages"].values())
            else "pending"
        )
        for _created, name, manifest in records
    }
    if statuses.get("broken.mp4") != "failed":
        raise AssertionError("broken.mp4 job is not failed")
    completed = [name for name, status in statuses.items() if status == "done"]
    output_count = len(list(output.glob("*.json")))
    if output_count < len(completed):
        raise AssertionError(
            f"batch output count was {output_count}, expected at least {len(completed)}"
        )
    print(
        json.dumps(
            {"jobs": len(records), "failed": 1, "completed": len(completed)},
            sort_keys=True,
        )
    )


def validate_backend_reuse(jobs: Path, inputs: tuple[Path, ...]) -> None:
    messages: list[str] = []
    job_roots = {_find_job(jobs, input_path) for input_path in inputs}
    for job_root in job_roots:
        log = job_root / "utteran.log"
        for line in log.read_text(encoding="utf-8").splitlines():
            if line:
                messages.append(str(json.loads(line).get("message", "")))
    loads = sum("ASRバックエンドをロード" in message for message in messages)
    reuses = sum("ASRバックエンドを再利用" in message for message in messages)
    if loads != 1 or reuses < len(inputs) - 1:
        raise AssertionError(f"ASR backend log counts were load={loads}, reuse={reuses}")
    print(json.dumps({"asr_loads": loads, "asr_reuses": reuses}, sort_keys=True))


def validate_dry_run(
    jobs: Path,
    expected: tuple[str, ...],
    absent: tuple[str, ...],
    command: list[str],
) -> None:
    before = {
        path: path.stat().st_mtime_ns for path in jobs.glob("*/manifest.json") if path.is_file()
    }
    completed = _run(command)
    for value in expected:
        if value not in completed.stdout:
            raise AssertionError(f"dry-run output is missing expected candidate: {value}")
    for value in absent:
        if value in completed.stdout:
            raise AssertionError(f"dry-run output included excluded candidate: {value}")
    after = {
        path: path.stat().st_mtime_ns for path in jobs.glob("*/manifest.json") if path.is_file()
    }
    if before != after:
        raise AssertionError("dry-run changed job manifests")
    print(json.dumps({"candidate_count": len(expected), "jobs_unchanged": True}))


def validate_generated_exclusion(
    output: Path,
    jobs: Path,
    command: list[str],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    jobs.mkdir(parents=True, exist_ok=True)
    (output / "should_not_process.mp4").write_bytes(b"generated output fixture")
    (jobs / "should_not_process.mp4").write_bytes(b"generated job fixture")
    _run(command)
    if list(jobs.glob("*/manifest.json")):
        raise AssertionError("generated job directory was recursively processed")
    if list(output.glob("*.json")):
        raise AssertionError("generated output directory was recursively processed")
    print(json.dumps({"excluded_generated_candidates": 2}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="scenario", required=True)

    stages = subparsers.add_parser("stages")
    stages.add_argument("--jobs", type=Path, required=True)
    stages.add_argument("--input", type=Path, required=True)
    stages.add_argument("--expected", default="")
    stages.add_argument("command", nargs=argparse.REMAINDER)

    interrupt = subparsers.add_parser("interrupt")
    interrupt.add_argument("--jobs", type=Path, required=True)
    interrupt.add_argument("--input", type=Path, required=True)
    interrupt.add_argument("--delay", type=float, default=5.0)
    interrupt.add_argument("command", nargs=argparse.REMAINDER)

    lock = subparsers.add_parser("concurrent-lock")
    lock.add_argument("--jobs", type=Path, required=True)
    lock.add_argument("--input", type=Path, required=True)
    lock.add_argument("command", nargs=argparse.REMAINDER)

    artificial = subparsers.add_parser("artificial-lock")
    artificial.add_argument("--jobs", type=Path, required=True)
    artificial.add_argument("--input", type=Path, required=True)
    artificial.add_argument("--live", action="store_true")
    artificial.add_argument("command", nargs=argparse.REMAINDER)

    corrupt = subparsers.add_parser("corrupt")
    corrupt.add_argument("--jobs", type=Path, required=True)
    corrupt.add_argument("command", nargs=argparse.REMAINDER)

    management = subparsers.add_parser("jobs-management")
    management.add_argument("--jobs", type=Path, required=True)
    management.add_argument("--utteran", type=Path, required=True)
    management.add_argument("--config", type=Path, required=True)

    log = subparsers.add_parser("log")
    log.add_argument("--jobs", type=Path, required=True)
    log.add_argument("--input", type=Path, required=True)

    batch_summary = subparsers.add_parser("batch-summary")
    batch_summary.add_argument("--jobs", type=Path, required=True)
    batch_summary.add_argument("--dir", type=Path, required=True)
    batch_summary.add_argument("--exit", type=int, required=True)
    batch_summary.add_argument("--success", type=int, required=True)
    batch_summary.add_argument("--skipped", type=int, required=True)
    batch_summary.add_argument("--failed", type=int, required=True)
    batch_summary.add_argument("--order", default="")
    batch_summary.add_argument("command", nargs=argparse.REMAINDER)

    batch_state = subparsers.add_parser("batch-state")
    batch_state.add_argument("--jobs", type=Path, required=True)
    batch_state.add_argument("--dir", type=Path, required=True)
    batch_state.add_argument("--output", type=Path, required=True)
    batch_state.add_argument("--order", required=True)

    reuse = subparsers.add_parser("backend-reuse")
    reuse.add_argument("--jobs", type=Path, required=True)
    reuse.add_argument("inputs", type=Path, nargs="+")

    dry_run = subparsers.add_parser("dry-run")
    dry_run.add_argument("--jobs", type=Path, required=True)
    dry_run.add_argument("--expected", required=True)
    dry_run.add_argument("--absent", default="")
    dry_run.add_argument("command", nargs=argparse.REMAINDER)

    generated = subparsers.add_parser("generated-exclusion")
    generated.add_argument("--output", type=Path, required=True)
    generated.add_argument("--jobs", type=Path, required=True)
    generated.add_argument("command", nargs=argparse.REMAINDER)

    command_output = subparsers.add_parser("command-output")
    command_output.add_argument("--exit", type=int, default=0)
    command_output.add_argument("--contains", action="append", default=[])
    command_output.add_argument("--absent", action="append", default=[])
    command_output.add_argument("--cwd", type=Path)
    command_output.add_argument("command", nargs=argparse.REMAINDER)

    json_output = subparsers.add_parser("json-output")
    json_output.add_argument("--key", action="append", default=[])
    json_output.add_argument("--value", action="append", default=[])
    json_output.add_argument("command", nargs=argparse.REMAINDER)

    config_lifecycle = subparsers.add_parser("config-lifecycle")
    config_lifecycle.add_argument("--target", type=Path, required=True)
    config_lifecycle.add_argument("--utteran", type=Path, required=True)

    config_precedence = subparsers.add_parser("config-precedence")
    config_precedence.add_argument("--root", type=Path, required=True)

    config_token = subparsers.add_parser("config-token")
    config_token.add_argument("--root", type=Path, required=True)
    config_token.add_argument("--utteran", type=Path, required=True)

    alignment = subparsers.add_parser("alignment-setting")
    alignment.add_argument("--root", type=Path, required=True)

    invalid_config = subparsers.add_parser("invalid-config")
    invalid_config.add_argument("--root", type=Path, required=True)
    invalid_config.add_argument("--utteran", type=Path, required=True)

    verbose_error = subparsers.add_parser("verbose-error")
    verbose_error.add_argument("--root", type=Path, required=True)

    security_redaction = subparsers.add_parser("security-redaction")
    security_redaction.add_argument("--root", type=Path, required=True)

    security_scan = subparsers.add_parser("security-scan")
    security_scan.add_argument("--project", type=Path, required=True)
    security_scan.add_argument("roots", type=Path, nargs="+")

    args = parser.parse_args()
    if args.scenario == "stages":
        validate_stages(
            args.jobs,
            args.input,
            tuple(filter(None, args.expected.split(","))),
            args.command,
        )
    elif args.scenario == "interrupt":
        interrupt_asr(args.jobs, args.input, args.delay, args.command)
    elif args.scenario == "concurrent-lock":
        concurrent_lock(args.jobs, args.input, args.command)
    elif args.scenario == "artificial-lock":
        artificial_lock(args.jobs, args.input, live=args.live, command=args.command)
    elif args.scenario == "corrupt":
        corrupt_manifest(args.jobs, args.command)
    elif args.scenario == "jobs-management":
        jobs_management(args.jobs, args.utteran, args.config)
    elif args.scenario == "log":
        validate_log(args.jobs, args.input)
    elif args.scenario == "batch-summary":
        validate_batch_summary(
            args.command,
            jobs=args.jobs,
            directory=args.dir,
            expected_exit=args.exit,
            expected_success=args.success,
            expected_skipped=args.skipped,
            expected_failed=args.failed,
            expected_order=tuple(filter(None, args.order.split(","))),
        )
    elif args.scenario == "batch-state":
        validate_batch_state(
            args.jobs,
            args.dir,
            args.output,
            tuple(filter(None, args.order.split(","))),
        )
    elif args.scenario == "backend-reuse":
        validate_backend_reuse(args.jobs, tuple(args.inputs))
    elif args.scenario == "dry-run":
        validate_dry_run(
            args.jobs,
            tuple(filter(None, args.expected.split(","))),
            tuple(filter(None, args.absent.split(","))),
            args.command,
        )
    elif args.scenario == "generated-exclusion":
        validate_generated_exclusion(args.output, args.jobs, args.command)
    elif args.scenario == "command-output":
        validate_command_output(
            args.command,
            expected_exit=args.exit,
            expected_text=tuple(args.contains),
            absent_text=tuple(args.absent),
            cwd=args.cwd,
        )
    elif args.scenario == "json-output":
        validate_json_output(
            args.command,
            required_keys=tuple(args.key),
            expected_values=tuple(args.value),
        )
    elif args.scenario == "config-lifecycle":
        validate_config_lifecycle(args.target, args.utteran)
    elif args.scenario == "config-precedence":
        validate_config_precedence(args.root)
    elif args.scenario == "config-token":
        validate_config_token_warning(args.root, args.utteran)
    elif args.scenario == "alignment-setting":
        validate_alignment_setting(args.root)
    elif args.scenario == "invalid-config":
        validate_invalid_config(args.root, args.utteran)
    elif args.scenario == "verbose-error":
        validate_verbose_error(args.root)
    elif args.scenario == "security-redaction":
        validate_security_redaction(args.root)
    else:
        validate_security_scan(args.project, tuple(args.roots))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
