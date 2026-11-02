"""Value-free control-store failures. Messages never carry paths, SQL or payloads."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    STORE_BUSY = "store_busy"
    STORE_FULL = "store_full"
    STORE_READONLY = "store_readonly"
    STORE_CORRUPT = "store_corrupt"
    STORE_UNAVAILABLE = "store_unavailable"
    STORE_SCHEMA_UNSUPPORTED = "store_schema_unsupported"
    STORE_UNSAFE_PATH = "store_unsafe_path"
    STORE_EXISTS = "store_exists"
    RESOURCE_NOT_FOUND = "resource_not_found"
    REVISION_CONFLICT = "revision_conflict"
    INVALID_TRANSITION = "invalid_transition"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IDEMPOTENCY_KEY_INVALID = "idempotency_key_invalid"
    ENDPOINT_INVALID = "endpoint_invalid"
    REQUEST_INVALID = "request_invalid"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    PRIVATE_PAYLOAD_FORBIDDEN = "private_payload_forbidden"
    CURSOR_INVALID = "cursor_invalid"
    EVENT_CURSOR_EXPIRED = "event_cursor_expired"
    FENCING_TOKEN_STALE = "fencing_token_stale"
    SLOT_OCCUPIED = "slot_occupied"
    QUEUE_PAUSED = "queue_paused"
    NOT_DISPATCHABLE = "not_dispatchable"


# Intended HTTP mapping for B9; B6 itself serves no HTTP.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.RESOURCE_NOT_FOUND: 404,
    ErrorCode.REVISION_CONFLICT: 412,
    ErrorCode.INVALID_TRANSITION: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_KEY_INVALID: 400,
    ErrorCode.ENDPOINT_INVALID: 400,
    ErrorCode.REQUEST_INVALID: 422,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.PRIVATE_PAYLOAD_FORBIDDEN: 422,
    ErrorCode.CURSOR_INVALID: 400,
    ErrorCode.EVENT_CURSOR_EXPIRED: 410,
    ErrorCode.FENCING_TOKEN_STALE: 409,
    ErrorCode.SLOT_OCCUPIED: 409,
    ErrorCode.QUEUE_PAUSED: 409,
    ErrorCode.NOT_DISPATCHABLE: 409,
    ErrorCode.STORE_EXISTS: 409,
}


class ControlError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        self.status = HTTP_STATUS.get(code, 503)
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"ControlError({self.code.value!r})"
