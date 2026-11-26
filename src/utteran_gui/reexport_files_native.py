"""E-C2b file access on the Rust core (``utteran_native_fs``), used on Windows (#87).

Same contract and fixed codes as :mod:`utteran_gui.reexport_files` (the POSIX
implementation, which stays the Linux/macOS path):

- **Source.** The job directory is walked from the volume root without following any link or
  reparse point; the marker and ``<name>.json`` are read through one handle each. The JSON
  must be a regular single-link file of the recorded size; its bytes are read once (Windows:
  ``FILE_SHARE_READ`` only, so nobody writes, renames or deletes it meanwhile), hashed and
  compared with the completion digest, and exactly those bytes are parsed.
- **Output.** Every ancestor of the destination is held without ``FILE_SHARE_DELETE`` (it
  cannot be renamed or deleted while held). The new directory is created *and* opened by one
  ``NtCreateFile(FILE_CREATE)``; the marker is written first (a non-empty directory cannot
  be turned into a junction), then every file with an exclusive create. Before each file the
  held handles are re-checked (still the same directories, none became a reparse point) and
  the shown path must still reach them. Nothing is overwritten, renamed or deleted, also not
  after a failure (partial files stay and are reported).
- **Git.** Any ``.git`` (directory, file or link) in a held ancestor refuses the destination
  (``output_in_repository``); ``.gitignore`` is not consulted on this path (owner decision).

Only local NTFS is accepted (the core refuses UNC/SMB, SUBST/per-logon drive letters,
FAT/exFAT/ReFS and reparse points). Without the native module the platform stays
``reexport_platform_unsupported``: there is no path-based fallback. No module here logs.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from utteran_gui.reexport_files import (
    JOB_MARKER,
    MAX_OUTPUT_BYTES,
    MAX_SOURCE_BYTES,
    REEXPORT_MARKER,
    FileError,
    PartialOutput,
    Protected,
    Source,
    Written,
    output_files,
)

__all__ = [
    "MAX_OUTPUT_BYTES",
    "MAX_SOURCE_BYTES",
    "Destination",
    "FileError",
    "PartialOutput",
    "Protected",
    "create_output",
    "git_check",
    "read_verified_json",
    "supported",
]

import importlib


def _load() -> Any:
    try:
        return importlib.import_module("utteran_native_fs")
    except ImportError:  # not bundled: the platform stays unsupported (fail closed)
        return None


_native: Any = _load()

_MARKER_LIMIT = 256


def supported() -> bool:
    """Windows stays disabled pending all mandatory VM acceptance items.

    A loaded wheel enables the POSIX regression path only.
    """
    # #22: every mandatory Windows VM acceptance item must pass before release.
    # W-07/W-11..14/W-23 remain unverified; a wheel alone cannot enable this path.
    if sys.platform == "win32" or _native is None:
        return False
    return True  # used on POSIX only by the regression tests


def _code(exc: BaseException) -> str:
    return str(exc.args[0]) if exc.args else "io"


def _norm(path: str) -> str:
    # By-name check only (the identity check is the real guard): drop a verbatim prefix so
    # `\\?\C:\x` and `C:\x` compare equal. 8.3 short names are not expanded here.
    if path.startswith("\\\\?\\"):
        path = path[4:]
    return os.path.normcase(os.path.normpath(path))


# ---- source ---------------------------------------------------------------------------------

_SOURCE_CODES = {
    "not_found": "source_missing",
    "link_or_reparse": "source_link",
    "hard_linked": "source_link",
    "name_aliased": "source_link",
    "not_a_regular_file": "source_special",
    "too_large": "source_too_large",
    "changed_during_read": "source_changed",
}


def _read_marker(chain: Any) -> str | None:
    try:
        data = chain.read_file(JOB_MARKER, _MARKER_LIMIT)
        return str(data.decode("utf-8").strip())
    except (_native.SafeFsError, UnicodeDecodeError):
        return None


def read_verified_json(
    parent: str, names: Iterable[str], job_id: str, evidence: tuple[str, int]
) -> Source:
    """The bytes of the job's JSON output, proven equal to the completion record."""
    if not supported():
        raise FileError("reexport_platform_unsupported")
    digest, size = evidence
    if size > MAX_SOURCE_BYTES:
        raise FileError("source_too_large")
    if not isinstance(parent, str) or not os.path.isabs(parent) or "\x00" in parent:
        raise FileError("source_missing")
    base = os.path.normpath(parent)
    for name in names:
        try:
            chain = _native.DirChain.open(os.path.join(base, name))
        except _native.SafeFsError:
            continue  # absent, a link/reparse point or not a directory: not this job's
        with chain:
            if _read_marker(chain) != job_id:
                continue
            try:
                data, _meta = chain.read_file_with_metadata(f"{name}.json", MAX_SOURCE_BYTES)
            except _native.SafeFsError as exc:
                raise FileError(_SOURCE_CODES.get(_code(exc), "source_unavailable")) from None
            if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
                raise FileError("source_changed")
            try:
                volume, file_id = chain.identities()[-1]
            except _native.SafeFsError:
                raise FileError("source_unavailable") from None
            return Source(bytes(data), (int(volume), int(file_id)), os.path.join(base, name))
    raise FileError("source_missing")


# ---- output ---------------------------------------------------------------------------------

_PARENT_CODES = {
    "not_found": "output_parent_missing",
    "link_or_reparse": "output_parent_link",
    "name_aliased": "output_parent_link",
    "busy": "output_parent_unavailable",
    "permission_denied": "output_parent_unavailable",
}


