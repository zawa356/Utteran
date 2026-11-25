"""E-C file access: read the verified JSON by descriptor, create a new re-export directory.

Threat model: another process of the same user may rename, replace or link any name at any
time (C2c §5.0). Therefore nothing here acts on a name after checking it:

- **Pinning.** Parents are opened component by component from ``/`` with ``O_NOFOLLOW``
  (``_Pinned``), so a component replaced by a link after a name-based check stops the walk.
- **Source.** The job's own directory, its ownership marker and ``<name>.json`` are opened
  relative to directory descriptors with ``O_NOFOLLOW`` (a symlink at any of these names is
  refused). The JSON descriptor must be a regular file with one link and exactly the size
  recorded at completion; its bytes are read once into memory, hashed and compared with the
  completion digest. Those same bytes are what the caller parses (never a re-opened path).
- **Output.** A *new* directory is made with ``mkdir`` relative to the pinned parent
  descriptor (never an existing one; a taken name moves to the next id length), opened with
  ``O_NOFOLLOW`` and checked to be an empty directory of this user. Every file is created
  with ``O_CREAT | O_EXCL | O_NOFOLLOW`` relative to that directory descriptor, so nothing is
  ever overwritten and nothing is written through a link. Files are fsynced, then the
  directory and the parent. Nothing is renamed, replaced or deleted -- also not after a
  failure (partial files stay in the new directory and are reported, never removed by name).

Only POSIX systems with ``dir_fd`` support for ``open``/``mkdir``/``stat`` qualify. Windows
(and anything else) is refused with ``reexport_platform_unsupported``: pinning a directory
and creating files relative to a held handle needs a handle-based design accepted on real
hardware (#59/#22), which does not exist yet. No module here logs.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

#: Largest accepted source JSON (measured, e-c-reexport.md §5): parse + all five renders of a
#: 32 MiB synthetic source take ~4.3 s and ~210 MB extra RSS on Cloud Linux.
MAX_SOURCE_BYTES = 32 * 1024 * 1024
#: Sum of the rendered outputs of one re-export (all formats), checked before any file exists.
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
JOB_MARKER = ".utteran-job"
REEXPORT_MARKER = ".utteran-reexport"
_MARKER_LIMIT = 256
_GIT_TIMEOUT_SECONDS = 10.0


# Absent on Windows (where ``supported()`` is False and nothing below runs).
_O_NOFOLLOW: int = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY: int = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK: int = getattr(os, "O_NONBLOCK", 0)


def _euid() -> int:
    return int(getattr(os, "geteuid", lambda: -1)())


class FileError(Exception):
    """Fixed, value-free failure code (no path, name or OS message)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _probe() -> bool:
    return (
        os.name == "posix"
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_DIRECTORY")
        and {os.open, os.mkdir, os.stat} <= os.supports_dir_fd
        and os.listdir in os.supports_fd
    )


_SUPPORTED = _probe()


def supported() -> bool:
    """True only where every operation below is descriptor-relative (POSIX with dir_fd)."""
    return _SUPPORTED


def _flags(*extra: int) -> int:
    value = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    for item in extra:
        value |= item
    return value


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


Identity = tuple[int, int]


def _identity(info: os.stat_result) -> Identity:
    return (info.st_dev, info.st_ino)


def reaches(path: str, expected: list[Identity]) -> bool:
    """Whether walking ``path`` from ``/`` now, by name and without following any link, meets
    exactly the directories ``expected`` (``/`` first, then every component). Read-only (opens
    directories, writes nothing). Any error or difference is ``False``: this is an observation
    at the moment of the call, never a guarantee about later renames (owner review P2-3)."""
    names = [part for part in path.split("/") if part]
    if len(names) + 1 != len(expected):
        return False
    fds: list[int] = []
    try:
        fds.append(os.open("/", _flags(_O_DIRECTORY)))
        if _identity(os.fstat(fds[-1])) != expected[0]:
            return False
        for index, name in enumerate(names, start=1):
            fds.append(os.open(name, _flags(_O_DIRECTORY, _O_NOFOLLOW), dir_fd=fds[-1]))
            if _identity(os.fstat(fds[-1])) != expected[index]:
                return False
        return True
    except OSError:
        return False
    finally:
        for descriptor in fds:
            os.close(descriptor)


_MAX_DEPTH = 4096


