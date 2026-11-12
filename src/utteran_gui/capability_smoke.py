"""Explicit, development-only B4 smoke launcher (0.1.50).

    python -m utteran_gui.capability_smoke --profile cpu --recipe fw-cpu --model tiny \
        < settings.json

Never imported by GUI startup, ``create_app()``, any HTTP route, B4 refresh/resolve or B7.
It runs one explicit smoke in a separate one-shot profile child
(``python -I -B -m utteran.capabilities.smoke run``) and prints a fixed code only.

Preconditions (fixed refusal codes, exit status 2):

- not a distributed/frozen build, and ``UTTERAN_DATA_ROOT`` names an existing, already
  G-09-marked dedicated development root (a marker is never created here);
- the per-data-root GUI instance lock is free: a running GUI or another smoke on the same
  root is refused. Other roots are independent. The CLI and older binaries are not
  excluded (same scope as G-09).

Like the rest of ``utteran_gui`` this module never imports core ``utteran``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from pydantic import ValidationError

from utteran_contracts import ProcessingSettings
from utteran_gui.capability_inventory import Runner
from utteran_gui.capability_transport import BridgeReadError, bridge_environment, read_bridge
from utteran_gui.cli import CliAdapter, CliError
from utteran_gui.instance_guard import (
    GuiInstanceLock,
    StartupRefused,
    _is_distribution,
    lock_directory,
    validate_dev_data_root,
)
from utteran_paths import (
    DATA_ROOT_ENV,
    EVIDENCE_TEMP_PATTERN,
    DataPaths,
    is_marked_dev_root,
)

SMOKE_MODULE = "utteran.capabilities.smoke"
TIMEOUT_DEFAULT = 900.0
TIMEOUT_MAX = 3600.0
SETTINGS_LIMIT = 1024 * 1024
PROFILES = ("cpu", "cuda", "intel", "vulkan")
RECIPES = ("fw-cpu", "fw-cuda")

# Child outcomes we are prepared to show (utteran.capabilities.smoke.SmokeOutcome).
# Anything else is reported as smoke_invalid, never echoed.
CHILD_OUTCOMES = frozenset(
    {
        "passed",
        "route_unverified",
        "route_mismatch",
        "compute_mismatch",
        "runtime_mismatch",
        "request_invalid",
        "profile_mismatch",
        "dev_root_invalid",
        "settings_unsupported",
        "recipe_unavailable",
        "profile_incompatible",
        "model_missing",
        "model_load_failed",
        "backend_unavailable",
        "out_of_memory",
        "inference_failed",
        "evidence_write_failed",
        "evidence_limit",
        "internal_error",
    }
)
_TRANSPORT_CODES = {
    "probe_timeout": "smoke_timeout",
    "bridge_stdout_limit": "smoke_output_limit",
    "bridge_stderr_limit": "smoke_output_limit",
}
_TEMP_NAME = re.compile(EVIDENCE_TEMP_PATTERN)


class SmokeLauncher:
    """Starts the profile child through the existing bounded transport (read_bridge):
    Windows Job Object / POSIX session, read-time output limits, timeout with
    descendant termination and wait. The runner is a test seam only."""

    def __init__(self, cli: CliAdapter, *, runner: Runner = read_bridge) -> None:
        self.cli = cli
        self.runner = runner

    def run(
        self,
        *,
        root: Path,
        profile: str,
        recipe: str,
        model: str,
        settings: ProcessingSettings,
        timeout: float,
        environ: Mapping[str, str] | None = None,
    ) -> str:
        if not math.isfinite(timeout) or not 0 < timeout <= TIMEOUT_MAX:
            return "smoke_request_invalid"
        try:
            info = self.cli.profile_info(profile)
        except CliError:
            return "profile_missing"
        python = info.path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not info.exists or not python.is_file():
            return "profile_missing"
        source = dict(os.environ if environ is None else environ)
        source[DATA_ROOT_ENV] = str(root)
        work = Path(tempfile.mkdtemp(prefix="utteran-smoke-"))
        try:
            body = json.dumps(
                {
                    "schema_version": 1,
                    "profile_id": profile,
                    "recipe_id": recipe,
                    "model_id": model,
                    "settings": settings.to_wire(),
                    "work_dir": str(work),
                }
            ).encode("utf-8")
            completed = self.runner(
                [str(python.absolute()), "-I", "-B", "-m", SMOKE_MODULE, "run"],
                cwd=self.cli.repo_root,
                env=bridge_environment(profile, source),
                input=body,
                timeout=timeout,
            )
            return _outcome(completed.stdout)
        except BridgeReadError as exc:
            return _TRANSPORT_CODES.get(exc.code, "smoke_unavailable")
        except subprocess.TimeoutExpired:
            return "smoke_timeout"
        except (CliError, OSError, subprocess.SubprocessError):
            return "smoke_unavailable"
        finally:
            shutil.rmtree(work, ignore_errors=True)
            _remove_writer_temporaries(DataPaths(root=root, project_root=root))


def _outcome(stdout: bytes) -> str:
    try:
        payload: Any = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, ValueError):
        return "smoke_invalid"
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "outcome", "evidence"}
        or payload["schema_version"] != 1
        or payload["outcome"] not in CHILD_OUTCOMES
        or payload["evidence"] not in {"none", "recorded", "invalidated"}
    ):
        return "smoke_invalid"
    return str(payload["outcome"])


def _remove_writer_temporaries(paths: DataPaths) -> None:
    """After the child is reaped, delete only an interrupted writer's temp files."""
    directory = paths.capability_evidence
    if directory is None or directory.is_symlink() or not directory.is_dir():
        return
    for entry in os.scandir(directory):
        if _TEMP_NAME.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
            with contextlib.suppress(OSError):
                os.unlink(entry.path)


