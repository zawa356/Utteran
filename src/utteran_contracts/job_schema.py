"""Deterministic offline B7 job/queue schema. Importing this module performs no I/O."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from utteran_contracts.jobs import PUBLIC_MODELS
from utteran_contracts.schema import normalize_literals


def schema_bytes() -> bytes:
    _, schema = models_json_schema([(model, "validation") for model in PUBLIC_MODELS])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Utteran B7 v1 job and queue wire contracts (opt-in, experimental)"
    schema["oneOf"] = [{"$ref": f"#/$defs/{model.__name__}"} for model in PUBLIC_MODELS]
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
            print("job schema is missing or stale", file=sys.stderr)
            return 1
    else:
        args.output.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