def _ancestry_protected(start: int, protected: set[Identity]) -> bool:
    """Whether ``start`` or any directory above it (followed through ``..`` from the held
    descriptor, never by name) is protected. Run right before ``mkdir`` and again on the new
    directory, so a destination moved *into* the job's directory after it was pinned is
    caught (independent re-review P2-a). An unreadable chain is treated as protected (fail
    closed). A bind mount of a sub-directory of a protected directory is not detectable
    this way (``..`` at a mount root leaves the mount); see e-c-reexport.md §11."""
    if not protected:
        return False
    fds: list[int] = []
    try:
        current = start
        previous: Identity | None = None
        for _ in range(_MAX_DEPTH):
            identity = _identity(os.fstat(current))
            if identity in protected:
                return True
            if identity == previous:
                return False  # ".." of "/" is "/" itself
            previous = identity
            fds.append(os.open("..", _flags(_O_DIRECTORY), dir_fd=current))
            current = fds[-1]
        return True
    except OSError:
        return True
    finally:
        for descriptor in fds:
            os.close(descriptor)


def _network(text: str) -> bool:
    return text.startswith("//")


def check_parent_path(text: str) -> str:
    """The normalized parent of a re-export directory: absolute, local, no link component
    (the B7 output-directory rule). Returns the normalized text."""
    if not isinstance(text, str) or not text or "\x00" in text or not os.path.isabs(text):
        raise FileError("output_parent_invalid")
    if _network(text):
        raise FileError("output_parent_invalid")
    text = os.path.normpath(text)
    path = Path(text)
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            raise FileError("output_parent_missing") from None
        except OSError:
            raise FileError("output_parent_unavailable") from None
        if stat.S_ISLNK(info.st_mode):
            raise FileError("output_parent_link")
        if not stat.S_ISDIR(info.st_mode):
            raise FileError("output_parent_invalid")
    return text


