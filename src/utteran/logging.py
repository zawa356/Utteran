"""Application, structured execution, and opt-in raw subprocess logging."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_log_dir

_HF_TOKEN_PATTERN = re.compile(r"\bhf_[A-Za-z0-9_-]{4,}\b")
_REGISTERED_SECRETS: set[str] = set()
_JOB_ID: ContextVar[str | None] = ContextVar("utteran_job_id", default=None)
_RUNTIME: RuntimeLogging | None = None


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
        return mask_secrets(super().format(record))


class JsonFormatter(RedactingFormatter):
    """Write one compact JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "utteran_event", None)
        fields = getattr(record, "utteran_fields", None)
        if isinstance(event, str):
            payload["event"] = event
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return mask_secrets(json.dumps(payload, ensure_ascii=False, default=str))


class _StructuredEventFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return isinstance(getattr(record, "utteran_event", None), str)


@dataclass(frozen=True)
class LogCleanupResult:
    files_deleted: int
    bytes_deleted: int


@dataclass
class RuntimeLogging:
    log_dir: Path
    preferred_log_dir: Path
    fell_back: bool
    raw_enabled: bool
    cli_log: Path | None = None


def application_dir() -> Path:
    """Return the install/check-out directory used by the default log policy."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    cwd = Path.cwd().resolve()
    if (cwd / "pyproject.toml").is_file():
        return cwd
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root
    return Path(sys.prefix).resolve()


def resolve_log_dir(
    configured: Path | None = None,
    *,
    install_dir: Path | None = None,
    fallback_dir: Path | None = None,
    writable: Callable[[Path], bool] | None = None,
) -> tuple[Path, Path, bool]:
    """Resolve configured/install logs, falling back to per-user storage."""
    preferred = (
        configured.expanduser().resolve()
        if configured is not None
        else (install_dir or application_dir()).resolve() / "logs"
    )
    fallback = (fallback_dir or Path(user_log_dir("utteran"))).expanduser().resolve()
    probe = writable or _is_writable_directory
    if probe(preferred):
        return preferred, preferred, False
    if not probe(fallback):
        raise OSError(f"ログ保存先へ書き込めません: {preferred}; fallback={fallback}")
    return fallback, preferred, True


def _is_writable_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".utteran-write-test-{os.getpid()}"
        with probe.open("x", encoding="utf-8"):
            pass
        probe.unlink()
        return True
    except OSError:
        return False


def configure_logging(level: str = "info", log_file: Path | None = None) -> None:
    """Configure console logging and an optional structured job log."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level.upper())
    console = logging.StreamHandler()
    console.setLevel(level.upper())
    console.setFormatter(RedactingFormatter("%(levelname)s: %(message)s"))
    root.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)


def configure_runtime_logging(
    *,
    level: str = "info",
    log_dir: Path | None = None,
    raw_enabled: bool = False,
    retention_days: int = 30,
    max_bytes: int = 100 * 1024 * 1024,
    raw_max_bytes: int = 1024 * 1024 * 1024,
    command: str | None = None,
    install_dir: Path | None = None,
) -> RuntimeLogging:
    """Configure rotating app logging and a per-command event-only JSONL file."""
    global _RUNTIME
    selected, preferred, fell_back = resolve_log_dir(log_dir, install_dir=install_dir)
    cleanup = clean_logs(
        selected,
        retention_days=retention_days,
        max_bytes=max_bytes,
        raw_max_bytes=raw_max_bytes,
    )
    configure_logging(level)
    root = logging.getLogger()
    app_handler = RotatingFileHandler(
        selected / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    app_handler.setFormatter(JsonFormatter())
    root.addHandler(app_handler)
    cli_path: Path | None = None
    if command:
        safe_command = re.sub(r"[^A-Za-z0-9_.-]+", "-", command).strip("-") or "command"
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        cli_path = selected / "cli" / f"{stamp}-{safe_command}.jsonl"
        cli_path.parent.mkdir(parents=True, exist_ok=True)
        cli_handler = logging.FileHandler(cli_path, encoding="utf-8")
        cli_handler.addFilter(_StructuredEventFilter())
        cli_handler.setFormatter(JsonFormatter())
        root.addHandler(cli_handler)
    _RUNTIME = RuntimeLogging(selected, preferred, fell_back, raw_enabled, cli_path)
    if fell_back:
        structured_event(
            "log_dir_fallback", preferred_path=str(preferred), resolved_path=str(selected)
        )
    if cleanup.files_deleted:
        structured_event(
            "log_cleanup",
            files_deleted=cleanup.files_deleted,
            bytes_deleted=cleanup.bytes_deleted,
            reason="startup_retention",
        )
    if raw_enabled:
        logging.getLogger(__name__).warning(
            "生のサブプロセス出力ログが有効です。文字起こし本文が含まれる可能性があります。"
        )
        structured_event("raw_subprocess_logging", enabled=True, privacy_warning=True)
    return _RUNTIME


def runtime_logging() -> RuntimeLogging | None:
    return _RUNTIME


def close_runtime_logging() -> None:
    """Close all handlers so Windows can remove log files safely."""
    global _RUNTIME
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    _RUNTIME = None


def structured_event(event: str, *, level: int = logging.INFO, **fields: object) -> None:
    """Record transcript-free execution facts in the structured event stream."""
    safe_fields = {key: value for key, value in fields.items() if value is not None}
    logging.getLogger("utteran.events").log(
        level,
        event,
        extra={"utteran_event": event, "utteran_fields": safe_fields},
    )


@contextmanager
def execution_context(job_id: str) -> Iterator[None]:
    token = _JOB_ID.set(job_id)
    try:
        yield
    finally:
        _JOB_ID.reset(token)


def write_raw_subprocess_log(name: str, content: str) -> Path | None:
    """Write redacted subprocess output only when the explicit opt-in is active."""
    runtime = _RUNTIME
    job_id = _JOB_ID.get()
    if runtime is None or not runtime.raw_enabled or not job_id:
        return None
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)
    target = runtime.log_dir / "raw" / job_id / f"{safe_name}.stderr.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as file:
        file.write(mask_secrets(content))
    return target


