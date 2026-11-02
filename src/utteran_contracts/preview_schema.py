"""Deterministic B5 schemas; previous artifacts remain unchanged."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from utteran_contracts.preview import PolicyList, PresetList, ValidationResult
from utteran_contracts.schema import contract_schema, normalize_literals


def schema_bytes() -> bytes:
    models = (PresetList, PolicyList, ValidationResult)
    _, schema = models_json_schema([(model, "validation") for model in models])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Utteran B5 private settings preview and collections"
    schema["oneOf"] = [{"$ref": f"#/$defs/{model.__name__}"} for model in models]
    b1 = contract_schema()["$defs"]
    for name in schema["$defs"]:
        if name in b1:
            schema["$defs"][name] = b1[name]
    normalize_literals(schema)
    return (json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = schema_bytes()
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != content:
            print("preview schema is missing or stale", file=sys.stderr)
            return 1
    else:
        args.output.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
