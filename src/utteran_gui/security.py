"""Secret redaction shared by the GUI subprocess and HTTP layers."""

from __future__ import annotations

import re
import threading

_HF_TOKEN_PATTERN = re.compile(r"\bhf_[A-Za-z0-9_-]{4,}\b")
_SECRETS: set[str] = set()
_LOCK = threading.Lock()


def register_secret(secret: str | None) -> None:
    """Register an exact value without ever exposing it through an accessor."""
    if not secret:
        return
    with _LOCK:
        _SECRETS.add(secret)


def mask_secrets(value: str) -> str:
    """Mask token-shaped and explicitly registered values in arbitrary text."""
    masked = _HF_TOKEN_PATTERN.sub("hf_****", value)
    with _LOCK:
        secrets = tuple(sorted(_SECRETS, key=len, reverse=True))
    for secret in secrets:
        masked = masked.replace(secret, "****")
    return masked


def sanitize_json(value: object) -> object:
    """Recursively redact strings in parsed subprocess payloads."""
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, dict):
        return {str(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    return value
