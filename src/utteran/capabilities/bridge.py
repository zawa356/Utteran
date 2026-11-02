"""Opt-in read-only profile CLI: python -B -m utteran.capabilities.bridge.

This separate module bypasses legacy CLI logging and probe/cache writes. Private
settings travel only on stdin, never argv. This is not an execution transport.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from pydantic import ValidationError

from utteran.capabilities.collector import refresh_inventory
from utteran.capabilities.recipes import recipe_catalog
from utteran.capabilities.resolver import resolve
from utteran.models.catalog import list_models
from utteran.models.manager import ModelManager
from utteran_contracts import Policy
from utteran_contracts.capabilities import (
    CapabilityCandidate,
    InventoryRefreshRequest,
    InventoryRefreshResult,
    ResolvedSelection,
)
from utteran_paths import resolve_data_paths

REQUEST_LIMIT = 1024 * 1024


def worker_model_dirs(candidates: tuple[CapabilityCandidate, ...]) -> dict[str, str]:
    """Private local model directories for worker-executable faster-whisper candidates.

    Only complete managed copies are reported; no Hub ID, cache lookup or download.
    """
    root = ModelManager(resolve_data_paths().models)
    entries = {entry.model_id: entry for entry in list_models(backend="faster-whisper")}
    result: dict[str, str] = {}
    for candidate in candidates:
        identity = candidate.deployment.identity
        entry = entries.get(identity.model_id)
        if (
            entry is None
            or identity.execution_path != "worker"
            or not identity.local_complete_model
            or identity.model_family != "ctranslate2"
        ):
            continue
        path = root.managed_path(entry)
        if path.is_absolute() and path.is_dir():
            result[candidate.id] = str(path)
    return result


def resolve_payload(raw: bytes) -> dict[str, object]:
    """B7: refresh then resolve inside the profile, so the GUI never imports core."""
    try:
        if len(raw) > REQUEST_LIMIT:
            raise ValueError("request too large")
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"refresh", "policy"}:
            raise ValueError("invalid resolve request")
        request = InventoryRefreshRequest.model_validate(data["refresh"])
        policy = Policy.model_validate(data["policy"])
        now = datetime.now(UTC)
        selection: ResolvedSelection = resolve(refresh_inventory(request, now=now), policy, now=now)
        # Every eligible candidate, so a later re-check can confirm an unselected one too.
        chosen = tuple(a.candidate for a in selection.alternatives if a.eligible)
        return {
            "schema_version": 1,
            "reason_code": "supported",
            "selection": selection.to_wire(),
            "worker_models": worker_model_dirs(chosen),
        }
    except (ValidationError, ValueError, UnicodeError, TypeError, KeyError):
        code = "bridge_invalid"
    except OSError:
        code = "probe_error"
    return {"schema_version": 1, "reason_code": code, "selection": None, "worker_models": {}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("recipes", "refresh", "resolve"))
    args = parser.parse_args()
    if args.action == "recipes":
        payload = recipe_catalog().to_wire()
    elif args.action == "resolve":
        payload = resolve_payload(sys.stdin.buffer.read(REQUEST_LIMIT + 1))
    else:
        raw = sys.stdin.buffer.read(REQUEST_LIMIT + 1)
        try:
            if len(raw) > REQUEST_LIMIT:
                raise ValueError("request too large")
            request = InventoryRefreshRequest.model_validate_json(raw)
            inventory = refresh_inventory(request, now=datetime.now(UTC))
            payload = InventoryRefreshResult(
                schema_version=1,
                inventory=inventory,
                reason_code="supported",
            ).to_wire()
        except (ValidationError, ValueError, UnicodeError, TypeError, KeyError):
            payload = InventoryRefreshResult(
                schema_version=1,
                inventory=None,
                reason_code="bridge_invalid",
            ).to_wire()
        except OSError:
            payload = InventoryRefreshResult(
                schema_version=1,
                inventory=None,
                reason_code="probe_error",
            ).to_wire()
    sys.stdout.buffer.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
