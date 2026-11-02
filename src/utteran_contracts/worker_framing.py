"""Bounded UTF-8 JSONL framing, separate from typed payload validation."""

from __future__ import annotations

import json
from typing import Any, BinaryIO

from pydantic import ValidationError

from utteran_contracts.worker import Envelope, WorkerErrorCode


class ProtocolError(Exception):
    def __init__(self, code: WorkerErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def decode_record(raw: bytes, limit: int) -> dict[str, Any]:
    if len(raw) > limit:
        raise ProtocolError(WorkerErrorCode.OVERSIZED_MESSAGE)
    if not raw or not raw.endswith(b"\n"):
        raise ProtocolError(WorkerErrorCode.UNEXPECTED_EOF)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        if not isinstance(value, dict):
            raise ValueError
    except (UnicodeError, ValueError, RecursionError):
        raise ProtocolError(WorkerErrorCode.MALFORMED_MESSAGE) from None
    for key, code in (
        ("protocol_version", WorkerErrorCode.PROTOCOL_MISMATCH),
        ("schema_version", WorkerErrorCode.SCHEMA_MISMATCH),
    ):
        if type(value.get(key)) is not int or value[key] != 1:
            raise ProtocolError(code)
    return value


def read_record(stream: BinaryIO, limit: int) -> dict[str, Any]:
    return decode_record(stream.readline(limit + 1), limit)


def encode_record(value: Envelope, limit: int) -> bytes:
    raw = (value.model_dump_json() + "\n").encode("utf-8")
    if len(raw) > limit:
        raise ProtocolError(WorkerErrorCode.OVERSIZED_MESSAGE)
    return raw


def validated_error(_: ValidationError) -> ProtocolError:
    # Neither raw input nor user-supplied field names enter diagnostics.
    return ProtocolError(WorkerErrorCode.MALFORMED_MESSAGE)
