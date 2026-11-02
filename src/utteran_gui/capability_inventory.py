"""Explicit read-only B4 adapter; never called during normal GUI startup."""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from utteran_contracts.capabilities import (
    InventoryRefreshRequest,
    InventoryRefreshResult,
    RecipeCatalog,
)
from utteran_gui.cli import CliAdapter, CliError

# One short-lived metadata reader, with no children, inference imports or probes.
Runner = Callable[..., subprocess.CompletedProcess[bytes]]


class CapabilityInventoryAdapter:
    def __init__(self, cli: CliAdapter, *, runner: Runner = subprocess.run) -> None:
        self.cli = cli
        self.runner = runner

    def _read(self, profile: str, action: str, request: bytes | None, timeout: float) -> Any:
        info = self.cli.profile_info(profile)
        python = info.path / (
            "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
        )
        if not info.exists or not python.is_file():
            raise CliError("profile_missing")
        completed = self.runner(
            [str(python.absolute()), "-I", "-B", "-m", "utteran.capabilities.bridge", action],
            cwd=self.cli.repo_root,
            env=self.cli.environment(profile),
            input=request if request is not None else b"",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode != 0 or len(completed.stdout) > 8 * 1024 * 1024:
            raise CliError("bridge_unavailable")
        return json.loads(completed.stdout)

    def recipes(self, profile: str, *, timeout: float = 20.0) -> RecipeCatalog | None:
        try:
            return RecipeCatalog.model_validate(self._read(profile, "recipes", None, timeout))
        except (CliError, OSError, subprocess.SubprocessError, ValueError):
            return None

    def refresh(
        self, request: InventoryRefreshRequest, *, timeout: float = 20.0
    ) -> InventoryRefreshResult:
        try:
            payload = self._read(
                request.profile_id,
                "refresh",
                json.dumps(request.to_wire()).encode("utf-8"),
                timeout,
            )
            result = InventoryRefreshResult.model_validate(payload)
            if result.inventory is not None and (
                result.inventory.revision != request.revision
                or result.inventory.settings != request.settings
                or result.inventory.execution_path != request.execution_path
                or any(
                    c.deployment.identity.profile_id != request.profile_id
                    for c in result.inventory.candidates
                )
                or any(
                    e.identity.profile_id != request.profile_id for e in result.inventory.exclusions
                )
            ):
                return InventoryRefreshResult(
                    schema_version=1,
                    inventory=None,
                    reason_code="profile_scope_mismatch",
                )
            return result
        except subprocess.TimeoutExpired:
            code = "probe_timeout"
        except ValidationError:
            code = "bridge_invalid"
        except (CliError, OSError, subprocess.SubprocessError, ValueError):
            code = "bridge_unavailable"
        return InventoryRefreshResult.model_validate(
            dict(
                schema_version=1,
                inventory=None,
                reason_code=code,
            )
        )
