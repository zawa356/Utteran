"""First-run setup wizard orchestration: venv build, model download, smoke test.

Runs `setup.ps1` and the resulting profile's `utteran.exe` as tracked
subprocess jobs the GUI can stream progress from and cancel, using the same
single-job-at-a-time / event-cursor / process-tree-kill shape as
`utteran_gui.jobs.JobManager` (shared via `utteran_gui.processes` so the two
never diverge). This module still never imports `utteran` or any inference
package - it only launches other processes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from utteran_gui.cli import CliAdapter
from utteran_gui.jobs import guidance_for
from utteran_gui.processes import PopenFactory, TreeKiller, build_popen_kwargs, kill_process_tree
from utteran_gui.security import mask_secrets
from utteran_gui.settings import PROFILE_NAMES, SettingsStore

WizardJobKind = Literal["venv_build", "model_download", "smoke_test"]
WizardJobStatus = Literal["starting", "running", "completed", "failed", "cancelled"]
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

# Emitted by setup.ps1's Write-Step -Stage as a machine-readable line alongside
# its normal human-readable "==> message" output, so the wizard can show a
# concrete stage name instead of guessing one from free text.
STAGE_MARKER = "##UTTERAN-WIZARD## stage="


class WizardBusyError(RuntimeError):
    """A second wizard operation was requested while one is already running."""


class WizardUnknownJobError(KeyError):
    """A requested wizard job id does not exist in this process."""


class WizardProfileMissingError(RuntimeError):
    """A model-download or smoke-test step was requested before the profile venv exists."""


class WizardNotReadyError(RuntimeError):
    """Completion was requested before any smoke-test job has succeeded."""


@dataclass
class WizardJob:
    id: str
    kind: WizardJobKind
    profile: str
    command: list[str]
    environment: dict[str, str]
    status: WizardJobStatus = "starting"
    stage: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    events: list[dict[str, object]] = field(default_factory=list)
    raw_logs: list[str] = field(default_factory=list)
    cancel_requested: bool = False
    last_event_monotonic: float = field(default_factory=time.monotonic)
    started_monotonic: float | None = None
    process: subprocess.Popen[str] | None = None
    cleanup: Callable[[], None] | None = None


class SetupWizardService:
    """Run venv-build / model-download / smoke-test wizard steps one at a time."""

    def __init__(
        self,
        cli: CliAdapter,
        *,
        settings_store: SettingsStore | None = None,
        popen_factory: PopenFactory | None = None,
        tree_killer: TreeKiller | None = None,
        stall_seconds: float = 20.0,
    ) -> None:
        self.cli = cli
        self._settings = settings_store or SettingsStore()
        self._popen_factory = popen_factory or cast(PopenFactory, subprocess.Popen)
        self._tree_killer = tree_killer or kill_process_tree
        self._stall_seconds = stall_seconds
        self._lock = threading.RLock()
        self._jobs: dict[str, WizardJob] = {}
        self._active_id: str | None = None
        self._last_successful_smoke_test_profile: str | None = None

    def status(self) -> dict[str, object]:
        """Report whether the wizard should be shown automatically.

        A machine that already has a settings file AND at least one profile
        venv is treated as an existing install even if it predates this
        field (settings.json simply lacks `setup_wizard_completed_at`) - the
        Phase 5c 指示書 explicitly warns against re-showing the wizard to
        people who are already using the product. Only a genuinely fresh
        install (no settings file at all) or a machine with zero profile
        venvs triggers it.
        """
        settings = self._settings.load()
        first_run = self._no_profile_exists() or not self._settings.path.is_file()
        return {"first_run": first_run, "completed_at": settings.setup_wizard_completed_at}

    def complete(self) -> dict[str, object]:
        """Mark the wizard complete - only once a smoke test has actually passed."""
        if self._last_successful_smoke_test_profile is None:
            raise WizardNotReadyError(
                "A smoke test must succeed before the wizard can be marked complete"
            )
        settings = self._settings.load()
        saved = self._settings.save(replace(settings, setup_wizard_completed_at=_now()))
        return saved.to_dict()

    def start_venv_build(self, profile: str) -> dict[str, object]:
        _validate_profile(profile)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.cli.repo_root / "setup.ps1"),
            "-Profile",
            profile,
            "-Yes",
        ]
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        return self._start("venv_build", profile, command, environment)

    def start_model_download(self, profile: str, model_ref: str) -> dict[str, object]:
        _validate_profile(profile)
        if not model_ref.strip():
            raise ValueError("model_ref must not be empty")
        info = self.cli.profile_info(profile)
        if not info.exists:
            raise WizardProfileMissingError(f"Profile venv does not exist yet: {profile}")
        command = [str(info.executable), "models", "download", model_ref]
        return self._start("model_download", profile, command, self.cli.environment(profile))

    def start_smoke_test(
        self,
        profile: str,
        *,
        asr_model_ref: str | None = None,
        diarization_enabled: bool = False,
    ) -> dict[str, object]:
        """Run a real transcribe on synthetic silent audio to prove setup actually works.

        `asr_model_ref` should be the same catalog key (`"<backend>:<model_id>"`)
        the wizard's model-download step just fetched - without it, this falls
        back to the profile's auto-selected backend/model, which can fail if
        that default was never downloaded (confirmed on this machine: the
        `intel` profile auto-selects whisper-cpp/vulkan, but only a
        faster-whisper model had been downloaded here).
        """
        _validate_profile(profile)
        info = self.cli.profile_info(profile)
        if not info.exists:
            raise WizardProfileMissingError(f"Profile venv does not exist yet: {profile}")
        workdir = Path(tempfile.mkdtemp(prefix="utteran-wizard-smoke-"))
        audio_path = workdir / "smoke.wav"
        output_dir = workdir / "output"
        _write_synthetic_wav(audio_path)
        output_dir.mkdir()
        diarization_flags = (
            ["--diarization-backend", "pyannote"] if diarization_enabled else ["--no-diarization"]
        )
        model_flags: list[str] = []
        if asr_model_ref:
            backend, separator, model_id = asr_model_ref.partition(":")
            if separator:
                model_flags = ["--asr-backend", backend, "--asr-model", model_id]
            else:
                model_flags = ["--asr-model", asr_model_ref]
        command = [
            str(info.executable),
            "transcribe",
            str(audio_path),
            "--output-dir",
            str(output_dir),
            "--format",
            "txt",
            *model_flags,
            *diarization_flags,
        ]
        snapshot = self._start("smoke_test", profile, command, self.cli.environment(profile))
        with self._lock:
            job = self._job(str(snapshot["id"]))
            job.cleanup = lambda: shutil.rmtree(workdir, ignore_errors=True)
        return snapshot

    def cancel(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._job(job_id)
            if job.status in TERMINAL_STATUSES:
                return self._snapshot(job)
            job.cancel_requested = True
            process = job.process
        if process is not None and process.poll() is None:
            self._tree_killer(process)
        return self.snapshot(job_id)

    def snapshot(self, job_id: str) -> dict[str, object]:
        with self._lock:
            return self._snapshot(self._job(job_id))

    def events_since(self, job_id: str, cursor: int) -> tuple[list[dict[str, object]], bool]:
        with self._lock:
            job = self._job(job_id)
            events = [dict(event) for event in job.events[cursor:]]
            terminal = job.status in TERMINAL_STATUSES
        return events, terminal

    def _no_profile_exists(self) -> bool:
        return not any(profile.exists for profile in self.cli.profiles())

    def _start(
        self,
        kind: WizardJobKind,
        profile: str,
        command: list[str],
        environment: dict[str, str],
    ) -> dict[str, object]:
        with self._lock:
            if self._active_id is not None:
                active = self._jobs[self._active_id]
                if active.status not in TERMINAL_STATUSES:
                    raise WizardBusyError("Only one setup step can run at a time")
            job_id = uuid.uuid4().hex
            job = WizardJob(job_id, kind, profile, command, environment)
            self._jobs[job_id] = job
            self._active_id = job_id
        threading.Thread(
            target=self._run,
            args=(job_id,),
            name=f"utteran-wizard-{job_id[:8]}",
            daemon=True,
        ).start()
        return self.snapshot(job_id)

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._job(job_id)
            if job.cancel_requested:
                self._finish(job, 130)
                return
            kwargs = build_popen_kwargs(cwd=self.cli.repo_root, env=job.environment)
            try:
                process = self._popen_factory(job.command, **kwargs)
            except Exception as exc:
                self._append_log(job, f"Failed to launch: {exc}", is_error=True)
                self._finish(job, 1)
                return
            job.process = process
            job.status = "running"
            job.started_at = _now()
            job.started_monotonic = time.monotonic()

        stdout_thread = threading.Thread(
            target=self._read_stream,
            args=(job_id, process.stdout),
            name=f"utteran-wizard-stdout-{job_id[:8]}",
            daemon=True,
        )
        stdout_thread.start()
        if process.stderr is not None:
            for line in process.stderr:
                self._handle_line(job_id, line)
        return_code = process.wait()
        stdout_thread.join(timeout=2.0)
        cleanup: Callable[[], None] | None = None
        with self._lock:
            job = self._job(job_id)
            effective_code = 130 if job.cancel_requested else return_code
            self._finish(job, effective_code)
            cleanup = job.cleanup
        if cleanup is not None:
            cleanup()

    def _read_stream(self, job_id: str, stream: Any) -> None:
        if stream is None:
            return
        for line in stream:
            self._handle_line(job_id, line)

    def _handle_line(self, job_id: str, line: str) -> None:
        stripped = line.rstrip("\r\n")
        if not stripped:
            return
        with self._lock:
            job = self._job(job_id)
            if stripped.startswith(STAGE_MARKER):
                stage = stripped[len(STAGE_MARKER) :].strip()
                job.stage = stage
                self._append_event(
                    job,
                    {
                        "schema_version": 1,
                        "timestamp": _now(),
                        "event": "stage_start",
                        "stage": stage,
                    },
                )
                return
            self._append_log(job, stripped)

    def _append_log(self, job: WizardJob, line: str, *, is_error: bool = False) -> None:
        text = mask_secrets(line)
        if not text:
            return
        job.raw_logs.append(text)
        job.last_event_monotonic = time.monotonic()
        self._append_event(
            job,
            {
                "schema_version": 1,
                "timestamp": _now(),
                "event": "error" if is_error else "log",
                "message": text,
            },
        )

    def _finish(self, job: WizardJob, exit_code: int) -> None:
        job.exit_code = exit_code
        job.finished_at = _now()
        job.process = None
        if exit_code == 130:
            job.status = "cancelled"
        elif exit_code == 0:
            job.status = "completed"
            if job.kind == "smoke_test":
                self._last_successful_smoke_test_profile = job.profile
        else:
            job.status = "failed"
        self._append_event(
            job,
            {
                "schema_version": 1,
                "timestamp": _now(),
                "event": "done",
                "exit_code": exit_code,
                "duration_seconds": _duration(job),
            },
        )
        if self._active_id == job.id:
            self._active_id = None

    def _append_event(self, job: WizardJob, event: dict[str, object]) -> None:
        event = dict(event)
        event["id"] = len(job.events)
        job.events.append(event)
        job.last_event_monotonic = time.monotonic()

    def _snapshot(self, job: WizardJob) -> dict[str, object]:
        stalled = (
            job.status == "running"
            and time.monotonic() - job.last_event_monotonic >= self._stall_seconds
        )
        return {
            "id": job.id,
            "kind": job.kind,
            "profile": job.profile,
            "status": job.status,
            "stage": job.stage,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "exit_code": job.exit_code,
            "stalled": stalled,
            "events": [dict(event) for event in job.events],
            "logs": list(job.raw_logs),
            "guidance": guidance_for(job.exit_code, job.raw_logs, job.events),
        }

    def _job(self, job_id: str) -> WizardJob:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise WizardUnknownJobError(job_id) from None


def _validate_profile(profile: str) -> None:
    if profile not in PROFILE_NAMES:
        raise ValueError(f"Unknown profile: {profile}")


def _write_synthetic_wav(path: Path, *, seconds: float = 2.0, sample_rate: int = 16000) -> None:
    """Generate a silent mono WAV - never real audio - for the completion smoke test."""
    frame_count = int(seconds * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frame_count)


def _duration(job: WizardJob) -> float:
    if job.started_monotonic is None:
        return 0.0
    return max(0.0, time.monotonic() - job.started_monotonic)


def _now() -> str:
    return datetime.now(UTC).isoformat()
