"""E-B ``/api/v1/worksets`` on the existing v1 sub-app (same session/Host/Origin boundary).

Mounted only by ``create_app(enable_v1_settings=True)`` (``--next-ui-v1``); the legacy GUI, the
legacy ``--next-ui`` and the CLI never import this module. Responses carry private values only
in authenticated bodies; errors carry fixed codes only (no path, name, term or state value),
and nothing here logs a request or response body.
"""

from __future__ import annotations

import os
import re
import stat
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
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


def _expected(request: Request) -> int:
    value = request.headers.get("if-match")
    if value is None:
        raise WorksetError("if_match_required", 428, retryable=False)
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


def _stat_one(path: str) -> str:
    # Network/device names are never touched from here (no SMB/credential side effects).
    if path.startswith(("\\\\", "//")) or "\x00" in path:
        return "unchecked"
    if not os.path.isabs(path):
        return "unchecked"
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return "missing"
    except NotADirectoryError:
        return "missing"
    except (OSError, ValueError):
        return "unchecked"
    return "file" if stat.S_ISREG(info.st_mode) else "not_file"


def input_status(paths: list[str], deadline: float = INPUT_STATUS_DEADLINE_SECONDS) -> list[str]:
    """Bounded, read-only existence check of local inputs; unknown stays ``unchecked``."""
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="workset-input-status")
    try:
        futures = [pool.submit(_stat_one, path) for path in paths]
        wait(futures, timeout=deadline)
        return [f.result() if f.done() and not f.exception() else "unchecked" for f in futures]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


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
        return JSONResponse({"schema_version": 1, **listing["active"]})

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
        return JSONResponse(await run(lambda: store().set_active(workset_id, view["mode"])))

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