def check_parent_path(text: str) -> str:
    """Absolute and local (no UNC / device namespace). The core refuses everything else
    (links, reparse points, remote or non-NTFS volumes, per-logon drive letters)."""
    if not isinstance(text, str) or not text or "\x00" in text or not os.path.isabs(text):
        raise FileError("output_parent_invalid")
    if text.startswith(("\\\\", "//")):
        raise FileError("output_parent_invalid")
    return os.path.normpath(text)


def _write_code(exc: BaseException) -> str:
    code = _code(exc)
    os_code = exc.args[1] if len(exc.args) > 1 else None
    if code == "already_exists":
        return "output_claim_lost"
    if code == "permission_denied":
        return "output_readonly"
    # ERROR_DISK_FULL 112, ERROR_HANDLE_DISK_FULL 39, ENOSPC 28, EDQUOT 122.
    if os_code in (112, 39, 28, 122):
        return "output_full"
    return "output_io_error"


class Destination:
    """A parent directory held for one re-export (every ancestor held)."""

    def __init__(self, parent: str, protected: Iterable[Protected] = ()) -> None:
        if not supported():
            raise FileError("reexport_platform_unsupported")
        self.parent = check_parent_path(parent)
        guarded = list(protected)
        mine = _norm(self.parent)
        for item in guarded:
            theirs = _norm(item.path)
            if mine == theirs or mine.startswith(theirs.rstrip("\\/") + os.sep):
                raise FileError("output_inside_source")
        try:
            self.chain = _native.DirChain.open(self.parent)
        except _native.SafeFsError as exc:
            raise FileError(_PARENT_CODES.get(_code(exc), "output_parent_invalid")) from None
        self.protected = {item.identity for item in guarded}
        try:
            self.ids = [(int(v), int(f)) for v, f in self.chain.identities()]
        except _native.SafeFsError:
            self.close()
            raise FileError("output_parent_unavailable") from None
        if any(identity in self.protected for identity in self.ids):
            self.close()
            raise FileError("output_inside_source")

    def close(self) -> None:
        self.chain.close()

    def check_git(self, names: Iterable[str], extensions: Iterable[str], git: GitCheck) -> None:
        extensions = list(extensions)
        for name in names:
            git(self, name, output_files(name, extensions))


GitCheck = Callable[[Destination, str, list[str]], None]


def git_check(destination: Destination, name: str, files: list[str]) -> None:
    """Refuse any destination with ``.git`` in a held ancestor (Windows policy: no
    ``.gitignore`` exception). Observed through the held handles, never by name."""
    del name, files
    try:
        found = destination.chain.find_in_ancestors(".git")
    except _native.SafeFsError:
        raise FileError("output_git_unverified") from None
    if found is not None:
        raise FileError("output_in_repository")


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
    Same contract as :func:`utteran_gui.reexport_files.create_output`, except that the
    marker is written before ``on_claimed`` (so the new directory is never empty while the
    record commits)."""
    if not supported():
        raise FileError("reexport_platform_unsupported")
    owned = not isinstance(parent, Destination)
    destination = Destination(parent, protected) if isinstance(parent, str) else parent
    chain = destination.chain
    try:
        claimed: str | None = None
        for name in names:
            git(destination, name, output_files(name, files))
            try:
                chain.push_new(name)
            except _native.SafeFsError as exc:
                if _code(exc) == "already_exists":
                    continue  # taken (by anything): never reuse, try the longer id form
                raise FileError(_write_code(exc)) from None
            claimed = name
            break
        if claimed is None:
            raise FileError("output_name_unavailable")

        def still_in_place() -> None:
            try:
                chain.verify_held()  # same directories, none turned into a reparse point
            except _native.SafeFsError:
                raise PartialOutput("output_claim_lost", claimed) from None
            try:
                chain.verify_reachable()  # the shown path still reaches the held chain
            except _native.SafeFsError:
                raise PartialOutput("output_moved", claimed) from None

        records: list[tuple[str, str, int]] = []
        try:
            chain.verify_held()  # the new directory is still ours and not a reparse point
        except _native.SafeFsError:
            on_claimed(claimed)  # record the name first: the empty directory stays
            raise PartialOutput("output_claim_lost", claimed) from None
        try:
            chain.write_new_file(REEXPORT_MARKER, (marker + "\n").encode("ascii"))
        except _native.SafeFsError as exc:
            # Record the name before reporting, so every partial directory is attributable
            # (as on the POSIX path, where the claim precedes the marker).
            on_claimed(claimed)
            raise PartialOutput(_write_code(exc), claimed) from None
        on_claimed(claimed)
        for extension, data in files.items():
            still_in_place()
            try:
                chain.write_new_file(f"{claimed}.{extension}", data)
            except _native.SafeFsError as exc:
                raise PartialOutput(_write_code(exc), claimed) from None
            records.append((extension, hashlib.sha256(data).hexdigest(), len(data)))
        try:
            chain.sync()  # POSIX fsync of the new directory; no-op on Windows (dir_flush)
        except _native.SafeFsError as exc:
            raise PartialOutput(_write_code(exc), claimed) from None
        try:
            chain.verify_held()
            chain.verify_reachable()
            verified = True
        except _native.SafeFsError:
            verified = False
        return Written(claimed, tuple(records), verified)
    finally:
        if owned:
            destination.close()
