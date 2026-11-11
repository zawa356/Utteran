"""GUI startup guard: launch mode, development data root and per-root process lock (G-09).

Standard library only (plus ``utteran_paths``), so the default legacy startup gains no
heavy import. Three things live here, all evaluated *before* any FastAPI application,
control/settings store or job runtime is constructed:

- :func:`launch_mode` -- exact-argv mode selection (legacy / ``--next-ui`` / ``--next-ui-v1``).
- :func:`validate_dev_data_root` -- ``--next-ui-v1`` only: the explicit ``UTTERAN_DATA_ROOT``
  must be a dedicated development root, identified by a fixed marker file that is created
  only inside an empty, already existing directory. Nothing is ever deleted or initialised.
- :class:`GuiInstanceLock` -- one OS-level exclusive lock per canonical data root, taken by
  every GUI launch mode. The OS releases it when the process ends (also on a crash or kill),
  so a stale file never blocks a later start. The lock file is never unlinked.

Scope (do not overstate): only cooperating GUI processes of this version take the lock.
The CLI, older binaries and other non-cooperating processes are not excluded.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Literal

from utteran_paths import DATA_ROOT_ENV, DISTRIBUTION_ENV

LaunchMode = Literal["legacy", "next_ui", "next_ui_v1"]

NEXT_UI_FLAG = "--next-ui"
# Temporary development-only flag (G-09). Never selected by settings, environment or
# migration; removal condition is recorded in docs/architecture/ui2/migration-next.md.
NEXT_UI_V1_FLAG = "--next-ui-v1"

LOCK_FILE_NAME = "gui-instance.lock"
DEV_MARKER_NAME = ".utteran-dev-data-root"
# Fixed marker content: no path, user name, time or other private value.
DEV_MARKER_CONTENT = (
    json.dumps(
        {"kind": "utteran-dev-data-root", "purpose": "next-ui-v1", "schema_version": 1},
        sort_keys=True,
    )
    + "\n"
)

RefusalCode = Literal[
    "dev_root_required",
    "dev_root_distribution",
    "dev_root_not_absolute",
    "dev_root_missing",
    "dev_root_reserved",
    "dev_root_not_marked",
    "dev_root_unavailable",
    "gui_already_running",
    "gui_lock_unavailable",
]

# Fixed, value-free explanations (no path, token or argv). Shown on refusal.
REFUSAL_MESSAGES: dict[str, str] = {
    "dev_root_required": (
        "--next-ui-v1 requires UTTERAN_DATA_ROOT to name a dedicated development data root."
    ),
    "dev_root_distribution": (
        "--next-ui-v1 is development-only and is refused for distributed/packaged builds."
    ),
    "dev_root_not_absolute": "UTTERAN_DATA_ROOT must be an absolute path for --next-ui-v1.",
    "dev_root_missing": (
        "The development data root must be an existing directory (it is never created)."
    ),
    "dev_root_reserved": (
        "The development data root must not be the home directory, a filesystem root or "
        "the default user data location (or inside/around it)."
    ),
    "dev_root_not_marked": (
        "The development data root is not empty and has no development marker. "
        "Use a new empty directory; existing data is never adopted or initialised."
    ),
    "dev_root_unavailable": "The development data root could not be inspected.",
    "gui_already_running": (
        "Another Utteran GUI is already running with the same data root. "
        "Close it first (a second GUI on one data root is refused)."
    ),
    "gui_lock_unavailable": "The GUI instance lock for this data root could not be opened.",
}


class StartupRefused(Exception):
    """A startup precondition failed. Carries a fixed code only."""

    def __init__(self, code: RefusalCode) -> None:
        self.code: RefusalCode = code
        super().__init__(code)

    @property
    def message(self) -> str:
        return REFUSAL_MESSAGES[self.code]


def launch_mode(argv: list[str]) -> LaunchMode:
    """Exact match only. Anything else keeps the legacy default (never rounded to v1)."""
    arguments = argv[1:]
    if arguments == [NEXT_UI_FLAG]:
        return "next_ui"
    if arguments == [NEXT_UI_V1_FLAG]:
        return "next_ui_v1"
    return "legacy"


# --------------------------------------------------------------------------- data root


def _canonical(path: Path) -> Path:
    """Resolve symlinks, junctions and ``..`` so aliases name one directory."""
    return Path(os.path.realpath(path))


def _same_or_related(candidate: Path, reserved: Path) -> bool:
    """True when ``candidate`` equals, contains or lies inside ``reserved``."""
    try:
        reserved = _canonical(reserved)
    except (OSError, ValueError):
        return False
    return candidate == reserved or candidate in reserved.parents or reserved in candidate.parents


def reserved_locations() -> list[Path]:
    """Default user data locations a development root must stay away from."""
    from platformdirs import user_cache_dir, user_config_dir, user_data_dir, user_log_dir

    return [
        Path(user_data_dir("utteran")),
        Path(user_config_dir("utteran")),
        Path(user_cache_dir("utteran")),
        Path(user_log_dir("utteran")),
        Path(user_config_dir("utteran-gui")),
        Path.home() / ".utteran",
    ]


def _is_distribution() -> bool:
    # Any explicit build identity (portable, installer, ...) or a frozen executable.
    return bool(os.environ.get(DISTRIBUTION_ENV)) or bool(getattr(sys, "frozen", False))


def validate_dev_data_root(environ: dict[str, str] | None = None) -> Path:
    """Return the canonical development root for ``--next-ui-v1`` or refuse.

    First use: an existing, *empty* directory receives the fixed marker (exclusive create).
    Later uses: only a directory whose marker matches exactly is accepted. A non-empty
    directory without the marker is refused, so real or portable data is never adopted.
    """
    env = os.environ if environ is None else environ
    if _is_distribution():
        raise StartupRefused("dev_root_distribution")
    configured = env.get(DATA_ROOT_ENV, "")
    if not configured.strip():
        raise StartupRefused("dev_root_required")
    raw = Path(configured).expanduser()
    if not raw.is_absolute():
        raise StartupRefused("dev_root_not_absolute")
    try:
        root = _canonical(raw)
        is_dir = root.is_dir()
    except (OSError, ValueError):
        raise StartupRefused("dev_root_missing") from None
    if not is_dir:
        raise StartupRefused("dev_root_missing")
    home = _canonical(Path.home())
    # A filesystem root, the home directory or any ancestor of it is never a dedicated
    # root. A directory *below* home (e.g. a scratch folder) is allowed.
    if root == Path(root.anchor) or root == home or root in home.parents:
        raise StartupRefused("dev_root_reserved")
    if any(_same_or_related(root, location) for location in reserved_locations()):
        raise StartupRefused("dev_root_reserved")
    marker = root / DEV_MARKER_NAME
    try:
        if marker.is_file() and not marker.is_symlink():
            if marker.read_text(encoding="utf-8") != DEV_MARKER_CONTENT:
                raise StartupRefused("dev_root_not_marked")
            return root
        if os.path.lexists(marker):
            raise StartupRefused("dev_root_not_marked")
        with os.scandir(root) as entries:
            if next(entries, None) is not None:
                raise StartupRefused("dev_root_not_marked")
        _create_marker(marker)
    except StartupRefused:
        raise
    except OSError:
        raise StartupRefused("dev_root_unavailable") from None
    return root


def _create_marker(marker: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(marker, flags, 0o600)
    except FileExistsError:
        # A concurrent first start won the race: accept only a complete, exact marker.
        if marker.read_text(encoding="utf-8") != DEV_MARKER_CONTENT:
            raise StartupRefused("dev_root_not_marked") from None
        return
    with os.fdopen(descriptor, "wb") as file:
        file.write(DEV_MARKER_CONTENT.encode("utf-8"))
        file.flush()
        os.fsync(file.fileno())


def lock_directory(data_root: Path | None) -> Path:
    """Canonical directory holding the lock for one logical data root.

    ``None`` is the platform default layout (no ``UTTERAN_DATA_ROOT``); its lock lives in
    the default user data directory, next to where the control store would be.
    """
    if data_root is None:
        from platformdirs import user_data_dir

        return Path(user_data_dir("utteran"))
    return data_root


# --------------------------------------------------------------------------- lock


class GuiInstanceLock:
    """Exclusive, non-blocking OS lock on ``<data root>/gui-instance.lock``.

    POSIX uses ``flock`` on an open file description; Windows locks byte 0 with
    ``msvcrt.locking``. Both are released by the OS when the descriptor is closed or the
    process ends. The descriptor is non-inheritable (PEP 446, plus ``O_CLOEXEC``), so a
    child process never keeps the lock alive after the GUI exits.
    """

    def __init__(self, directory: Path) -> None:
        self.path = _canonical(directory) / LOCK_FILE_NAME
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self, *, create_parent: bool = False) -> GuiInstanceLock:
        if self._fd is not None:
            raise RuntimeError("lock already held by this object")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
        try:
            if create_parent:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, flags, 0o600)
        except OSError:
            raise StartupRefused("gui_lock_unavailable") from None
        os.set_inheritable(fd, False)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            raise StartupRefused("gui_already_running") from None
        self._fd = fd
        return self

    def release(self) -> None:
        """Release (idempotent). The file stays: unlinking would let two holders coexist."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        except OSError:
            pass  # closing the descriptor below releases the lock in every case
        finally:
            os.close(fd)

    def __enter__(self) -> GuiInstanceLock:
        return self if self.held else self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()


if sys.platform == "win32":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        # LK_NBLCK: one attempt, raises OSError when another process holds byte 0.
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
