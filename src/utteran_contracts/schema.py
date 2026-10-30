"""Deterministic offline JSON Schema export. Importing this module performs no I/O."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic.json_schema import models_json_schema

from utteran_contracts import (
    ASRSettings,
    DiarizationSettings,
    EditingSession,
    Error,
    ErrorDetail,
    ExtraArguments,
    FasterWhisperSettings,
    FileInput,
    GenAISettings,
    Issue,
    OutputSettings,
    Policy,
    PolicyWrite,
    PostprocessingSettings,
    Preset,
    PresetWrite,
    ProcessingSettings,
    Ref,
    SettingOverride,
    VadSettings,
    WhisperCppSettings,
)
from utteran_contracts.base import Contract
from utteran_contracts.editing import leaf_adapters

MODELS: tuple[type[Contract], ...] = (
    Ref,
    Issue,
    ErrorDetail,
    Error,
    VadSettings,
    FasterWhisperSettings,
    WhisperCppSettings,
    GenAISettings,
    ExtraArguments,
    ASRSettings,
    DiarizationSettings,
    PostprocessingSettings,
    OutputSettings,
    ProcessingSettings,
    PresetWrite,
    Preset,
    PolicyWrite,
    Policy,
    SettingOverride,
    FileInput,
    EditingSession,
)


def contract_schema() -> dict[str, Any]:
    """Only the B1 subset, not an OpenAPI server or the entire Phase A proposal."""
    _, schema = models_json_schema([(model, "validation") for model in MODELS])
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Utteran B1 contracts (schema version 1)"
    # Select roots explicitly; no empty root schema accepting arbitrary payloads.
    schema["oneOf"] = [
        {"$ref": f"#/$defs/{model.__name__}"}
        for model in (
            Error,
            ProcessingSettings,
            PresetWrite,
            Preset,
            PolicyWrite,
            Policy,
            EditingSession,
        )
    ]
    for name in ("PolicyWrite", "Policy"):
        schema["$defs"][name]["allOf"] = [
            {
                "if": {"properties": {"mode": {"const": "fixed"}}},
                "then": {
                    "properties": {
                        "fixed_candidate_id": {"type": "string", "minLength": 1},
                        "fallback": {"const": "none"},
                    }
                },
            },
            {
                "if": {"properties": {"mode": {"const": "auto"}}},
                "then": {"properties": {"fixed_candidate_id": {"type": "null"}}},
            },
        ]
    schema["$defs"]["SettingOverride"]["properties"]["path"]["enum"] = sorted(leaf_adapters())
    conditions = []
    for pointer, (adapter, nullable, unique) in sorted(leaf_adapters().items()):
        leaf_schema = adapter.json_schema()
        if not nullable and "anyOf" in leaf_schema:
            leaf_schema["anyOf"] = [v for v in leaf_schema["anyOf"] if v.get("type") != "null"]
        if unique:
            leaf_schema["uniqueItems"] = True
        conditions.append(
            {
                "if": {"properties": {"path": {"const": pointer}}},
                "then": {"properties": {"value": leaf_schema}},
            }
        )
    schema["$defs"]["SettingOverride"]["allOf"] = conditions
    normalize_literals(schema)
    return schema


def normalize_literals(value: Any) -> None:
    """Pydantic 2.9 emits a redundant enum next to const; remove only equivalents."""
    if isinstance(value, dict):
        values = value.get("enum")
        if (
            "const" in value
            and isinstance(values, list)
            and len(values) == 1
            and type(values[0]) is type(value["const"])
            and values[0] == value["const"]
        ):
            value.pop("enum")
        for bound in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            number = value.get(bound)
            if isinstance(number, float) and number.is_integer():
                value[bound] = int(number)
        for child in value.values():
            normalize_literals(child)
    elif isinstance(value, list):
        for child in value:
            normalize_literals(child)


def schema_bytes() -> bytes:
    return (
        json.dumps(contract_schema(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="Compare output without writing")
    args = parser.parse_args(argv)
    if args.check and args.output is None:
        parser.error("--check requires --output")
    content = schema_bytes()
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != content:
            print("contract schema is missing or stale", file=sys.stderr)
            return 1
    elif args.output is not None:
        args.output.write_bytes(content)
    else:
        sys.stdout.buffer.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
