"""Logging setup with defense-in-depth token redaction."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

_HF_TOKEN_PATTERN = re.compile(r"\bhf_[A-Za-z0-9_-]{4,}\b")
_REGISTERED_SECRETS: set[str] = set()


def register_secret(secret: str | None) -> None:
    """Register an exact secret value that must be removed from formatted logs."""
    if secret:
        _REGISTERED_SECRETS.add(secret)


def mask_secrets(value: str) -> str:
    """Mask Hugging Face-looking and explicitly registered secret values."""
    masked = _HF_TOKEN_PATTERN.sub("hf_****", value)
    for secret in sorted(_REGISTERED_SECRETS, key=len, reverse=True):
        masked = masked.replace(secret, "****")
    return masked


class RedactingFormatter(logging.Formatter):
    """Apply redaction after standard formatting, including exception text."""

    def format(self, record: logging.LogRecord) -> str:
        """Format and sanitize a complete log record."""
        return mask_secrets(super().format(record))


class JsonFormatter(RedactingFormatter):
    """Write one compact JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a record and then sanitize the complete JSON line."""
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return mask_secrets(json.dumps(payload, ensure_ascii=False))


def configure_logging(level: str = "info", log_file: Path | None = None) -> None:
    """Configure console logging and an optional structured log file."""
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    console = logging.StreamHandler()
    console.setLevel(level.upper())
    console.setFormatter(RedactingFormatter("%(levelname)s: %(message)s"))
    root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)


@contextmanager
def job_log(path: Path, level: str = "info") -> Iterator[None]:
    """Temporarily append redacted structured records to one job log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    selected_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    previous_level = root.level
    if previous_level > selected_level:
        root.setLevel(selected_level)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(selected_level)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous_level)
