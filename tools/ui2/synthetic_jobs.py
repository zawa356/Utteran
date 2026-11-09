"""Synthetic profile CLI for transcription jobs in the UI E2E harness (UI-3 S3-A).

The *real* ``JobManager`` and ``OperationQueue`` run on top of ``SyntheticJobCli``: only
``build_transcribe_command`` is replaced, and it launches ``synthetic_transcribe.py`` (no model
download, no ASR, no file written). ``POST /api/jobs``, ``GET /api/jobs/{id}``, ``/cancel``,
``/events`` and ``/api/queue`` are therefore the production code paths, including the dynamic
environment validation against the synthetic ``EnvironmentService``.

An input *name* containing ``reject`` makes the command builder raise ``CliError`` with a
path in its message (like a missing profile), so the API answers 400 and the UI must not show
the server text. Nothing here is imported by the product.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from utteran_gui.cli import CliError, TranscriptionOptions  # noqa: E402
from utteran_gui.jobs import JobManager  # noqa: E402
from utteran_gui.operation_queue import OperationQueue  # noqa: E402

SCRIPT = Path(__file__).resolve().with_name("synthetic_transcribe.py")


class SyntheticJobCli:
    """The part of ``CliAdapter`` that ``JobManager`` uses (``repo_root`` + command builder)."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.commands: list[list[str]] = []

    def build_transcribe_command(
        self, options: TranscriptionOptions
    ) -> tuple[list[str], dict[str, str]]:
        name = PurePath(options.input_path.replace("\\", "/")).name
        if "reject" in name:
            raise CliError(f"Profile is not available: {self.repo_root / 'synthetic-profile'}")
        if options.diarization_enabled:
            # S3-A must always send diarization_enabled=false; fail loudly if it does not.
            raise CliError("synthetic harness: diarization must be disabled in S3-A")
        command = [
            sys.executable,
            "-I",
            str(SCRIPT),
            options.input_path,
            options.output_dir,
            ",".join(options.formats),
        ]
        self.commands.append(command)
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        return command, environment


def synthetic_job_manager(repo_root: Path) -> JobManager:
    return JobManager(SyntheticJobCli(repo_root), operation_queue=OperationQueue())  # type: ignore[arg-type]
