"""Subprocess-only adapter for profile-local utteran executables."""

from __future__ import annotations

import json
import os
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from utteran_gui.security import mask_secrets, sanitize_json

PROFILE_NAMES = ("cpu", "cuda", "intel", "vulkan")
OUTPUT_FORMATS = ("srt", "vtt", "json", "txt", "md")
ResumeMode = Literal["resume", "fresh", "force"]


class CliError(RuntimeError):
    """A profile CLI could not be launched or returned invalid JSON."""


@dataclass(frozen=True)
class ProfileInfo:
    name: str
    path: Path
    exists: bool
    executable: Path
    updated_at: str | None = None


@dataclass(frozen=True)
class TranscriptionOptions:
    input_path: str
    output_dir: str
    profile: str
    asr_backend: str
    asr_model: str
    asr_device: str
    diarization_enabled: bool = True
    diarization_backend: str = "pyannote"
    diarization_model: str = ""
    diarization_device: str = "cpu"
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    language: str = "ja"
    formats: tuple[str, ...] = ("srt", "json", "md")
    resume_mode: ResumeMode = "resume"
    recursive: bool = False
    include: tuple[str, ...] = field(default_factory=tuple)
    exclude: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RegenerationOptions:
    """Export-only changes applied to one persistent core job."""

    job_id: str
    profile: str
    output_dir: str
    formats: tuple[str, ...]
    speaker_labels: dict[str, str] = field(default_factory=dict)


class CliAdapter:
    """Resolve profile environments and invoke only their console executable."""

    def __init__(self, repo_root: Path, venv_root: Path | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.venv_root = (venv_root or self.repo_root / ".venvs").resolve()

    @property
    def os_slug(self) -> str:
        return "win" if platform.system() == "Windows" else "linux"

    def profile_info(self, profile: str) -> ProfileInfo:
        if profile not in PROFILE_NAMES:
            raise CliError(f"Unknown profile: {profile}")
        root = self.venv_root / f"{self.os_slug}-{profile}"
        executable = (
            root / "Scripts" / "utteran.exe"
            if platform.system() == "Windows"
            else root / "bin" / "utteran"
        )
        try:
            updated = root.stat().st_mtime if root.is_dir() else None
        except OSError:
            updated = None
        return ProfileInfo(
            profile,
            root,
            root.is_dir() and executable.is_file(),
            executable,
            None if updated is None else str(updated),
        )

    def profiles(self) -> tuple[ProfileInfo, ...]:
        return tuple(self.profile_info(profile) for profile in PROFILE_NAMES)

    def environment(self, profile: str) -> dict[str, str]:
        environment = dict(os.environ)
        environment["UTTERAN_PROFILE"] = profile
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        return environment

    def run_json(
        self,
        profile: str,
        arguments: list[str],
        *,
        timeout: float = 60.0,
    ) -> object:
        info = self.profile_info(profile)
        if not info.exists:
            raise CliError(f"Profile is not available: {profile}")
        completed = subprocess.run(
            [str(info.executable), *arguments],
            cwd=self.repo_root,
            env=self.environment(profile),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0:
            detail = mask_secrets(completed.stderr.strip() or completed.stdout.strip())
            raise CliError(f"CLI exited {completed.returncode}: {detail[:1000]}")
        try:
            return sanitize_json(json.loads(completed.stdout))
        except json.JSONDecodeError as exc:
            raise CliError(f"CLI returned invalid JSON: {mask_secrets(str(exc))}") from None

    def build_transcribe_command(
        self,
        options: TranscriptionOptions,
    ) -> tuple[list[str], dict[str, str]]:
        """Build a shell-free argument vector for one GUI request."""
        info = self.profile_info(options.profile)
        if not info.exists:
            raise CliError(f"Profile is not available: {options.profile}")
        if not options.input_path.strip():
            raise CliError("Input path is required")
        if not options.output_dir.strip():
            raise CliError("Output directory is required")
        formats = tuple(dict.fromkeys(item.lower() for item in options.formats))
        if not formats or any(item not in OUTPUT_FORMATS for item in formats):
            raise CliError("At least one supported output format is required")
        arguments = [
            str(info.executable),
            "transcribe",
            options.input_path,
            "--output-dir",
            options.output_dir,
            "--asr-backend",
            options.asr_backend,
            "--asr-model",
            options.asr_model,
            "--asr-device",
            options.asr_device,
            "--language",
            options.language,
            "--format",
            ",".join(formats),
            "--progress-json",
            "--quiet",
        ]
        if options.diarization_enabled:
            arguments.extend(
                [
                    "--diarization-backend",
                    options.diarization_backend,
                    "--diarization-model",
                    options.diarization_model,
                    "--diarization-device",
                    options.diarization_device,
                ]
            )
            if options.num_speakers is not None:
                arguments.extend(["--num-speakers", str(options.num_speakers)])
            else:
                if options.min_speakers is not None:
                    arguments.extend(["--min-speakers", str(options.min_speakers)])
                if options.max_speakers is not None:
                    arguments.extend(["--max-speakers", str(options.max_speakers)])
        else:
            arguments.append("--no-diarization")
        if options.resume_mode == "fresh":
            arguments.append("--no-resume")
        elif options.resume_mode == "force":
            arguments.append("--force")
        if options.recursive:
            arguments.append("--recursive")
        for pattern in options.include:
            arguments.extend(["--include", pattern])
        for pattern in options.exclude:
            arguments.extend(["--exclude", pattern])
        return arguments, self.environment(options.profile)

    def list_jobs(self, profile: str) -> object:
        """Read the shared job history through the core JSON contract."""
        return self.run_json(profile, ["jobs", "list", "--json"])

    def show_job(self, profile: str, job_id: str) -> object:
        """Read one normalized viewer payload without importing the core package."""
        return self.run_json(profile, ["jobs", "show", job_id, "--json"])

    def delete_job(self, profile: str, job_id: str) -> object:
        """Delete exactly one core job after the GUI has confirmed its size."""
        return self.run_json(
            profile,
            ["jobs", "clean", "--job-id", job_id, "--yes", "--json"],
        )

    def regenerate(self, options: RegenerationOptions) -> object:
        """Run only export from merged.json with shell-free label arguments."""
        formats = tuple(dict.fromkeys(item.lower() for item in options.formats))
        if not formats or any(item not in OUTPUT_FORMATS for item in formats):
            raise CliError("At least one supported output format is required")
        if not options.output_dir.strip():
            raise CliError("Output directory is required")
        arguments = [
            "jobs",
            "export",
            options.job_id,
            "--output-dir",
            options.output_dir,
            "--format",
            ",".join(formats),
        ]
        for speaker, display_name in options.speaker_labels.items():
            arguments.extend(["--speaker-label", f"{speaker}={display_name}"])
        arguments.append("--json")
        return self.run_json(options.profile, arguments, timeout=300.0)


def as_json_dict(value: object) -> dict[str, Any]:
    """Narrow a CLI JSON response to a mapping for environment composition."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}
