"""E-B ``/api/v1/worksets`` on the existing v1 sub-app (same session/Host/Origin boundary).

Mounted only by ``create_app(enable_v1_settings=True)`` (``--next-ui-v1``); the legacy GUI, the
legacy ``--next-ui`` and the CLI never import this module. Responses carry private values only
in authenticated bodies; errors carry fixed codes only (no path, name, term or state value),
and nothing here logs a request or response body.
"""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from utteran_gui.worksets_model import MAX_INPUTS, MAX_PATH, MAX_STATE_BYTES
from utteran_gui.worksets_store import WorksetError, WorksetStore
from utteran_settings.errors import SettingsError

#: The largest state plus its small envelope (name, schema_version).
MAX_WORKSET_BODY = MAX_STATE_BYTES + 64 * 1024
INPUT_STATUS_DEADLINE_SECONDS = 3.0
_ID = re.compile(r"ws_[0-9a-f]{32}")
_IF_MATCH = re.compile(r'"([1-9][0-9]{0,17})"')


def _workset_id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise WorksetError("workset_not_found", 404, retryable=False)
    return value


def _expected(request: Request, *, allow_zero: bool = False) -> int:
    value = request.headers.get("if-match")
    if value is None:
        raise WorksetError("if_match_required", 428, retryable=False)
    if allow_zero and value == '"0"':
        return 0
    match = _IF_MATCH.fullmatch(value)
    if match is None:
        raise WorksetError("if_match_invalid", 400, retryable=False)
    return int(match.group(1))


def _key(request: Request) -> str:
    key = request.headers.get("idempotency-key", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{16,128}", key):
        raise WorksetError("idempotency_key_invalid", 400, retryable=False)
    return key


async def _json(request: Request, limit: int = MAX_WORKSET_BODY) -> dict[str, Any]:
    from utteran_gui.settings_api import read_json_body

    try:
        body = await read_json_body(request, limit)
    except SettingsError as exc:
        raise WorksetError(exc.code, exc.status, retryable=False) from None
    if not isinstance(body, dict):
        raise WorksetError("malformed_json", 400, retryable=False)
    version = body.get("schema_version")
    if type(version) is not int or version != 1:
        raise WorksetError("schema_version_unsupported", 422, retryable=False)
    return body


def _only(body: dict[str, Any], *names: str) -> None:
    if set(body) - {"schema_version", *names}:
        raise WorksetError("contract_validation_failed", 422, retryable=False)


def _resource(row: Any, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        row.to_wire(),
        status_code=status,
        headers={"ETag": f'"{row.revision}"', **(headers or {})},
    )


# ---- read-only input existence check --------------------------------------------------------
#
# Contract: never touch a network location (no SMB/NFS round trip, no credential prompt).
# Everything is decided *before* the first system call that could reach one, and anything that
# cannot be proven local is ``unchecked`` (fail closed: ``unchecked`` never blocks a start).
#
# - Windows: UNC/device names (``\\server``, ``\\?\``, ``//``) are refused by name; a drive
#   letter is checked with ``GetDriveTypeW`` (a pure local query) and only fixed, removable and
#   RAM drives are used. Mapped network drives (``Z:\``), CD/DVD, unknown and absent roots stay
#   ``unchecked``. Each component is then read with ``lstat`` (does not follow links); a reparse
#   point (symlink, junction, mount point) anywhere on the way stops the check.
# - Linux: the mount that holds the path is found by name in ``/proc/self/mounts`` (no symlink
#   resolution needed because no component checked so far was a link). Network and FUSE file
#   systems stay ``unchecked``; a symlink component stops the check. Other POSIX systems have no
#   such table and stay ``unchecked``.
# - At most ``INPUT_STATUS_MAX_WORKERS`` check threads exist in the whole process. A request that
#   finds none free answers ``unchecked`` at once; a request past its deadline stops starting new
#   system calls, so a hung call keeps at most one thread until it returns.

INPUT_STATUS_MAX_WORKERS = 2
_WORKER_SLOTS = threading.BoundedSemaphore(INPUT_STATUS_MAX_WORKERS)
_IS_WINDOWS = os.name == "nt"
_LOCAL_DRIVE_TYPES = frozenset({2, 3, 6})  # DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_RAMDISK
_NETWORK_FS = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smb3",
        "smbfs",
        "ncpfs",
        "afs",
        "ceph",
        "glusterfs",
        "9p",
        "lustre",
        "gpfs",
        "davfs",
        "coda",
        "sshfs",
        "virtiofs",
        "vboxsf",
        "prl_fs",
        "autofs",
    }
)
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


