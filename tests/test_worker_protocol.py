from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from utteran_contracts.worker import (
    EVENT_ADAPTER,
    EVENT_LIMIT,
    REQUEST_LIMIT,
    WorkerAccepted,
    WorkerCancelAcknowledged,
    WorkerError,
    WorkerHello,
    WorkerProgress,
    WorkerRequest,
)
from utteran_contracts.worker import (
    WorkerErrorCode as Code,
)
from utteran_contracts.worker_framing import (
    ProtocolError,
    decode_record,
    encode_record,
    read_record,
)
from utteran_contracts.worker_schema import schema_bytes
from utteran_contracts.worker_state import EventSequence


def ids(request: WorkerRequest) -> dict[str, str]:
    return {key: getattr(request, key) for key in ("request_id", "job_id", "attempt_id")}


def test_handshake_sequence_and_private_projection(worker_request: WorkerRequest) -> None:
    state = EventSequence(worker_request)
    state.accept(WorkerHello(sequence=0, profile="cpu"))
    state.accept(WorkerAccepted(sequence=1, **ids(worker_request)))
    state.accept(
        WorkerProgress(
            sequence=2, stage="asr", state="running", fraction=0.5, **ids(worker_request)
        )
    )
    state.accept(WorkerError(sequence=3, code=Code.CORE_FAILURE, **ids(worker_request)))
    assert state.terminal is not None
    serialized = encode_record(worker_request, REQUEST_LIMIT)
    assert serialized.count(b"\n") == 1
    assert (
        WorkerRequest.model_validate(read_record(io.BytesIO(serialized), REQUEST_LIMIT))
        == worker_request
    )
    assert "PRIVATE_PROMPT" not in repr(worker_request) + str(worker_request.public_projection())
    with pytest.raises(ProtocolError, match="invalid_sequence"):
        state.accept(WorkerError(sequence=4, code=Code.CORE_FAILURE, **ids(worker_request)))


@pytest.mark.parametrize(
    "raw,limit,code",
    [
        (b"{bad}\n", 200, Code.MALFORMED_MESSAGE),
        (b"\xff\n", 200, Code.MALFORMED_MESSAGE),
        (b"[]\n", 200, Code.MALFORMED_MESSAGE),
        (b"{}", 200, Code.UNEXPECTED_EOF),
        (b"", 200, Code.UNEXPECTED_EOF),
        (b"x" * 201, 200, Code.OVERSIZED_MESSAGE),
        (b'{"schema_version":1,"protocol_version":2}\n', 200, Code.PROTOCOL_MISMATCH),
        (b'{"schema_version":2,"protocol_version":1}\n', 200, Code.SCHEMA_MISMATCH),
        (b'{"schema_version":true,"protocol_version":1}\n', 200, Code.SCHEMA_MISMATCH),
        (b'{"schema_version":1,"protocol_version":true}\n', 200, Code.PROTOCOL_MISMATCH),
        (b'{"schema_version":1,"protocol_version":1,"x":NaN}\n', 200, Code.MALFORMED_MESSAGE),
        (b'{"schema_version":1,"protocol_version":1,"x":1,"x":2}\n', 200, Code.MALFORMED_MESSAGE),
    ],
)
def test_framing_rejections(raw: bytes, limit: int, code: Code) -> None:
    with pytest.raises(ProtocolError) as caught:
        decode_record(raw, limit)
    assert caught.value.code == code


@pytest.mark.parametrize("limit", [REQUEST_LIMIT, EVENT_LIMIT])
def test_bounded_stream_read(limit: int) -> None:
    class Stream(io.BytesIO):
        def readline(self, size: int = -1) -> bytes:
            assert size == limit + 1
            return super().readline(size)

    with pytest.raises(ProtocolError, match="oversized_message"):
        read_record(Stream(b"x" * (limit + 2)), limit)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 2},
        {"protocol_version": 2},
        {"unknown": "PRIVATE_PROMPT"},
        {"request_id": "../invalid"},
        {"job_id": ""},
        {"attempt_id": "has spaces"},
        {"force": "false"},
        {"context": {"unknown": 1}},
    ],
)
def test_typed_request_rejections(worker_request: WorkerRequest, changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WorkerRequest.model_validate({**worker_request.model_dump(mode="json"), **changes})


@pytest.mark.parametrize(
    "kind",
    [
        "progress_first",
        "accepted_twice",
        "seq_gap",
        "hello_twice",
        "request_id",
        "job_id",
        "attempt_id",
        "unsolicited_cancel_ack",
    ],
)
def test_invalid_event_order_and_ids(worker_request: WorkerRequest, kind: str) -> None:
    state = EventSequence(worker_request)
    state.accept(WorkerHello(sequence=0, profile="cpu"))
    identity = ids(worker_request)
    if kind == "progress_first":
        event = WorkerProgress(sequence=1, stage="asr", state="running", **identity)
    elif kind == "hello_twice":
        event = WorkerHello(sequence=0, profile="cpu")
    else:
        state.accept(WorkerAccepted(sequence=1, **identity))
        if kind == "accepted_twice":
            event = WorkerAccepted(sequence=2, **identity)
        elif kind == "unsolicited_cancel_ack":
            event = WorkerCancelAcknowledged(sequence=2, **identity)
        else:
            if kind in identity:
                identity[kind] = "different"
            event = WorkerError(
                sequence=4 if kind == "seq_gap" else 2, code=Code.CORE_FAILURE, **identity
            )
    with pytest.raises(ProtocolError):
        state.accept(event)


def test_worker_schema_offline_artifact(worker_request: WorkerRequest) -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads(schema_bytes())
    assert (
        schema_bytes()
        == (Path(__file__).parents[1] / "docs/architecture/b3/worker.schema.json").read_bytes()
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(worker_request.model_dump(mode="json"))
    assert "PRIVATE_PROMPT" not in schema_bytes().decode()
    with pytest.raises(ValidationError):
        EVENT_ADAPTER.validate_python(
            {"type": "progress", "sequence": 0, "stage": "PRIVATE_PROMPT"}
        )
