"""E-B: the saved, restorable part of one named workset (version 1, ``--next-ui-v1`` only).

Only *confirmed* editing state is representable: the ordered input list (selection, primary,
per-row output and overrides), the frozen B5 Preset/Policy ``id`` + ``revision`` and the common
differences. Nothing here describes a job, a queue, a validation/resolution token, an
idempotency intent, an unapplied draft or any other runtime state (B7 is their only owner).

The shape is canonical so that equal states have equal bytes: absent overrides are omitted
(explicit ``null`` is refused), glossaries are de-duplicated into ``glossaries`` and referenced
by index in first-use order, and every list keeps its order.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

SCHEMA_VERSION = 1

#: Owner decision (Issue #80): about 20 named worksets.
MAX_WORKSETS = 20
#: Same as the frontend input list (``inputCollection.MAX_INPUTS``).
MAX_INPUTS = 100
#: B5 ``ASRSettings.glossary``: 1,000 terms of at most 256 Unicode code points.
MAX_GLOSSARY_TERMS = 1000
MAX_TERM_CODEPOINTS = 256
#: B5 ``FileInput.input_path`` / ``output_dir``.
MAX_PATH = 32768
MAX_NAME = 120
#: One workset's canonical UTF-8 state. Equal to the B5/B7 request limit (8 MiB), so a state
#: that fits here can be sent to B5 as one editing session of the same rows; measured worst
#: legal configurations are recorded in docs/architecture/ui3/e-b-worksets.md.
MAX_STATE_BYTES = 8 * 1024 * 1024
#: Store-wide sum of the current states. 20 worksets at the measured "typical maximum" (100
#: inputs, one shared 1,000-term CJK glossary) need about 19 MB, so this leaves >3x headroom
#: while bounding the file (current + recovery) to about 96 MiB plus SQLite overhead.
MAX_STORE_STATE_BYTES = 64 * 1024 * 1024
#: Recovery generations kept per workset (previous checkpoints, never per-keystroke copies).
RECOVERY_GENERATIONS = 2
#: A new recovery checkpoint is taken only when the newest one is older than this.
CHECKPOINT_INTERVAL_SECONDS = 10 * 60
#: Store-wide byte budget for recovery generations; when exceeded, the oldest recovery
#: checkpoints of the new store are dropped first. Current states are never dropped.
RECOVERY_BUDGET_BYTES = 32 * 1024 * 1024

OutputFormat = Literal["srt", "vtt", "json", "txt", "md"]
Source = Literal["picker", "drop", "typed"]
OutputSource = Literal["saved_default", "picker", "typed"]

Short = Annotated[StrictStr, Field(min_length=1, max_length=200)]
Path = Annotated[StrictStr, Field(min_length=1, max_length=MAX_PATH)]
Language = Annotated[StrictStr, Field(min_length=1, max_length=32)]
Formats = Annotated[tuple[OutputFormat, ...], Field(max_length=5)]
Term = Annotated[StrictStr, Field(min_length=1, max_length=MAX_TERM_CODEPOINTS)]
Glossary = Annotated[tuple[Term, ...], Field(max_length=MAX_GLOSSARY_TERMS)]


class _Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, allow_inf_nan=False
    )

    @model_validator(mode="before")
    @classmethod
    def _no_explicit_null(cls, data: Any) -> Any:
        # Absence means "inherit"; an explicit null is a different, unsupported value.
        if isinstance(data, dict):
            for name, field in cls.model_fields.items():
                if name in data and data[name] is None and not field.is_required():
                    raise ValueError("explicit null is not allowed for this optional field")
        return data


class Ref(_Model):
    id: Short
    revision: Annotated[StrictInt, Field(ge=1, le=10**18)]


class Common(_Model):
    """Screen differences from the Preset (``null`` = inherit) and the common output folder."""

    language: Language | None
    formats: Formats | None
    output_dir: Annotated[StrictStr, Field(max_length=MAX_PATH)]
    output_source: OutputSource | None

    @model_validator(mode="after")
    def _unique_formats(self) -> Common:
        if self.formats is not None and len(set(self.formats)) != len(self.formats):
            raise ValueError("formats must be unique")
        return self


class Output(_Model):
    kind: Literal["inherit", "custom"]
    path: Path | None = None

    @model_validator(mode="after")
    def _path_for_custom(self) -> Output:
        if (self.kind == "custom") != (self.path is not None):
            raise ValueError("a custom output needs a path and inherit has none")
        return self


class Overrides(_Model):
    """Only the leaves a row changes; an absent key follows the common value."""

    asr_backend: Short | None = None
    asr_model: Short | None = None
    asr_device: Short | None = None
    language: Language | None = None
    formats: Formats | None = None
    #: Index into ``WorksetState.glossaries`` (an explicit, possibly empty, list).
    glossary: Annotated[StrictInt, Field(ge=0, lt=MAX_INPUTS)] | None = None

    @model_validator(mode="after")
    def _unique_formats(self) -> Overrides:
        if self.formats is not None and len(set(self.formats)) != len(self.formats):
            raise ValueError("formats must be unique")
        return self


class Input(_Model):
    input_path: Path
    source: Source
    selected: StrictBool
    output: Output
    overrides: Overrides


class WorksetState(_Model):
    schema_version: Literal[1]
    preset_ref: Ref | None
    policy_ref: Ref | None
    common: Common
    inputs: Annotated[tuple[Input, ...], Field(max_length=MAX_INPUTS)]
    primary_index: Annotated[StrictInt, Field(ge=0, lt=MAX_INPUTS)] | None
    glossaries: Annotated[tuple[Glossary, ...], Field(max_length=MAX_INPUTS)]

    @model_validator(mode="after")
    def _references(self) -> WorksetState:
        if (self.primary_index is None) != (len(self.inputs) == 0):
            raise ValueError("primary_index is required exactly when inputs exist")
        if self.primary_index is not None and self.primary_index >= len(self.inputs):
            raise ValueError("primary_index out of range")
        # Canonical de-duplication: new glossaries appear in first-use order, each used.
        expected = 0
        for item in self.inputs:
            index = item.overrides.glossary
            if index is None:
                continue
            if index == expected:
                expected += 1
            elif index > expected:
                raise ValueError("glossaries must be referenced in first-use order")
        if expected != len(self.glossaries):
            raise ValueError("every glossary must be referenced")
        if len({tuple(g) for g in self.glossaries}) != len(self.glossaries):
            raise ValueError("glossaries must be unique")
        return self

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_unset=True)


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "preset_ref": None,
        "policy_ref": None,
        "common": {"language": None, "formats": None, "output_dir": "", "output_source": None},
        "inputs": [],
        "primary_index": None,
        "glossaries": [],
    }


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def wire_state(state: WorksetState) -> dict[str, Any]:
    """The stored/served form: absent overrides stay absent, nullable fields stay explicit."""
    data = state.model_dump(mode="json")
    for item in data["inputs"]:
        item["overrides"] = {k: v for k, v in item["overrides"].items() if v is not None}
        if item["output"]["path"] is None:
            del item["output"]["path"]
    return data


def input_count(state_text: str) -> int:
    value = json.loads(state_text)
    return len(value["inputs"])


def state_schema() -> dict[str, Any]:
    return WorksetState.model_json_schema()