def _drive_type(root: str) -> int:
    """``GetDriveTypeW`` of a ``X:\\`` root (local query; never opens the drive)."""
    import ctypes

    windll: Any = getattr(ctypes, "windll", None)
    if windll is None:
        return 0
    return int(windll.kernel32.GetDriveTypeW(root))


def _mounts() -> list[tuple[str, str]]:
    """``(mount point, fs type)`` of this process; empty when the table cannot be read."""
    try:
        with open("/proc/self/mounts", encoding="utf-8", errors="replace") as table:
            lines = table.read().splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        parts = line.split()
        if len(parts) >= 3:
            # The kernel escapes space, tab, newline and backslash as octal (``\\040``).
            point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), parts[1])
            result.append((point, parts[2]))
    return result


def _local_mount(path: str, mounts: list[tuple[str, str]]) -> bool:
    best = None
    for point, fstype in mounts:
        prefix = point.rstrip("/") + "/"
        if (path == point or path.startswith(prefix) or point == "/") and (
            best is None or len(point) > len(best[0])
        ):
            best = (point, fstype)
    if best is None:
        return False
    fstype = best[1]
    return not (fstype in _NETWORK_FS or fstype.startswith("fuse") or fstype.startswith("nfs"))


def _paths() -> Any:
    """Path rules of the platform being checked (``ntpath`` on Windows)."""
    return ntpath if _IS_WINDOWS else posixpath


def _components(path: str) -> list[str]:
    """Absolute prefixes from the first element below the root to ``path`` itself."""
    rules = _paths()
    drive, rest = rules.splitdrive(path)
    sep = "\\" if _IS_WINDOWS else "/"
    parts = [p for p in rest.replace("/", sep).split(sep) if p]
    prefixes = []
    current = drive + sep
    for part in parts:
        current = rules.join(current, part)
        prefixes.append(current)
    return prefixes


def _is_reparse(info: os.stat_result) -> bool:
    """A symlink, or on Windows any reparse point (junction, mount point, cloud placeholder)."""
    attributes = getattr(info, "st_file_attributes", 0) or 0
    return stat.S_ISLNK(info.st_mode) or bool(attributes & _REPARSE_POINT)


def _check_one(path: str, cancelled: threading.Event, mounts: list[tuple[str, str]]) -> str:
    rules = _paths()
    if "\x00" in path or path.startswith(("\\\\", "//")) or not rules.isabs(path):
        return "unchecked"
    if _IS_WINDOWS:
        drive = rules.splitdrive(path)[0]
        if (
            len(drive) != 2
            or drive[1] != ":"
            or _drive_type(drive + "\\") not in _LOCAL_DRIVE_TYPES
        ):
            return "unchecked"
    else:
        normalized = os.path.normpath(path)
        if normalized != path.rstrip("/") or not _local_mount(normalized, mounts):
            return "unchecked"
    components = _components(path)
    if not components:
        return "not_file"
    info: os.stat_result | None = None
    for component in components:
        if cancelled.is_set():
            return "unchecked"
        try:
            info = os.lstat(component)
        except (FileNotFoundError, NotADirectoryError):
            return "missing"
        except (OSError, ValueError):
            return "unchecked"
        if _is_reparse(info):
            # A link may lead anywhere (also onto a network share): not followed.
            return "unchecked"
    assert info is not None
    return "file" if stat.S_ISREG(info.st_mode) else "not_file"


def input_status(paths: list[str], deadline: float = INPUT_STATUS_DEADLINE_SECONDS) -> list[str]:
    """Bounded, read-only existence check of local inputs; anything unproven is ``unchecked``."""
    results = ["unchecked"] * len(paths)
    if not paths or not _WORKER_SLOTS.acquire(blocking=False):
        return results
    cancelled = threading.Event()
    finished = threading.Event()
    lock = threading.Lock()
    mounts = [] if _IS_WINDOWS else _mounts()

    def work() -> None:
        try:
            for index, path in enumerate(paths):
                if cancelled.is_set():
                    return
                try:
                    state = _check_one(path, cancelled, mounts)
                except Exception:  # any surprise stays unproven (never a crash or a guess)
                    state = "unchecked"
                with lock:
                    if not cancelled.is_set():
                        results[index] = state
        finally:
            finished.set()
            _WORKER_SLOTS.release()

    threading.Thread(target=work, name="workset-input-status", daemon=True).start()
    finished.wait(deadline)
    with lock:
        cancelled.set()
        return list(results)


