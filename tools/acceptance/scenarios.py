"""Run stateful acceptance scenarios without exposing transcript content."""

from __future__ import annotations

import argparse
import ctypes
import json
import locale
import os
import re
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STAGES = ("audio", "asr", "diarization", "merge", "export")
TOKEN_PATTERN = re.compile(r"\bhf_[A-Za-z0-9_-]{4,}\b")


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
    target = input_path.resolve()
    for manifest_path in jobs.glob("*/manifest.json"):
        try:
            manifest = _load(manifest_path)
            if Path(str(manifest["input"]["path"])).resolve() == target:
                return manifest_path.parent
        except (AssertionError, KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
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
    first = _start(command)

    def lock_exists() -> bool:
        try:
            return (_find_job(jobs, input_path) / ".lock").is_file()
        except AssertionError:
            return False

    try:
        _wait_for(lock_exists, timeout=60, description="job lock")
        second = _run(command, expected_exit=1)
        combined = second.stdout + "\n" + second.stderr
        if "PID" not in combined or "force-unlock" not in combined:
            raise AssertionError("lock rejection did not identify its owner and recovery option")
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
    else:
        validate_log(args.jobs, args.input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
