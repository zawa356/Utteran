"""Pure, immutable contract primitives; explicit wire and diagnostic projections."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, ClassVar, Literal, Self, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

SCHEMA_VERSION = 1
SchemaVersion = Literal[1]


class Contract(BaseModel):
    """DTO only: never resolves paths, detects devices, or executes requests."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        allow_inf_nan=False,
        revalidate_instances="never",
    )
    # Python None represents absence for these optional, non-nullable wire fields.
    # fields_set / to_wire preserve absence; explicit null is rejected.
    nonnullable_optional: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def check_explicit_values(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            if "schema_version" in data and (
                type(data["schema_version"]) is not int or data["schema_version"] != SCHEMA_VERSION
            ):
                raise ValueError("unsupported contract schema version")
            if any(name in data and data[name] is None for name in cls.nonnullable_optional):
                raise ValueError("explicit null is not allowed for this optional field")
            for name, field in cls.model_fields.items():
                if (
                    name in data
                    and get_origin(field.annotation) is tuple
                    and not isinstance(data[name], (list, tuple))
                ):
                    raise ValueError("array fields require an ordered JSON array or tuple")
        return data

    def __repr_args__(self) -> Iterable[tuple[str | None, Any]]:
        # Even nested DTOs, user labels, and experimental values stay out of repr/str.
        return ()

    @model_serializer(mode="wrap")
    def serialize_present_fields(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}

    def to_wire(self) -> dict[str, Any]:
        """Explicit private serialization; never use this projection for logging."""
        return self.model_dump(mode="json", exclude_unset=True)

    def public_projection(self) -> dict[str, str | int]:
        """Minimal allowlist for diagnostics; deliberately contains no user values."""
        return {"contract": type(self).__name__, "schema_version": SCHEMA_VERSION}

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Unlike BaseModel.model_copy, updates are validated; nested values are immutable."""
        return type(self).model_validate({**self.to_wire(), **(update or {})})

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        schema = handler.resolve_ref_schema(handler(core_schema))
        for name in cls.nonnullable_optional:
            prop = schema.get("properties", {}).get(name, {})
            if "anyOf" in prop:
                branches = [b for b in prop["anyOf"] if b.get("type") != "null"]
                prop.pop("anyOf")
                if len(branches) == 1:
                    prop.update(branches[0])
                else:
                    prop["anyOf"] = branches
            prop.pop("default", None)
        return schema


class VersionedContract(Contract):
    schema_version: SchemaVersion


def validation_error_projection(error: ValidationError) -> dict[str, Any]:
    """Do not forward errors()/json(): they can retain private input and context."""
    return {
        "code": "contract_validation_failed",
        "error_types": tuple(
            item["type"]
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        ),
    }
