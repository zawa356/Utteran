"""Deterministic offline B4 artifact; B1/B3 schema bytes stay unchanged."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic.json_schema import models_json_schema

from utteran_contracts.capabilities import (
    InventoryRefreshRequest,
    InventoryRefreshResult,
    InventorySnapshot,
    RecipeCatalog,
    ResolvedSelection,
)
from utteran_contracts.schema import normalize_literals

MODELS = (
    RecipeCatalog,
    InventorySnapshot,
    ResolvedSelection,
    InventoryRefreshRequest,
    InventoryRefreshResult,
)


def schema_bytes() -> bytes:
    _, schema = models_json_schema([(model, "validation") for model in MODELS])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Utteran B4 capability inventory / preparation result (schema 1)"
    schema["oneOf"] = [{"$ref": f"#/$defs/{model.__name__}"} for model in MODELS]
    from utteran_contracts.schema import contract_schema

    b1 = contract_schema()["$defs"]
    for name in ("Policy", "PolicyWrite"):
        if name in schema["$defs"]:
            schema["$defs"][name] = b1[name]
    normalize_literals(schema)
    _sort_enums(schema)
    return (json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _sort_enums(value: Any) -> None:
    # Python may intern equivalent Literal types with a different source order
    # when core Config is imported first. Enum order has no JSON Schema meaning.
    if isinstance(value, dict):
        if isinstance(value.get("enum"), list):
            value["enum"] = sorted(value["enum"], key=lambda item: json.dumps(item))
        for child in value.values():
            _sort_enums(child)
    elif isinstance(value, list):
        for child in value:
            _sort_enums(child)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check and args.output is None:
        parser.error("--check requires --output")
    content = schema_bytes()
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != content:
            print("capability schema is missing or stale", file=sys.stderr)
            return 1
    elif args.output is not None:
        args.output.write_bytes(content)
    else:
        sys.stdout.buffer.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