def development_root(environ: Mapping[str, str]) -> Path:
    """Like ``--next-ui-v1`` but stricter: the marker must already exist."""
    if _is_distribution():
        raise StartupRefused("dev_root_distribution")
    configured = environ.get(DATA_ROOT_ENV, "")
    candidate = Path(configured).expanduser() if configured.strip() else None
    if candidate is not None and candidate.is_absolute():
        try:
            real = Path(os.path.realpath(candidate))
            exists = real.is_dir()
        except (OSError, ValueError):
            exists = False
        if exists and not is_marked_dev_root(real):
            raise StartupRefused("dev_root_not_marked")
    return validate_dev_data_root(dict(environ))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m utteran_gui.capability_smoke",
        description="Development only: run one explicit B4 smoke and record private evidence.",
    )
    parser.add_argument("--profile", required=True, choices=PROFILES)
    parser.add_argument("--recipe", required=True, choices=RECIPES)
    parser.add_argument("--model", required=True)
    parser.add_argument("--timeout", type=float, default=TIMEOUT_DEFAULT)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    stdin: BinaryIO | None = None,
    stdout: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    launcher_factory: Callable[[CliAdapter], SmokeLauncher] = SmokeLauncher,
    repo_root: Path | None = None,
) -> int:
    out = stdout or sys.stdout
    args = _parser().parse_args(argv)
    env = dict(os.environ if environ is None else environ)

    def report(code: str) -> None:
        # Fixed code only: no path, digest, settings, stderr or transcript.
        out.write(f"utteran capability smoke: [{code}]\n")

    try:
        root = development_root(env)
        lock = GuiInstanceLock(lock_directory(root)).acquire()
    except StartupRefused as refused:
        report(refused.code)
        return 2
    try:
        raw = (stdin or sys.stdin.buffer).read(SETTINGS_LIMIT + 1)
        try:
            if len(raw) > SETTINGS_LIMIT:
                raise ValueError("settings too large")
            settings = ProcessingSettings.model_validate_json(raw)
        except (ValidationError, ValueError, UnicodeError):
            report("smoke_request_invalid")
            return 2
        env[DATA_ROOT_ENV] = str(root)
        cli = CliAdapter(
            (repo_root or Path.cwd()).resolve(),
            DataPaths(root=root, project_root=root).venvs,
        )
        code = launcher_factory(cli).run(
            root=root,
            profile=args.profile,
            recipe=args.recipe,
            model=args.model,
            settings=settings,
            timeout=args.timeout,
            environ=env,
        )
    finally:
        lock.release()
    report(code)
    return 0 if code == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
