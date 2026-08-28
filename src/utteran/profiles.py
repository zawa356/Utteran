"""Per-profile venv layout, discovery, and default-profile resolution.

Phase 3a introduces one independent venv per hardware profile (see
`要件定義.md` 15章) because a single virtual environment can only hold one
torch build (CPU / CUDA / XPU) at a time. This module only resolves paths and
inspects already-created venvs; it never creates, syncs, or deletes one -
that remains the responsibility of `setup.ps1` (Windows) and `run.ps1`
(execution). Keeping this module process-invocation-free lets it stay
importable and unit-testable without a real venv on disk.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from utteran.errors import ConfigurationError

#: extras synced for each profile, matching 要件定義.md 15章.
PROFILE_EXTRAS: dict[str, tuple[str, ...]] = {
    "cpu": ("cpu", "japanese"),
    "cuda": ("cuda", "japanese"),
    "intel": ("xpu", "whisper-cpp", "openvino", "japanese"),
    "vulkan": ("cpu", "whisper-cpp", "japanese"),
}
PROFILE_NAMES: tuple[str, ...] = tuple(PROFILE_EXTRAS)

_ENV_CURRENT_PROFILE = "UTTERAN_PROFILE"
_ENV_VENV_DIR = "UTTERAN_VENV_DIR"


class UnknownProfileError(ConfigurationError):
    """A profile name outside PROFILE_NAMES was requested."""


def validate_profile_name(name: str) -> str:
    """Return the profile name unchanged, or raise for an unregistered one."""
    if name not in PROFILE_EXTRAS:
        known = ", ".join(PROFILE_NAMES)
        raise UnknownProfileError(f"未登録のプロファイルです: {name} (既知: {known})")
    return name


def os_slug() -> str:
    """Return the directory prefix that keeps Windows and WSL venvs apart.

    WSL and Windows can open the same checkout (typically via a /mnt/c
    mount), and each needs its own interpreter/venv layout even though the
    directory tree is shared. Any non-Windows platform.system() is treated
    as "linux" for this purpose; the goal is only to distinguish the two
    OSes that can realistically share one checkout on one filesystem.
    """
    return "win" if platform.system() == "Windows" else "linux"


def profile_extras(profile: str) -> tuple[str, ...]:
    """Return the extras synced for one validated profile name."""
    return PROFILE_EXTRAS[validate_profile_name(profile)]


def default_venv_root(repo_root: Path) -> Path:
    """Return the documented repository-relative default: `<repo>/.venvs`."""
    return repo_root / ".venvs"


def resolve_venv_root(
    repo_root: Path,
    *,
    configured: Path | None = None,
) -> Path:
    """Resolve explicit config > UTTERAN_VENV_DIR > repository-relative default."""
    if configured is not None:
        return configured.expanduser()
    if environment := os.environ.get(_ENV_VENV_DIR):
        return Path(environment).expanduser()
    return default_venv_root(repo_root)


def venv_dir_name(profile: str) -> str:
    """Return the `<os>-<profile>` directory name for one validated profile."""
    return f"{os_slug()}-{validate_profile_name(profile)}"


def venv_path(venv_root: Path, profile: str) -> Path:
    """Return the venv directory for one profile below a resolved venv root."""
    return venv_root / venv_dir_name(profile)


def venv_python(venv_root: Path, profile: str) -> Path:
    """Return the profile venv's interpreter path for the current OS."""
    root = venv_path(venv_root, profile)
    if platform.system() == "Windows":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


@dataclass(frozen=True)
class ProfileStatus:
    """One profile's on-disk presence, size, and freshness."""

    name: str
    extras: tuple[str, ...]
    path: Path
    exists: bool
    size_bytes: int | None
    updated_at: str | None


def current_profile_name() -> str | None:
    """Return the profile `run.ps1` recorded via UTTERAN_PROFILE, if any.

    Returns None when a process was started outside `run.ps1` (e.g. a bare
    `uv run utteran ...`); callers must treat that as "unknown", not as a
    missing profile error.
    """
    value = os.environ.get(_ENV_CURRENT_PROFILE)
    return value if value in PROFILE_EXTRAS else None


def default_profile_name(configured: str | None, venv_root: Path) -> str:
    """Resolve config default > the sole existing profile > an actionable error."""
    if configured is not None:
        return validate_profile_name(configured)
    existing = [name for name in PROFILE_NAMES if venv_path(venv_root, name).is_dir()]
    if len(existing) == 1:
        return existing[0]
    if not existing:
        raise ConfigurationError(
            "作成済みのプロファイルがありません。`setup.ps1 -Profile <名前>` を実行してください。"
        )
    joined = ", ".join(sorted(existing))
    raise ConfigurationError(
        f"既定プロファイルが未設定で、複数のプロファイルが存在します ({joined})。"
        "`--profile` または config.toml の [general].default_profile を指定してください。"
    )


def list_profile_statuses(venv_root: Path) -> list[ProfileStatus]:
    """Report every known profile's presence, disk usage, and last update."""
    statuses: list[ProfileStatus] = []
    for name in PROFILE_NAMES:
        path = venv_path(venv_root, name)
        exists = path.is_dir()
        statuses.append(
            ProfileStatus(
                name=name,
                extras=PROFILE_EXTRAS[name],
                path=path,
                exists=exists,
                size_bytes=_directory_size(path) if exists else None,
                updated_at=_updated_at(path) if exists else None,
            )
        )
    return statuses


def _directory_size(path: Path) -> int:
    """Return the sum of regular file sizes below a directory."""
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _updated_at(path: Path) -> str | None:
    """Return an ISO timestamp for the venv's most recent modification."""
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).astimezone().isoformat()
    except OSError:
        return None
