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
    # B7 job list snapshots (process-local)
    LIST_CURSOR_EXPIRED = "cursor_expired"
    LIST_SNAPSHOT_TOO_LARGE = "list_snapshot_too_large"
    # B8 setup plan / approval / operation
    PLAN_EXPIRED = "plan_expired"
    PLAN_NOT_APPROVED = "plan_not_approved"
    PLAN_ALREADY_APPROVED = "plan_already_approved"
    PLAN_CONSUMED = "plan_consumed"
    PLAN_HASH_MISMATCH = "plan_hash_mismatch"
    PLAN_REVISION_MISMATCH = "plan_revision_mismatch"
    INVENTORY_STALE = "inventory_stale"
    AGREEMENT_REQUIRED = "agreement_required"
    AGREEMENT_UNKNOWN = "agreement_unknown"
    CREDENTIAL_REQUIRED = "credential_required"
    APPROVAL_INVALID = "approval_invalid"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_CONSUMED = "approval_consumed"
    REAPPROVAL_REQUIRED = "reapproval_required"


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
    ErrorCode.LIST_CURSOR_EXPIRED: 409,
    ErrorCode.LIST_SNAPSHOT_TOO_LARGE: 503,
    ErrorCode.PLAN_EXPIRED: 409,
    ErrorCode.PLAN_NOT_APPROVED: 409,
    ErrorCode.PLAN_ALREADY_APPROVED: 409,
    ErrorCode.PLAN_CONSUMED: 409,
    ErrorCode.PLAN_HASH_MISMATCH: 409,
    ErrorCode.PLAN_REVISION_MISMATCH: 409,
    ErrorCode.INVENTORY_STALE: 409,
    ErrorCode.AGREEMENT_REQUIRED: 409,
    ErrorCode.AGREEMENT_UNKNOWN: 422,
    ErrorCode.CREDENTIAL_REQUIRED: 409,
    ErrorCode.APPROVAL_INVALID: 409,
    ErrorCode.APPROVAL_EXPIRED: 409,
    ErrorCode.APPROVAL_CONSUMED: 409,
    ErrorCode.REAPPROVAL_REQUIRED: 409,
}


class ControlError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        self.status = HTTP_STATUS.get(code, 503)
        super().__init__(code.value)

    def __repr__(self) -> str:
        return f"ControlError({self.code.value!r})"
