"""Opt-in v1 job/queue routes, registered on the *same* v1 sub-application as B5.

There is one ``/api/v1`` mount, one session/Host/Origin middleware and one body parser.
This module is a thin composition: validation, CAS and idempotency live in admission and
the control store. Errors are fixed codes; no path, prompt or exception text is returned.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import uuid4

from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from utteran_contracts.jobs import CancelRequest, QueueControl, QueueItemPatch, QueueOrder
from utteran_control.errors import ControlError
from utteran_control.store import IdempotencyScope
from utteran_gui.job_admission import AdmissionError, ResolutionTokens
from utteran_gui.job_runtime import JobRuntime, RuntimeStateError
from utteran_gui.settings_api import _body, _key, _match
from utteran_settings.errors import SettingsError
from utteran_settings.inheritance import safe_contract_issues

T = TypeVar("T")
_JOB_ID = re.compile(r"job_[0-9a-f]{32}")
_ITEM_ID = re.compile(r"qi_[0-9a-f]{32}")


class JobApiError(Exception):
    def __init__(self, code: str, status: int, issues: tuple[Any, ...] = ()) -> None:
        self.code, self.status, self.issues = code, status, issues
        super().__init__(code)


def _error(code: str, status: int, issues: tuple[Any, ...] = ()) -> JSONResponse:
    return JSONResponse(
        {
            "schema_version": 1,
            "error": {
                "code": code,
                "message_key": f"jobs.{code}",
                "request_id": uuid4().hex,
                "retryable": status == 503,
                "issues": [item.to_wire() for item in issues],
            },
        },
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _session(request: Request) -> str:
    return request.headers.get("x-utteran-session") or request.cookies.get(
        "utteran_gui_session", ""
    )


def register_job_routes(app: FastAPI, runtime: JobRuntime) -> None:
    """Add B7 routes to the existing v1 sub-app (B9 events/sync stay unregistered)."""

    @app.exception_handler(JobApiError)
    async def job_failure(request: Request, exc: JobApiError) -> JSONResponse:
        return _error(exc.code, exc.status, exc.issues)

    async def call(work: Callable[[], T]) -> T:
        try:
            return await run_in_threadpool(work)
        except AdmissionError as exc:
            raise JobApiError(exc.code, exc.status, exc.issues) from None
        except ControlError as exc:
            raise JobApiError(exc.code.value, exc.status) from None
        except RuntimeStateError as exc:
            raise JobApiError(exc.code, 503) from None

    async def guarded(request: Request, handler: Callable[[], Awaitable[Response]]) -> Response:
        try:
            return await handler()
        except SettingsError as exc:
            raise JobApiError(exc.code, exc.status, exc.issues) from None
        except ValidationError as exc:
            raise JobApiError(
                "contract_validation_failed", 422, safe_contract_issues(exc)
            ) from None

    def parts() -> tuple[Any, Any, Any]:
        if runtime.state != "active" or runtime.store is None or runtime.admission is None:
            raise RuntimeStateError("jobs_runtime_inactive")
        return runtime.store, runtime.admission, runtime.dispatcher

    def job_id_of(value: str) -> str:
        if not _JOB_ID.fullmatch(value):
            raise JobApiError("resource_not_found", 404)
        return value

    def json(status: int, body: dict[str, Any], headers: Any = None) -> JSONResponse:
        return JSONResponse(body, status_code=status, headers=dict(headers or {}))

    @app.post("/execution-resolutions")
    async def resolve(request: Request) -> Response:
        async def handler() -> Response:
            key = _key(request)
            body = await _body(request)
            status, resolution, _ = await call(
                lambda: parts()[1].resolve(key, body, session=_session(request))
            )
            return json(status, resolution.to_wire())

        return await guarded(request, handler)

    @app.get("/jobs")
    async def list_jobs(request: Request) -> Response:
        cursor = request.query_params.get("cursor")
        viewer = ResolutionTokens.binding(_session(request))  # cursor bound to session
        result = await call(lambda: parts()[0].list_jobs(cursor, scope=viewer))
        return json(200, result.to_wire(), {"ETag": f'"{result.revision}"'})

    @app.post("/jobs")
    async def submit(request: Request) -> Response:
        async def handler() -> Response:
            key = _key(request)
            body = await _body(request)
            scope = IdempotencyScope("/api/v1/jobs", key)
            accepted = await call(lambda: parts()[1].submit(scope, body, session=_session(request)))
            parts()[2].wake()
            return json(accepted.status, accepted.batch.to_wire(), accepted.headers)

        return await guarded(request, handler)

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> Response:
        identifier = job_id_of(job_id)
        job = await call(lambda: parts()[0].get_job(identifier))
        return json(200, job.to_wire(), {"ETag": f'"{job.revision}"'})

    @app.post("/jobs/{job_id}/{action}")
    async def job_action(job_id: str, action: str, request: Request) -> Response:
        if action not in {"cancel", "retry", "rerun", "revalidate"}:
            raise JobApiError("resource_not_found", 404)
        identifier = job_id_of(job_id)

        async def handler() -> Response:
            key = _key(request)
            expected = _match(request)
            body = await _body(request)
            scope = IdempotencyScope(f"/api/v1/jobs/{identifier}/{action}", key)
            if action == "cancel":
                mode = CancelRequest.model_validate(body).mode
                if mode != "cooperative":
                    # Forced termination is not verified for the worker backend in B7.
                    raise JobApiError("cancel_mode_unsupported", 409)
                accepted = await call(lambda: parts()[0].cancel_job(identifier, expected, scope))
                parts()[2].notify_cancel(identifier)
                return json(accepted.status, accepted.job.to_wire(), accepted.headers)
            if action == "rerun":
                batch = await call(
                    lambda: parts()[1].rerun(
                        identifier, expected, scope, body, session=_session(request)
                    )
                )
                parts()[2].wake()
                return json(batch.status, batch.batch.to_wire(), batch.headers)
            if not isinstance(body, dict) or body != {"schema_version": 1}:
                raise JobApiError("contract_validation_failed", 422)
            method = parts()[1].retry if action == "retry" else parts()[1].revalidate
            result = await call(lambda: method(identifier, expected, scope))
            parts()[2].wake()
            return json(result.status, result.job.to_wire(), result.headers)

        return await guarded(request, handler)

    @app.get("/queue")
    async def get_queue() -> Response:
        view = await call(lambda: parts()[0].queue())
        return json(200, view.to_wire(), {"ETag": f'"{view.revision}"'})

    @app.put("/queue/order")
    async def reorder(request: Request) -> Response:
        async def handler() -> Response:
            key = _key(request)
            expected = _match(request)
            order = QueueOrder.model_validate(await _body(request))
            scope = IdempotencyScope("/api/v1/queue/order", key)
            accepted = await call(
                lambda: parts()[0].reorder(expected, order.waiting_item_ids, scope)
            )
            parts()[2].wake()
            return json(accepted.status, accepted.queue.to_wire(), accepted.headers)

        return await guarded(request, handler)

    @app.patch("/queue/control")
    async def control(request: Request) -> Response:
        async def handler() -> Response:
            key = _key(request)
            expected = _match(request)
            dispatch = QueueControl.model_validate(await _body(request)).dispatch
            scope = IdempotencyScope("/api/v1/queue/control", key)
            accepted = await call(
                lambda: parts()[0].set_dispatch(expected, dispatch, idempotency=scope)
            )
            parts()[2].wake()
            return json(accepted.status, accepted.queue.to_wire(), accepted.headers)

        return await guarded(request, handler)

    @app.patch("/queue/items/{item_id}")
    async def hold(item_id: str, request: Request) -> Response:
        if not _ITEM_ID.fullmatch(item_id):
            raise JobApiError("resource_not_found", 404)

        async def handler() -> Response:
            key = _key(request)
            expected = _match(request)
            held = QueueItemPatch.model_validate(await _body(request)).held
            scope = IdempotencyScope(f"/api/v1/queue/items/{item_id}", key)
            accepted = await call(lambda: parts()[0].set_item_hold(item_id, expected, held, scope))
            parts()[2].wake()
            return json(accepted.status, accepted.queue.to_wire(), accepted.headers)

        return await guarded(request, handler)
