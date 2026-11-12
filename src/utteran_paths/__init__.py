"""Single resolution path for utteran-managed runtime and user data."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir, user_log_dir

DATA_ROOT_ENV = "UTTERAN_DATA_ROOT"
DISTRIBUTION_ENV = "UTTERAN_DISTRIBUTION"
TOKEN_MODE_ENV = "UTTERAN_TOKEN_MODE"

# G-09 dedicated development data root marker (shared by the GUI guard and core B4
# evidence). Fixed content: no path, user name, time or other private value.
DEV_MARKER_NAME = ".utteran-dev-data-root"
DEV_MARKER_CONTENT = (
    json.dumps(
        {"kind": "utteran-dev-data-root", "purpose": "next-ui-v1", "schema_version": 1},
        sort_keys=True,
    )
    + "\n"
)
_MARKER_READ_LIMIT = 4096
# Interrupted B4 evidence writers may leave ``.<scope>.<random>.tmp`` files behind.
EVIDENCE_TEMP_PATTERN = r"^\.[a-f0-9]{64}\.[a-f0-9]{16}\.tmp$"


@dataclass(frozen=True)
class DataPaths:
    """All application-managed locations resolved from one optional root."""

    root: Path | None
    project_root: Path

    @property
    def portable(self) -> bool:
        return self.root is not None

    @property
    def venvs(self) -> Path:
        return self.root / ".venvs" if self.root else self.project_root / ".venvs"

    @property
    def models(self) -> Path:
        return self.root / "models" if self.root else Path(user_cache_dir("utteran")) / "models"

    @property
    def openvino_genai_cache(self) -> Path:
        return (
            self.root / "cache" / "openvino-genai-compiled"
            if self.root
            else Path(user_cache_dir("utteran")) / "openvino-genai-compiled"
        )

    @property
    def device_probe_cache(self) -> Path:
        return (
            self.root / "cache" / "device-probes-v1.json"
            if self.root
            else Path(user_cache_dir("utteran")) / "device-probes-v1.json"
        )

    @property
    def native(self) -> Path:
        return self.root / "native" if self.root else Path.home() / ".utteran" / "native"

    @property
    def ffmpeg_bin(self) -> Path:
        return self.root / "bin" if self.root else Path(user_data_dir("utteran")) / "bin"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs" if self.root else Path(user_cache_dir("utteran")) / "jobs"

    @property
    def core_config(self) -> Path:
        return (
            self.root / "config" / "config.toml"
            if self.root
            else Path(user_config_dir("utteran")) / "config.toml"
        )

    @property
    def gui_settings(self) -> Path:
        return (
            self.root / "config" / "gui-settings.json"
            if self.root
            else Path(user_config_dir("utteran-gui")) / "settings.json"
        )

    @property
    def logs(self) -> Path:
        return self.root / "logs" if self.root else Path(user_log_dir("utteran"))

    @property
    def settings_resources(self) -> Path:
        return (
            self.root / "data" / "settings-v1" / "resources.sqlite3"
            if self.root
            else Path(user_data_dir("utteran")) / "settings-v1" / "resources.sqlite3"
        )

    @property
    def control_store(self) -> Path:
        """B6 control DB. Separate from settings_resources; created only on explicit use."""
        return (
            self.root / "data" / "control-v1" / "operations.sqlite3"
            if self.root
            else Path(user_data_dir("utteran")) / "control-v1" / "operations.sqlite3"
        )

    @property
    def capability_evidence(self) -> Path | None:
        """B4 private smoke evidence (0.1.50). Only below an explicit data root.

        The platform default layout has no evidence location at all, so default
        user installs can never read or write smoke evidence.
        """
        return self.root / "cache" / "capability-evidence-v1" if self.root else None

    @property
    def memory_calibration(self) -> Path:
        return (
            self.root / "config" / "memory-calibration.json"
            if self.root
            else Path(user_data_dir("utteran")) / "memory-calibration.json"
        )


def resolve_data_paths(project_root: Path | None = None) -> DataPaths:
    """Resolve explicit build-provided data root or unchanged platform defaults."""
    selected_project = (project_root or Path.cwd()).expanduser().resolve()
    configured = os.environ.get(DATA_ROOT_ENV)
    root = Path(configured).expanduser().resolve() if configured else None
    return DataPaths(root=root, project_root=selected_project)


def is_portable_distribution() -> bool:
    """Return the explicit build identity; never infer it from files or paths."""
    return os.environ.get(DISTRIBUTION_ENV) == "portable"


def uses_session_token() -> bool:
    """Return the token policy fixed by the build artifact."""
    return os.environ.get(TOKEN_MODE_ENV) == "session"


def is_marked_dev_root(root: Path) -> bool:
    """True only when ``root`` holds the exact G-09 marker as a regular, non-link file.

    Read-only: never creates the marker (that is ``validate_dev_data_root``'s job).
    """
    marker = root / DEV_MARKER_NAME
    try:
        info = os.lstat(marker)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MARKER_READ_LIMIT:
            return False
        with open(marker, "rb") as source:
            content = source.read(_MARKER_READ_LIMIT + 1)
    except OSError:
        return False
    return content == DEV_MARKER_CONTENT.encode("utf-8")
