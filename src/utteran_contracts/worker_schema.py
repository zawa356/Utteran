"""Deterministic offline B3 schemas, independent from B1's artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from utteran_contracts.schema import normalize_literals
from utteran_contracts.worker import (
    WorkerAccepted,
    WorkerCancel,
    WorkerCancelAcknowledged,
    WorkerCompleted,
    WorkerError,
    WorkerHello,
    WorkerProgress,
    WorkerRequest,
)

MODELS = (
    WorkerRequest,
    WorkerCancel,
    WorkerHello,
    WorkerAccepted,
    WorkerProgress,
    WorkerCompleted,
    WorkerError,
    WorkerCancelAcknowledged,
)


def schema_bytes() -> bytes:
    _, schema = models_json_schema([(model, "validation") for model in MODELS])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Utteran worker IPC protocol 1 / DTO schema 1"
    schema["oneOf"] = [{"$ref": f"#/$defs/{model.__name__}"} for model in MODELS]
    # Versions are required on the wire, even though local Python constructors
    # offer defaults. Framing enforces this before DTO validation.
    for definition in schema["$defs"].values():
        properties = definition.get("properties", {})
        if "protocol_version" in properties:
            definition["required"] = sorted(
                set(definition.get("required", [])) | {"schema_version", "protocol_version"}
            )
    normalize_literals(schema)
    return (json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


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
            print("worker schema is missing or stale", file=sys.stderr)
            return 1
    elif args.output:
        args.output.write_bytes(content)
    else:
        sys.stdout.buffer.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