def register_workset_routes(app: FastAPI, store_path: Path) -> None:
    lock = threading.Lock()
    holder: dict[str, WorksetStore] = {}

    def store() -> WorksetStore:
        with lock:
            if "store" not in holder:
                holder["store"] = WorksetStore(store_path)
            return holder["store"]

    async def run(work: Callable[[], Any]) -> Any:
        return await run_in_threadpool(work)

    @app.get("/worksets")
    async def list_worksets() -> Response:
        return JSONResponse(await run(lambda: store().list()))

    @app.post("/worksets/bootstrap")
    async def bootstrap(request: Request) -> Response:
        body = await _json(request, 64 * 1024)
        _only(body, "name")
        return JSONResponse(await run(lambda: store().bootstrap(body.get("name"))))

    @app.post("/worksets")
    async def create(request: Request) -> Response:
        key = _key(request)
        body = await _json(request)
        _only(body, "name", "state")
        status, row = await run(lambda: store().create(key, body.get("name"), body.get("state")))
        return _resource(row, status, {"Location": "/api/v1/worksets/" + row.id})

    @app.get("/worksets/active")
    async def get_active() -> Response:
        listing = await run(lambda: store().list())
        if listing["active"] is None:
            raise WorksetError("workset_not_found", 404, retryable=False)
        active = listing["active"]
        return JSONResponse(
            {"schema_version": 1, **active}, headers={"ETag": f'"{active["revision"]}"'}
        )

    @app.put("/worksets/active")
    async def put_active(request: Request) -> Response:
        body = await _json(request, 64 * 1024)
        _only(body, "workset_id", "view")
        view = body.get("view")
        if (
            not isinstance(view, dict)
            or set(view) != {"mode"}
            or not isinstance(body.get("workset_id"), str)
        ):
            raise WorksetError("contract_validation_failed", 422, retryable=False)
        workset_id = _workset_id(body["workset_id"])
        expected = _expected(request, allow_zero=True)
        result = await run(lambda: store().set_active(workset_id, view["mode"], expected))
        return JSONResponse(result, headers={"ETag": f'"{result["revision"]}"'})

    @app.post("/worksets/input-status")
    async def check_inputs(request: Request) -> Response:
        body = await _json(request, MAX_INPUTS * (MAX_PATH * 4 + 16) + 1024)
        _only(body, "paths")
        paths = body.get("paths")
        if (
            not isinstance(paths, list)
            or len(paths) > MAX_INPUTS
            or not all(isinstance(p, str) and 0 < len(p) <= MAX_PATH for p in paths)
        ):
            raise WorksetError("contract_validation_failed", 422, retryable=False)
        states = await run(lambda: input_status(paths))
        # Same order as the request; the response never repeats a path.
        return JSONResponse({"schema_version": 1, "items": [{"state": s} for s in states]})

    @app.get("/worksets/{workset_id}")
    async def get_one(workset_id: str) -> Response:
        selected = _workset_id(workset_id)
        return _resource(await run(lambda: store().get(selected)))

    @app.put("/worksets/{workset_id}")
    async def put_one(workset_id: str, request: Request) -> Response:
        selected = _workset_id(workset_id)
        expected = _expected(request)
        body = await _json(request)
        _only(body, "name", "state")
        if "state" not in body or "name" not in body:
            raise WorksetError("contract_validation_failed", 422, retryable=False)
        row = await run(lambda: store().update(selected, expected, body["name"], body["state"]))
        return _resource(row)

    @app.delete("/worksets/{workset_id}")
    async def delete_one(workset_id: str, request: Request) -> Response:
        selected = _workset_id(workset_id)
        expected = _expected(request)
        return JSONResponse(await run(lambda: store().delete(selected, expected)))

    @app.get("/worksets/{workset_id}/recovery")
    async def list_recovery(workset_id: str) -> Response:
        selected = _workset_id(workset_id)
        return JSONResponse(await run(lambda: store().recovery(selected)))

    @app.post("/worksets/{workset_id}/recovery/{revision}/restore")
    async def restore(workset_id: str, revision: str, request: Request) -> Response:
        selected = _workset_id(workset_id)
        expected = _expected(request)
        if not re.fullmatch(r"[1-9][0-9]{0,17}", revision):
            raise WorksetError("recovery_not_found", 404, retryable=False)
        number = int(revision)
        return _resource(await run(lambda: store().restore_recovery(selected, number, expected)))
