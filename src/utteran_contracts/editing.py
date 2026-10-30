"""Leaf overrides and editing requests, without merging or resolving settings."""

from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from itertools import pairwise
from types import MappingProxyType
from typing import Annotated, Any, cast, get_args

from pydantic import (
    Field,
    JsonValue,
    StrictStr,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

from utteran_contracts.base import Contract, VersionedContract
from utteran_contracts.settings import ProcessingSettings, Ref


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@lru_cache(maxsize=1)
def leaf_adapters() -> Mapping[str, tuple[TypeAdapter[Any], bool, bool]]:
    """Build a pure type registry; arrays/maps are atomic leaves, not indexable paths."""
    result: dict[str, tuple[TypeAdapter[Any], bool, bool]] = {}

    def visit(model: type[Contract], prefix: str) -> None:
        for name, field in model.model_fields.items():
            if name == "schema_version":
                continue
            pointer = f"{prefix}/{name}"
            annotation = field.annotation
            variants = (annotation, *get_args(annotation))
            nested = next(
                (v for v in variants if isinstance(v, type) and issubclass(v, Contract)), None
            )
            if nested is not None:
                visit(nested, pointer)
            else:
                result[pointer] = (
                    TypeAdapter(field.rebuild_annotation()),
                    name not in model.nonnullable_optional,
                    isinstance(field.json_schema_extra, dict)
                    and field.json_schema_extra.get("uniqueItems") is True,
                )

    visit(ProcessingSettings, "")
    return MappingProxyType(result)


class SettingOverride(Contract):
    path: StrictStr
    value: JsonValue

    @field_validator("value", mode="before")
    @classmethod
    def accept_frozen_json(cls, value: Any) -> Any:
        return _thaw(value)

    @model_validator(mode="after")
    def validate_leaf(self) -> SettingOverride:
        target = leaf_adapters().get(self.path)
        if target is None:
            raise ValueError("override path must be a registered settings leaf")
        adapter, nullable, unique = target
        if self.value is None and not nullable:
            raise ValueError("explicit null is not allowed for this leaf")
        # JSON validation accepts JSON arrays for immutable tuple fields, without
        # accepting Python-only coercions such as an integer string or bool-as-int.
        validated = adapter.validate_json(json.dumps(_thaw(self.value)), strict=True)
        if (
            unique
            and isinstance(validated, (list, tuple))
            and len(set(validated)) != len(validated)
        ):
            raise ValueError("duplicate values in unique array leaf")
        object.__setattr__(self, "value", cast(JsonValue, _freeze(_thaw(self.value))))
        return self

    @field_serializer("value")
    def serialize_value(self, value: JsonValue) -> Any:
        return _thaw(value)


def validate_override_paths(overrides: tuple[SettingOverride, ...]) -> None:
    paths = sorted(item.path for item in overrides)
    if len(set(paths)) != len(paths) or any(
        second.startswith(first + "/") for first, second in pairwise(paths)
    ):
        raise ValueError("duplicate or overlapping override paths")


class FileInput(Contract):
    client_file_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    input_path: Annotated[StrictStr, Field(min_length=1, max_length=32768)]
    output_dir: Annotated[StrictStr, Field(min_length=1, max_length=32768)]
    overrides: tuple[SettingOverride, ...]

    @model_validator(mode="after")
    def validate_overrides(self) -> FileInput:
        validate_override_paths(self.overrides)
        return self


class EditingSession(VersionedContract):
    preset_ref: Ref
    policy_ref: Ref
    common_overrides: tuple[SettingOverride, ...]
    files: Annotated[tuple[FileInput, ...], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def validate_editing_shape(self) -> EditingSession:
        validate_override_paths(self.common_overrides)
        if len({item.client_file_id for item in self.files}) != len(self.files):
            raise ValueError("duplicate client file identifier")
        return self
