"""Explicit read-only B4 adapter; never called during normal GUI startup."""

from __future__ import annotations

import json
import platform
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePath
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from utteran_contracts.capabilities import (
    InventoryRefreshRequest,
    InventoryRefreshResult,
    RecipeCatalog,
    ResolvedSelection,
)
from utteran_contracts.settings import Policy
from utteran_gui.capability_transport import (
    STDOUT_LIMIT,
    BridgeReadError,
    bridge_environment,
    read_bridge,
)
from utteran_gui.cli import CliAdapter, CliError

# One short-lived metadata reader, with no children, inference imports or probes.
Runner = Callable[..., subprocess.CompletedProcess[bytes]]


@dataclass(frozen=True)
class BridgeResolution:
    """Private result of the profile-side refresh+resolve (B7). Never log or publish."""

    reason_code: str
    selection: ResolvedSelection | None
    worker_models: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


class CapabilityInventoryAdapter:
    def __init__(self, cli: CliAdapter, *, runner: Runner = read_bridge) -> None:
        self.cli = cli
        self.runner = runner

    def _read(self, profile: str, action: str, request: bytes | None, timeout: float) -> Any:
        if action not in {"recipes", "refresh", "resolve"}:
            raise BridgeReadError("bridge_invalid")
        info = self.cli.profile_info(profile)
        python = info.path / (
            "Scripts/python.exe" if platform.system() == "Windows" else "bin/python"
        )
        if not info.exists or not python.is_file():
            raise BridgeReadError("profile_missing")
        completed = self.runner(
            [str(python.absolute()), "-I", "-B", "-m", "utteran.capabilities.bridge", action],
            cwd=self.cli.repo_root,
            env=bridge_environment(profile),
            input=request if request is not None else b"",
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise BridgeReadError("bridge_unavailable")
        if len(completed.stdout) > STDOUT_LIMIT:
            raise BridgeReadError("bridge_stdout_limit")
        try:
            return json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise BridgeReadError("bridge_invalid") from None

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
        except BridgeReadError as exc:
            code = exc.code
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

    def resolve(
        self, request: InventoryRefreshRequest, policy: Policy, *, timeout: float = 30.0
    ) -> BridgeResolution:
        """Refresh and resolve in the profile. Scope is re-checked; failures are fixed codes."""
        body = json.dumps({"refresh": request.to_wire(), "policy": policy.to_wire()})
        try:
            payload = self._read(request.profile_id, "resolve", body.encode("utf-8"), timeout)
            if not isinstance(payload, dict) or set(payload) != {
                "schema_version",
                "reason_code",
                "selection",
                "worker_models",
            }:
                raise ValueError
            if payload["schema_version"] != 1 or not isinstance(payload["reason_code"], str):
                raise ValueError
            if payload["selection"] is None:
                return BridgeResolution(_known(payload["reason_code"]), None)
            selection = ResolvedSelection.model_validate(payload["selection"])
            models = payload["worker_models"]
            chosen = (selection.selected, *selection.preapproved) if selection.selected else ()
            ids = {candidate.id for candidate in chosen}
            if (
                selection.requested_settings != request.settings
                or selection.inventory_revision != request.revision
                or selection.policy != policy
                or any(
                    a.candidate.deployment.identity.profile_id != request.profile_id
                    for a in selection.alternatives
                )
                or any(c.deployment.identity.profile_id != request.profile_id for c in chosen)
                or not isinstance(models, dict)
                or not set(models) <= ids
                or not all(
                    isinstance(v, str) and PurePath(v).is_absolute() for v in models.values()
                )
            ):
                return BridgeResolution("profile_scope_mismatch", None)
            return BridgeResolution("supported", selection, MappingProxyType(dict(models)))
        except BridgeReadError as exc:
            return BridgeResolution(exc.code, None)
        except subprocess.TimeoutExpired:
            return BridgeResolution("probe_timeout", None)
        except (ValidationError, ValueError, TypeError, KeyError):
            return BridgeResolution("bridge_invalid", None)
        except (CliError, OSError, subprocess.SubprocessError):
            return BridgeResolution("bridge_unavailable", None)


def _known(code: str) -> str:
    return code if code in {"bridge_invalid", "probe_error"} else "bridge_invalid"