def write_diagnostic_snapshot(name: str, payload: object) -> Path | None:
    """Persist a redacted, timestamped environment snapshot under diagnostics/."""
    runtime = _RUNTIME
    if runtime is None:
        return None
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    target = runtime.log_dir / "diagnostics" / f"{stamp}-{safe_name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    target.write_text(mask_secrets(serialized), encoding="utf-8")
    structured_event("diagnostic_snapshot", kind=name, path=str(target))
    return target


def clean_logs(
    log_dir: Path,
    *,
    retention_days: int,
    max_bytes: int,
    raw_max_bytes: int,
    now: datetime | None = None,
) -> LogCleanupResult:
    """Delete old files, then oldest files exceeding raw and overall byte caps."""
    if not log_dir.exists():
        return LogCleanupResult(0, 0)
    selected_now = now or datetime.now(UTC)
    cutoff = selected_now - timedelta(days=retention_days)
    deleted_files = 0
    deleted_bytes = 0
    files = [path for path in log_dir.rglob("*") if path.is_file()]
    for path in files:
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified < cutoff:
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            deleted_files += 1
            deleted_bytes += size
    remaining = [path for path in log_dir.rglob("*") if path.is_file()]
    raw_root = log_dir / "raw"
    raw_files = [path for path in remaining if raw_root in path.parents]
    count, size = _trim_to_size(raw_files, raw_max_bytes)
    deleted_files += count
    deleted_bytes += size
    remaining = [
        path
        for path in log_dir.rglob("*")
        if path.is_file() and raw_root not in path.parents
    ]
    count, size = _trim_to_size(remaining, max_bytes)
    deleted_files += count
    deleted_bytes += size
    for directory in sorted(
        (path for path in log_dir.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        with suppress(OSError):
            directory.rmdir()
    return LogCleanupResult(deleted_files, deleted_bytes)


def _trim_to_size(files: list[Path], maximum: int) -> tuple[int, int]:
    existing = [path for path in files if path.exists()]
    total = sum(path.stat().st_size for path in existing)
    deleted = 0
    deleted_bytes = 0
    for path in sorted(existing, key=lambda item: item.stat().st_mtime):
        if total <= maximum:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total -= size
        deleted += 1
        deleted_bytes += size
    return deleted, deleted_bytes


def remove_all_logs(log_dir: Path) -> LogCleanupResult:
    """Remove a resolved log directory's contents for the manual clean command."""
    if not log_dir.exists():
        return LogCleanupResult(0, 0)
    files = [path for path in log_dir.rglob("*") if path.is_file()]
    size = sum(path.stat().st_size for path in files)
    count = len(files)
    shutil.rmtree(log_dir)
    return LogCleanupResult(count, size)


@contextmanager
def job_log(path: Path, level: str = "info") -> Iterator[None]:
    """Temporarily append redacted structured records to one legacy job log."""
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
