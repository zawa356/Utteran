"""Dependency-free GUI logging configuration for the minimal packaged venv."""

from __future__ import annotations

import json
import logging
import os
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_config_dir, user_log_dir

from utteran_gui.security import mask_secrets


@dataclass(frozen=True)
class GuiLoggingSettings:
    log_dir: Path | None
    raw_subprocess_logs: bool
    retention_days: int
    max_bytes: int
    raw_max_bytes: int


@dataclass(frozen=True)
class GuiLoggingStatus:
    log_dir: Path
    fell_back: bool
    raw_subprocess_logs: bool


_STATUS: GuiLoggingStatus | None = None


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask_secrets(json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        ))


def load_logging_settings() -> GuiLoggingSettings:
    """Read only the two logging values the standalone GUI must expose."""
    payload: object = {}
    path = Path(user_config_dir("utteran")) / "config.toml"
    try:
        with path.open("rb") as file:
            payload = tomllib.load(file).get("general", {})
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        pass
    general = payload if isinstance(payload, dict) else {}
    configured = os.environ.get("UTTERAN_GENERAL__LOG_DIR") or general.get("log_dir")
    raw_value: object = os.environ.get("UTTERAN_GENERAL__RAW_SUBPROCESS_LOGS")
    if raw_value is None:
        raw_value = general.get("raw_subprocess_logs", False)
    raw_enabled = (
        raw_value.casefold() in {"1", "true", "yes", "on"}
        if isinstance(raw_value, str)
        else bool(raw_value)
    )
    return GuiLoggingSettings(
        Path(str(configured)).expanduser() if configured else None,
        raw_enabled,
        _positive_int(general.get("log_retention_days"), 30),
        _positive_int(general.get("log_max_mib"), 100) * 1024 * 1024,
        _positive_int(general.get("raw_log_max_mib"), 1024) * 1024 * 1024,
    )


def resolve_gui_log_dir(
    settings: GuiLoggingSettings,
    *,
    install_dir: Path,
) -> tuple[Path, bool]:
    preferred = (
        settings.log_dir.resolve()
        if settings.log_dir is not None
        else install_dir.resolve() / "logs"
    )
    if _writable(preferred):
        return preferred, False
    fallback = Path(user_log_dir("utteran")).resolve()
    if not _writable(fallback):
        raise OSError(f"ログ保存先へ書き込めません: {preferred}; fallback={fallback}")
    return fallback, True


def configure_gui_logging(*, install_dir: Path) -> GuiLoggingStatus:
    global _STATUS
    settings = load_logging_settings()
    selected, fell_back = resolve_gui_log_dir(settings, install_dir=install_dir)
    deleted, deleted_bytes = _cleanup(
        selected,
        retention_days=settings.retention_days,
        max_bytes=settings.max_bytes,
        raw_max_bytes=settings.raw_max_bytes,
    )
    handler = RotatingFileHandler(
        selected / "app.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(_JsonFormatter())
    logging.getLogger().addHandler(handler)
    _STATUS = GuiLoggingStatus(selected, fell_back, settings.raw_subprocess_logs)
    if fell_back:
        logging.getLogger(__name__).warning("ログ保存先をユーザーデータ領域へ退避しました。")
    if settings.raw_subprocess_logs:
        logging.getLogger(__name__).warning(
            "生のサブプロセス出力ログが有効です。文字起こし本文が含まれる可能性があります。"
        )
    if deleted:
        logging.getLogger(__name__).info(
            "ログ保持ポリシーにより%dファイル (%d bytes) を削除しました。",
            deleted,
            deleted_bytes,
        )
    return _STATUS


def gui_logging_status(*, install_dir: Path) -> GuiLoggingStatus:
    if _STATUS is not None:
        return _STATUS
    settings = load_logging_settings()
    selected, fell_back = resolve_gui_log_dir(settings, install_dir=install_dir)
    return GuiLoggingStatus(selected, fell_back, settings.raw_subprocess_logs)


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".utteran-gui-write-test-{os.getpid()}-{sys.version_info.minor}"
        with probe.open("x", encoding="utf-8"):
            pass
        probe.unlink()
        return True
    except OSError:
        return False


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value))
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _cleanup(
    root: Path, *, retention_days: int, max_bytes: int, raw_max_bytes: int
) -> tuple[int, int]:
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    deleted = 0
    deleted_bytes = 0
    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            deleted += 1
            deleted_bytes += size
    raw_root = root / "raw"
    raw_files = [path for path in root.rglob("*") if path.is_file() and raw_root in path.parents]
    count, size = _trim(raw_files, raw_max_bytes)
    deleted += count
    deleted_bytes += size
    regular = [
        path for path in root.rglob("*") if path.is_file() and raw_root not in path.parents
    ]
    count, size = _trim(regular, max_bytes)
    return deleted + count, deleted_bytes + size


def _trim(files: list[Path], maximum: int) -> tuple[int, int]:
    total = sum(path.stat().st_size for path in files)
    deleted = 0
    deleted_bytes = 0
    for path in sorted(files, key=lambda item: item.stat().st_mtime):
        if total <= maximum:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total -= size
        deleted += 1
        deleted_bytes += size
    return deleted, deleted_bytes
