"""First-run setup wizard orchestration: venv build, model download, smoke test.

Runs `setup.ps1` and the resulting profile's `utteran.exe` as tracked
subprocess jobs the GUI can stream progress from and cancel, using the same
single-job-at-a-time / event-cursor / process-tree-kill shape as
`utteran_gui.jobs.JobManager` (shared via `utteran_gui.processes` so the two
never diverge). This module still never imports `utteran` or any inference
package - it only launches other processes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from utteran_gui.cli import CliAdapter
from utteran_gui.jobs import guidance_for
from utteran_gui.operation_queue import OperationQueue, QueueStatus
from utteran_gui.processes import PopenFactory, TreeKiller, build_popen_kwargs, kill_process_tree
from utteran_gui.security import mask_secrets
from utteran_gui.settings import PROFILE_NAMES, WIZARD_EXECUTION_STAGES, WIZARD_STEPS, SettingsStore

WizardJobKind = Literal["venv_build", "model_download", "model_action", "smoke_test"]
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
    model_ref: str | None = None
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
        operation_queue: OperationQueue | None = None,
    ) -> None:
        self.cli = cli
        self._settings = settings_store or SettingsStore()
        self._popen_factory = popen_factory or cast(PopenFactory, subprocess.Popen)
        self._tree_killer = tree_killer or kill_process_tree
        self._stall_seconds = stall_seconds
        self.queue = operation_queue or OperationQueue()
        self._lock = threading.RLock()
        self._jobs: dict[str, WizardJob] = {}
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
        step = settings.setup_wizard_step
        completed_stages = list(settings.setup_wizard_completed_stages)
        # A profile is required for every step after the recommendation screen.
        # Older/interrupted frontends could persist execution with profile=null;
        # recover by showing the proven hardware recommendation UX again instead
        # of sending an invalid job that can only fail with HTTP 422.
        if settings.setup_wizard_profile is None and step not in {"welcome", "profile"}:
            step = "profile"
            completed_stages = []
        # Token errors are meaningful only after the selected profile has
        # actually run its preflight. Older interrupted runs could leave an
        # error behind after their profile/stages were reset, which made a new
        # wizard session claim that a configured token had just been rejected.
        token_error = settings.setup_wizard_token_error
        profile_ready = (
            settings.setup_wizard_profile is not None
            and self.cli.profile_info(settings.setup_wizard_profile).exists
        )
        if "venv" not in completed_stages or not profile_ready:
            token_error = None
        persisted_in_progress = (
            settings.setup_wizard_completed_at is None and settings.setup_wizard_step != "welcome"
        )
        first_run = (
            self._no_profile_exists()
            or persisted_in_progress
            or (
                settings.setup_wizard_completed_at is None
                and (
                    settings.setup_wizard_started_at is not None
                    or not self._settings.path.is_file()
                )
            )
        )
        return {
            "first_run": first_run,
            "resume_available": (
                settings.setup_wizard_completed_at is None
                and (settings.setup_wizard_started_at is not None or persisted_in_progress)
            ),
            "started_at": settings.setup_wizard_started_at,
            "completed_at": settings.setup_wizard_completed_at,
            "step": step,
            "profile": settings.setup_wizard_profile,
            "diarization_enabled": settings.setup_wizard_diarization_enabled,
            "model_ref": settings.setup_wizard_model_ref,
            "completed_stages": completed_stages,
            "token_error": token_error,
        }

    def start(self) -> dict[str, object]:
        """Persist that a fresh/manual wizard flow has begun."""
        current = self._settings.load()
        if current.setup_wizard_completed_at is not None:
            changes: dict[str, object] = {
                "setup_wizard_started_at": _now(),
                "setup_wizard_completed_at": None,
                "setup_wizard_step": "profile",
                "setup_wizard_profile": None,
                "setup_wizard_diarization_enabled": None,
                "setup_wizard_completed_stages": [],
                "setup_wizard_token_error": None,
            }
        elif current.setup_wizard_started_at is None:
            changes = {
                "setup_wizard_started_at": _now(),
                "setup_wizard_step": "profile",
                "setup_wizard_token_error": None,
            }
        else:
            return current.to_dict()
        return self._settings.update(changes).to_dict()

    def save_state(
        self,
        step: str,
        *,
        profile: str | None = None,
        diarization_enabled: bool | None = None,
        model_ref: str | None = None,
        token_error: str | None = None,
    ) -> dict[str, object]:
        """Persist non-secret wizard input so the UI can resume after restart."""
        if step not in WIZARD_STEPS:
            raise ValueError(f"Unknown wizard step: {step}")
        current = self._settings.load()
        effective_profile = profile or current.setup_wizard_profile
        if effective_profile is None and step not in {"welcome", "profile"}:
            step = "profile"
        changes: dict[str, object] = {"setup_wizard_step": step}
        if profile is not None:
            _validate_profile(profile)
            changes["setup_wizard_profile"] = profile
            if profile != current.setup_wizard_profile:
                changes["setup_wizard_completed_stages"] = []
                changes["setup_wizard_token_error"] = None
        elif effective_profile is None:
            changes["setup_wizard_completed_stages"] = []
        if diarization_enabled is not None:
            changes["setup_wizard_diarization_enabled"] = diarization_enabled
        if model_ref is not None:
            if not model_ref.strip():
                raise ValueError("model_ref must not be empty")
            changes["setup_wizard_model_ref"] = model_ref
        if token_error is not None:
            if token_error not in {
                "token_missing",
                "token_invalid",
                "agreement_required",
                "network_error",
            }:
                raise ValueError(f"Unknown token error: {token_error}")
            changes["setup_wizard_token_error"] = token_error
        else:
            changes["setup_wizard_token_error"] = None
        return self._settings.update(changes).to_dict()

    def record_preflight(self, access: str) -> None:
        """Persist a successful preflight or route a classified failure back to token input."""
        if access == "available":
            self._record_stage("preflight")
            self._settings.update({"setup_wizard_token_error": None})
            return
        error = (
            access
            if access in {"token_missing", "token_invalid", "agreement_required", "network_error"}
            else "network_error"
        )
        current = self._settings.load()
        completed = [
            stage for stage in current.setup_wizard_completed_stages if stage != "preflight"
        ]
        self._settings.update(
            {
                "setup_wizard_step": "token",
                "setup_wizard_completed_stages": completed,
                "setup_wizard_token_error": error,
            }
        )

    def complete(self) -> dict[str, object]:
        """Mark the wizard complete - only once a smoke test has actually passed."""
        current = self._settings.load()
        if current.setup_wizard_completed_at is not None:
            return current.to_dict()
        if self._last_successful_smoke_test_profile is None:
            raise WizardNotReadyError(
                "A smoke test must succeed before the wizard can be marked complete"
            )
        saved = self._settings.update({"setup_wizard_completed_at": _now()})
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
        command = self.cli.command(profile, ["models", "download", model_ref, "--progress-json"])
        return self._start(
            "model_download",
            profile,
            command,
            self.cli.environment(profile),
            model_ref=model_ref,
        )

    def start_model_action(self, profile: str, action: str, model_ref: str) -> dict[str, object]:
        """Run an explicit model-management operation with cancellation support."""
        _validate_profile(profile)
        if not model_ref.strip():
            raise ValueError("model_ref must not be empty")
        info = self.cli.profile_info(profile)
        if not info.exists:
            raise WizardProfileMissingError(f"Profile venv does not exist yet: {profile}")
        commands = {
            "download": ["models", "download", model_ref, "--progress-json"],
            "remove": ["models", "remove", model_ref, "--yes"],
            "verify": ["models", "verify", model_ref],
            "prepare_openvino": ["models", "prepare-openvino", model_ref, "--yes"],
            "remove_openvino": ["models", "remove-openvino", model_ref, "--yes"],
        }
        arguments = commands.get(action)
        if arguments is None:
            raise ValueError(f"Unknown model action: {action}")
        return self._start(
            "model_action",
            profile,
            self.cli.command(profile, arguments),
            self.cli.environment(profile),
            model_ref=model_ref,
        )

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
        command = self.cli.command(
            profile,
            [
                "transcribe",
                str(audio_path),
                "--output-dir",
                str(output_dir),
                "--format",
                "txt",
                *model_flags,
                *diarization_flags,
            ],
        )
        return self._start(
            "smoke_test",
            profile,
            command,
            self.cli.environment(profile),
            cleanup=lambda: shutil.rmtree(workdir, ignore_errors=True),
        )

    def cancel(self, job_id: str) -> dict[str, object]:
        self.queue.cancel(job_id)
        return self.snapshot(job_id)

    def shutdown(self) -> None:
        """Cancel every non-terminal setup/model process during GUI shutdown."""
        with self._lock:
            job_ids = [job.id for job in self._jobs.values() if job.status not in TERMINAL_STATUSES]
        for job_id in job_ids:
            self._cancel_direct(job_id)

    def _cancel_direct(self, job_id: str) -> None:
        with self._lock:
            job = self._job(job_id)
            if job.status in TERMINAL_STATUSES:
                return
            job.cancel_requested = True
            process = job.process
            if process is None:
                self._finish(job, 130)
                return
        if process is not None and process.poll() is None:
            self._tree_killer(process)

    def _run_queued(self, job_id: str) -> QueueStatus:
        self._run(job_id)
        return cast(QueueStatus, self.snapshot(job_id)["status"])

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
        *,
        cleanup: Callable[[], None] | None = None,
        model_ref: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            job_id = uuid.uuid4().hex
            job = WizardJob(
                job_id, kind, profile, command, environment, model_ref=model_ref, cleanup=cleanup
            )
            self._jobs[job_id] = job
        self.queue.submit(
            job_id,
            kind=kind,
            label=model_ref or kind,
            runner=lambda: self._run_queued(job_id),
            canceller=lambda: self._cancel_direct(job_id),
        )
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
        with self._lock:
            job = self._job(job_id)
            effective_code = 130 if job.cancel_requested else return_code
            self._finish(job, effective_code)

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
            try:
                progress_event = json.loads(stripped)
            except json.JSONDecodeError:
                progress_event = None
            if (
                isinstance(progress_event, dict)
                and progress_event.get("event") == "progress"
                and progress_event.get("schema_version") == 1
            ):
                self._append_event(job, progress_event)
                message = progress_event.get("message")
                if isinstance(message, str) and message:
                    job.raw_logs.append(mask_secrets(message))
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
        cleanup = job.cleanup
        job.cleanup = None
        if cleanup is not None:
            cleanup()
        job.exit_code = exit_code
        job.finished_at = _now()
        job.process = None
        if exit_code == 130:
            job.status = "cancelled"
        elif exit_code == 0:
            job.status = "completed"
            if job.kind == "venv_build":
                self._record_stage("venv")
            elif job.kind == "model_download":
                stage = (
                    "diarization_model"
                    if (job.model_ref or "").startswith("pyannote:")
                    else (
                        "vad_model"
                        if (job.model_ref or "").startswith("whisper-cpp-vad:")
                        else "asr_model"
                    )
                )
                self._record_stage(stage)
            if job.kind == "smoke_test":
                self._last_successful_smoke_test_profile = job.profile
                self._record_stage("smoke")
                self._settings.update(
                    {"setup_wizard_completed_at": _now(), "setup_wizard_step": "done"}
                )
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

    def _record_stage(self, stage: str) -> None:
        if stage not in WIZARD_EXECUTION_STAGES:
            raise ValueError(f"Unknown execution stage: {stage}")
        current = self._settings.load()
        stages = list(current.setup_wizard_completed_stages)
        if stage not in stages:
            stages.append(stage)
            self._settings.update({"setup_wizard_completed_stages": stages})

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
            "guidance": guidance_for(
                job.exit_code,
                job.raw_logs,
                job.events,
                operation=job.kind,
            ),
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