class _Pinned:
    """Every directory of an absolute path opened from ``/`` with ``O_NOFOLLOW`` (PR review
    P2-3): a component replaced by a link after a name-based check stops the walk, so the
    final descriptor is the directory at that path *without* following any link."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.names = [part for part in text.split("/") if part]
        self.fds: list[int] = []
        #: ``(st_dev, st_ino)`` of ``/`` and of every component, read from the held descriptors.
        self.ids: list[Identity] = []
        try:
            self.fds.append(os.open("/", _flags(_O_DIRECTORY)))
            self.ids.append(_identity(os.fstat(self.fds[-1])))
            for name in self.names:
                if name in (".", ".."):
                    raise FileError("output_parent_invalid")
                self.fds.append(
                    os.open(name, _flags(_O_DIRECTORY, _O_NOFOLLOW), dir_fd=self.fds[-1])
                )
                self.ids.append(_identity(os.fstat(self.fds[-1])))
        except OSError:
            self.close()
            raise FileError("output_parent_changed") from None
        except FileError:
            self.close()
            raise

    @property
    def fd(self) -> int:
        return self.fds[-1]

    def close(self) -> None:
        while self.fds:
            os.close(self.fds.pop())


# ---- source ---------------------------------------------------------------------------------


def _read_marker(directory: int, name: str) -> str | None:
    try:
        descriptor = os.open(name, _flags(_O_NOFOLLOW, _O_NONBLOCK), dir_fd=directory)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MARKER_LIMIT:
            return None
        return os.read(descriptor, _MARKER_LIMIT + 1).decode("utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(descriptor)


def _read_exact(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size + 1  # one byte more: a file that grew is detected without a second open
    while remaining > 0:
        block = os.read(descriptor, min(remaining, 1024 * 1024))
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    return b"".join(chunks)


@dataclass(frozen=True)
class Source:
    """The verified JSON bytes and the job directory they were read from."""

    data: bytes
    #: ``(st_dev, st_ino)`` of the job directory whose marker named the job (held at read).
    directory: Identity
    #: Normalized absolute path of that directory at read time.
    path: str


def read_verified_json(
    parent: str, names: Iterable[str], job_id: str, evidence: tuple[str, int]
) -> Source:
    """The bytes of the job's JSON output, proven equal to the completion record.

    ``names``: the candidate directory names of the job (every id length). The directory
    is the one whose marker names ``job_id``; the file is ``<name>.json`` inside it. The
    directory's identity is returned so the destination can be refused inside it.
    """
    if not supported():
        raise FileError("reexport_platform_unsupported")
    digest, size = evidence
    if size > MAX_SOURCE_BYTES:
        raise FileError("source_too_large")
    if not isinstance(parent, str) or not os.path.isabs(parent) or "\x00" in parent:
        raise FileError("source_missing")
    try:
        pinned = _Pinned(os.path.normpath(parent))
    except FileError:
        raise FileError("source_missing") from None
    parent_fd = pinned.fd
    try:
        for name in names:
            try:
                directory = os.open(name, _flags(_O_DIRECTORY, _O_NOFOLLOW), dir_fd=parent_fd)
            except OSError:
                continue  # absent, a link or not a directory: not this job's directory
            try:
                if _read_marker(directory, JOB_MARKER) != job_id:
                    continue
                data = _read_json(directory, f"{name}.json", digest, size)
                try:
                    identity = _identity(os.fstat(directory))
                except OSError:
                    raise FileError("source_unavailable") from None
                return Source(data, identity, os.path.join(pinned.text, name))
            finally:
                os.close(directory)
        raise FileError("source_missing")
    finally:
        pinned.close()


def _read_json(directory: int, name: str, digest: str, size: int) -> bytes:
    try:
        descriptor = os.open(name, _flags(_O_NOFOLLOW, _O_NONBLOCK), dir_fd=directory)
    except FileNotFoundError:
        raise FileError("source_missing") from None
    except OSError as exc:
        raise FileError("source_link" if exc.errno == errno.ELOOP else "source_missing") from None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FileError("source_special")
        if before.st_nlink != 1:
            raise FileError("source_link")  # a hard link: another name could change it
        if before.st_size > MAX_SOURCE_BYTES:
            raise FileError("source_too_large")
        if before.st_size != size:
            raise FileError("source_changed")
        data = _read_exact(descriptor, size)
        after = os.fstat(descriptor)
    except OSError:
        raise FileError("source_unavailable") from None
    finally:
        os.close(descriptor)
    if (
        len(data) != size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or hashlib.sha256(data).hexdigest() != digest
    ):
        raise FileError("source_changed")
    return data


# ---- output ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Written:
    name: str
    files: tuple[tuple[str, str, int], ...]  # (format, sha256, size)
    location_verified: bool


GitCheck = Callable[["_Pinned", str, list[str]], None]


#: Inherited variables that would make Git read another repository or ignore list.
_GIT_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_NAMESPACE",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
)


def _git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _GIT_VARIABLES and not key.startswith("GIT_CONFIG_")
    }
    # GIT_LITERAL_PATHSPECS is not set: check-ignore refuses it (exit 128). Pathspec magic
    # cannot occur anyway (":" is replaced in every stem) and would fail closed as 128.
    return environment


def output_files(name: str, extensions: Iterable[str]) -> list[str]:
    """Every name created inside a new re-export directory ``name``: the marker and
    ``<name>.<ext>`` per format. The Git check judges exactly these (owner review P2-1)."""
    return [REEXPORT_MARKER, *(f"{name}.{extension}" for extension in extensions)]


def git_check(pinned: _Pinned, name: str, files: list[str]) -> None:
    """Refuse a destination inside a Git checkout unless ``.gitignore`` ignores **every file
    that will be created** (``files``: names inside the new directory ``name``, the marker
    included). Each path is asked separately (``check-ignore --quiet`` answers "ignored" if
    any one of several paths is), so a narrow pattern or a negation that leaves one real file
    visible refuses the destination.

    The checkout is found through the *pinned* ancestor descriptors (never by name), and Git
    runs in that held directory (``/proc/self/fd/<n>``), so a swapped path component cannot
    hide a checkout. Where the held directory cannot be handed to Git (no ``/proc``), a
    checkout fails closed. Git's own answer is lexical (``--no-index``): it reads the
    ``.gitignore`` files of the checkout, nothing is created to ask it.
    """
    found = None
    for index in range(len(pinned.fds) - 1, -1, -1):
        try:
            os.stat(".git", dir_fd=pinned.fds[index], follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            raise FileError("output_git_unverified") from None
        found = index
        break
    if found is None:
        return
    repository = pinned.fds[found]
    if not os.path.isdir(f"/proc/self/fd/{repository}"):
        raise FileError("output_git_unverified")
    below = pinned.names[found:]
    if not files:
        raise FileError("output_git_unverified")
    for file_name in files:
        probe = "/".join([*below, name, file_name])
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", "--", probe],
                cwd=f"/proc/self/fd/{repository}",
                pass_fds=(repository,),
                env=_git_environment(),
                check=False,
                capture_output=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError):
            raise FileError("output_git_unverified") from None
        if result.returncode == 1:
            raise FileError("output_in_repository")
        if result.returncode != 0:
            raise FileError("output_git_unverified")


def _os_code(exc: OSError) -> str:
    if exc.errno in (errno.ENOSPC, errno.EDQUOT):
        return "output_full"
    if exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
        return "output_readonly"
    return "output_io_error"


def _create(directory: int, name: str, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class PartialOutput(FileError):
    """Failure after the new directory was made: files written so far stay (not deleted)."""

    def __init__(self, code: str, name: str) -> None:
        super().__init__(code)
        self.name = name


@dataclass(frozen=True)
class Protected:
    """A directory no re-export may be written into or below: the job's own output
    directory as it was verified (identity) and its path at that time (name)."""

    identity: Identity
    path: str


class Destination:
    """A parent directory pinned for one re-export (held across the record's commit)."""

    def __init__(self, parent: str, protected: Iterable[Protected] = ()) -> None:
        if not supported():
            raise FileError("reexport_platform_unsupported")
        self.parent = check_parent_path(parent)
        guarded = list(protected)
        # By name first: the path or a descendant of a protected path (owner review P2-2),
        # also when another directory now has that name.
        for item in guarded:
            if self.parent == item.path or self.parent.startswith(item.path.rstrip("/") + "/"):
                raise FileError("output_inside_source")
        self.pinned = _Pinned(self.parent)  # O_NOFOLLOW from "/" (no link at any component)
        # By identity: no pinned component may be a protected directory, whatever its name
        # now (moved, renamed). The held descriptors cannot change identity later.
        self.protected = {item.identity for item in guarded}
        if any(identity in self.protected for identity in self.pinned.ids):
            self.close()
            raise FileError("output_inside_source")

    def close(self) -> None:
        self.pinned.close()

    def check_git(self, names: Iterable[str], extensions: Iterable[str], git: GitCheck) -> None:
        """Every candidate directory name, before any record or directory exists."""
        extensions = list(extensions)
        for name in names:
            git(self.pinned, name, output_files(name, extensions))


def create_output(
    parent: str | Destination,
    names: Iterable[str],
    marker: str,
    files: Mapping[str, bytes],
    *,
    on_claimed: Callable[[str], object],
    git: GitCheck = git_check,
    protected: Iterable[Protected] = (),
) -> Written:
    """Make a new directory under ``parent`` and write ``files`` (``ext -> bytes``) into it.

    ``parent`` is a path (pinned here, then closed) or a :class:`Destination` the caller
    pinned and checked (left open for the caller to close). ``on_claimed(name)`` runs after
    the directory exists and before any file is written (the caller records the name first,
    so a crash afterwards is attributable). Errors before the directory exists raise
    :class:`FileError` (nothing was created); later errors raise :class:`PartialOutput` with
    the directory name. ``location_verified`` is the observation, after writing, that the
    shown absolute path still walks to the very directories that were written (§4).
    """
    if not supported():
        raise FileError("reexport_platform_unsupported")
    owned = not isinstance(parent, Destination)
    destination = Destination(parent, protected) if isinstance(parent, str) else parent
    pinned = destination.pinned
    parent_fd = pinned.fd
    try:
        claimed: str | None = None
        for name in names:
            # Before the directory exists, so a refusal leaves nothing behind (again here for
            # a destination checked earlier: the checkout's ignore rules may have changed).
            git(pinned, name, output_files(name, files))
            # Moved into the job's directory since it was pinned? Checked through "..".
            if _ancestry_protected(parent_fd, destination.protected):
                raise FileError("output_inside_source")
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue  # taken (by anything): never reuse, try the longer id form
            except OSError as exc:
                raise FileError(_os_code(exc)) from None
            claimed = name
            break
        if claimed is None:
            raise FileError("output_name_unavailable")
        # From here on the directory exists: every failure is reported with its name.
        try:
            directory = os.open(claimed, _flags(_O_DIRECTORY, _O_NOFOLLOW), dir_fd=parent_fd)
        except OSError:
            raise PartialOutput("output_claim_lost", claimed) from None
        try:
            try:
                info = os.fstat(directory)
                entries = os.listdir(directory)
            except OSError:
                raise PartialOutput("output_claim_lost", claimed) from None
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != _euid() or entries:
                # Not the empty directory we made (replaced in between): write nothing.
                raise PartialOutput("output_claim_lost", claimed)
            if _ancestry_protected(directory, destination.protected):
                # Moved into the job's directory between the check and mkdir: no file is
                # written (the empty directory stays; nothing is deleted).
                raise PartialOutput("output_inside_source", claimed)
            on_claimed(claimed)
            records: list[tuple[str, str, int]] = []
            try:
                _create(directory, REEXPORT_MARKER, (marker + "\n").encode("ascii"))
                for extension, data in files.items():
                    _create(directory, f"{claimed}.{extension}", data)
                    records.append((extension, hashlib.sha256(data).hexdigest(), len(data)))
                os.fsync(directory)
                os.fsync(parent_fd)
            except FileExistsError:
                raise PartialOutput("output_claim_lost", claimed) from None
            except OSError as exc:
                raise PartialOutput(_os_code(exc), claimed) from None
            # The shown path, walked again by name: every ancestor, the parent and the new
            # directory must be the ones held while writing (owner review P2-3).
            verified = reaches(
                os.path.join(destination.parent, claimed), [*pinned.ids, _identity(info)]
            )
            return Written(claimed, tuple(records), verified)
        finally:
            os.close(directory)
    finally:
        if owned:
            destination.close()
