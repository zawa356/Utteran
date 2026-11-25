"""E-C routes on the existing v1 sub-app (same session/Host/Origin boundary as B5/B7/E-B).

Registered only with the v1 job runtime (``--next-ui-v1``); the legacy GUI, the legacy
``--next-ui`` and the CLI never import this module.

- ``GET  /jobs/{id}/reexport-source``  availability (fixed refusal code), frozen defaults,
  displayed speaker labels and the verified source digest. Reads only; creates nothing.
- ``POST /jobs/{id}/reexports``  ``If-Match: "<job revision>"`` + ``Idempotency-Key``.
  201 new record; 200 the record already made for this key (same body); 409 for another body.
- ``GET  /jobs/{id}/reexports``  the records of one job, newest first.
- ``GET  /reexports/{rex_id}``  one record (reconciliation after an unknown outcome).

Private values (labels, the output directory) appear only in authenticated response bodies;
errors are ``{schema_version, error:{code, message_key:"reexport.<code>", ...}}`` without any
value, and nothing here logs.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from utteran_gui.reexport_service import ReexportService
from utteran_gui.reexport_store import ReexportError
from utteran_settings.errors import SettingsError

T = TypeVar("T")
#: 256 labels x 256 characters (4 bytes each at most) plus the envelope.
MAX_REEXPORT_BODY = 512 * 1024
_JOB_ID = re.compile(r"job_[0-9a-f]{32}")
_REEXPORT_ID = re.compile(r"rex_[0-9a-f]{32}")
_IF_MATCH = re.compile(r'"([1-9][0-9]{0,17})"')
_NO_STORE = {"Cache-Control": "no-store"}


def _error(exc: ReexportError) -> JSONResponse:
    from uuid import uuid4

    return JSONResponse(
        {
            "schema_version": 1,
            "error": {
                "code": exc.code,
                "message_key": f"reexport.{exc.code}",
                "request_id": uuid4().hex,
                "retryable": exc.retryable,
                "issues": [],
            },
        },
        status_code=exc.status,
        headers=_NO_STORE,
    )


def register_reexport_routes(app: FastAPI, service_factory: Callable[[], ReexportService]) -> None:
    def job_id(value: str) -> str:
        if not _JOB_ID.fullmatch(value):
            raise ReexportError("resource_not_found", 404, retryable=False)
        return value

    async def run(work: Callable[[], T]) -> T:
        return await run_in_threadpool(work)

    async def guard(handler: Callable[[], Awaitable[Response]]) -> Response:
        try:
            return await handler()
        except ReexportError as exc:
            return _error(exc)
        except SettingsError as exc:  # body parser: malformed_json, payload_too_large, ...
            return _error(ReexportError(exc.code, exc.status, retryable=False))

    @app.get("/jobs/{identifier}/reexport-source")
    async def source(identifier: str) -> Response:
        async def handler() -> Response:
            checked = job_id(identifier)
            body = await run(lambda: service_factory().plan(checked))
            return JSONResponse(body, headers=_NO_STORE)

        return await guard(handler)

    @app.post("/jobs/{identifier}/reexports")
    async def create(identifier: str, request: Request) -> Response:
        async def handler() -> Response:
            from utteran_gui.settings_api import read_json_body

            checked = job_id(identifier)
            match = _IF_MATCH.fullmatch(request.headers.get("if-match", ""))
            if request.headers.get("if-match") is None:
                raise ReexportError("if_match_required", 428, retryable=False)
            if match is None:
                raise ReexportError("if_match_invalid", 400, retryable=False)
            key = request.headers.get("idempotency-key", "")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{16,128}", key):
                raise ReexportError("idempotency_key_invalid", 400, retryable=False)
            body = await read_json_body(request, MAX_REEXPORT_BODY)
            status, result = await run(
                lambda: service_factory().execute(checked, int(match.group(1)), key, body)
            )
            return JSONResponse(result, status_code=status, headers=_NO_STORE)

        return await guard(handler)

    @app.get("/jobs/{identifier}/reexports")
    async def listing(identifier: str) -> Response:
        async def handler() -> Response:
            checked = job_id(identifier)
            body = await run(lambda: service_factory().list_for_job(checked))
            return JSONResponse(body, headers=_NO_STORE)

        return await guard(handler)

    @app.get("/reexports/{identifier}")
    async def one(identifier: str) -> Response:
        async def handler() -> Response:
            if not _REEXPORT_ID.fullmatch(identifier):
                raise ReexportError("reexport_not_found", 404, retryable=False)
            body = await run(lambda: service_factory().get(identifier))
            return JSONResponse(body, headers=_NO_STORE)

        return await guard(handler)
