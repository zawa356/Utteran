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
from utteran_contracts.capabilities import InventoryRefreshRequest, InventoryRefreshResult

REQUEST_LIMIT = 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("recipes", "refresh"))
    args = parser.parse_args()
    if args.action == "recipes":
        payload = recipe_catalog().to_wire()
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
