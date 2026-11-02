"""Opt-in B8 setup routes on the *same* v1 sub-application as B5/B7 (one ``/api/v1`` mount).

Composition only: plan building is pure, observation is read-only, and every state change
is one control-store transaction. Errors are fixed codes; no path, argv, token or child
output is returned.
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

from utteran_contracts.setup import (
    ApprovalRequest,
    SetupCancelRequest,
    SetupPlanRequest,
    SetupRetryRequest,
    SetupStart,
)
from utteran_control.errors import ControlError
from utteran_control.setup_repository import SetupControlStore
from utteran_control.store import IdempotencyScope
from utteran_gui.job_admission import ResolutionTokens
from utteran_gui.job_runtime import JobRuntime, RuntimeStateError
from utteran_gui.settings_api import _body, _key, _match
from utteran_gui.setup_planner import PlanRejected, SetupObservation, build_plan
from utteran_settings.errors import SettingsError
from utteran_settings.inheritance import safe_contract_issues

T = TypeVar("T")
_PLAN_ID = re.compile(r"plan_[0-9a-f]{32}")
_SETUP_ID = re.compile(r"setup_[0-9a-f]{32}")


class SetupApiError(Exception):
    def __init__(self, code: str, status: int, issues: tuple[Any, ...] = ()) -> None:
        self.code, self.status, self.issues = code, status, issues
        super().__init__(code)


def _error(code: str, status: int, issues: tuple[Any, ...] = ()) -> JSONResponse:
    return JSONResponse(
        {
            "schema_version": 1,
            "error": {
                "code": code,
                "message_key": f"setup.{code}",
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


def register_setup_routes(app: FastAPI, runtime: JobRuntime) -> None:
    """Add B8 routes. Requires a runtime constructed with ``setup=SetupComponents(...)``."""
    if runtime.setup is None:
        raise ValueError("v1 setup requires a job runtime with setup components")
    observer = runtime.setup.observer

    @app.exception_handler(SetupApiError)
    async def setup_failure(request: Request, exc: SetupApiError) -> JSONResponse:
        return _error(exc.code, exc.status, exc.issues)

    async def call(work: Callable[[], T]) -> T:
        try:
            return await run_in_threadpool(work)
        except PlanRejected as exc:
            raise SetupApiError(exc.code, exc.status) from None
        except ControlError as exc:
            raise SetupApiError(exc.code.value, exc.status) from None
        except RuntimeStateError as exc:
            raise SetupApiError(exc.code, 503) from None

    async def guarded(handler: Callable[[], Awaitable[Response]]) -> Response:
        try:
            return await handler()
        except SettingsError as exc:
            raise SetupApiError(exc.code, exc.status, exc.issues) from None
        except ValidationError as exc:
            raise SetupApiError(
                "contract_validation_failed", 422, safe_contract_issues(exc)
            ) from None

    def store() -> SetupControlStore:
        if (
            runtime.state != "active"
            or not isinstance(runtime.store, SetupControlStore)
            or runtime.setup_executor is None
        ):
            raise RuntimeStateError("setup_runtime_inactive")
        return runtime.store

    def observe() -> SetupObservation:
        result: SetupObservation = observer.observe()
        return result

    def wake() -> None:
        if runtime.dispatcher is not None:
            runtime.dispatcher.wake()

    def json(status: int, body: dict[str, Any], headers: Any = None) -> JSONResponse:
        return JSONResponse(body, status_code=status, headers=dict(headers or {}))

    def plan_id_of(value: str) -> str:
        if not _PLAN_ID.fullmatch(value):
            raise SetupApiError("resource_not_found", 404)
        return value

    def setup_id_of(value: str) -> str:
        if not _SETUP_ID.fullmatch(value):
            raise SetupApiError("resource_not_found", 404)
        return value

    @app.post("/setup-plans")
    async def create_plan(request: Request) -> Response:
        async def handler() -> Response:
            key = _key(request)
            body = await _body(request)
            scope = IdempotencyScope("/api/v1/setup-plans", key)

            def work() -> Any:
                repo = store()
                digest = repo.setup_digest(body)
                replay = repo.lookup_setup(scope, digest, "plan")  # before any observation
                if replay is not None:
                    return replay
                plan_request = SetupPlanRequest.model_validate(body)
                content = build_plan(plan_request, observe())  # read-only; no side effects
                return repo.create_plan(scope, digest, content)

            accepted = await call(work)
            return json(accepted.status, accepted.body, accepted.headers)

        return await guarded(handler)

    @app.get("/setup-plans/{plan_id}")
    async def get_plan(plan_id: str) -> Response:
        identifier = plan_id_of(plan_id)
        plan = await call(lambda: store().get_plan(identifier))
        return json(200, plan.to_wire(), {"ETag": f'"{plan.revision}"'})

    @app.delete("/setup-plans/{plan_id}")
    async def discard_plan(plan_id: str, request: Request) -> Response:
        identifier = plan_id_of(plan_id)

        async def handler() -> Response:
            expected = _match(request)
            await call(lambda: store().discard_plan(identifier, expected))
            return Response(status_code=204)

        return await guarded(handler)

    @app.post("/setup-plans/{plan_id}/approve")
    async def approve(plan_id: str, request: Request) -> Response:
        identifier = plan_id_of(plan_id)

        async def handler() -> Response:
            key = _key(request)
            expected = _match(request)
            approval = ApprovalRequest.model_validate(await _body(request))
            scope = IdempotencyScope(f"/api/v1/setup-plans/{identifier}/approve", key)

            def work() -> Any:
                repo = store()
                observation = observe()
                return repo.approve_plan(
                    identifier,
                    expected,
                    scope,
                    approval,
                    fingerprint=observation.fingerprint,
                    credentials=observation.credentials,
                )

            accepted = await call(work)
            return json(accepted.status, accepted.body, accepted.headers)

        return await guarded(handler)

    @app.post("/setup-operations")
    async def start(request: Request) -> Response:
        async def handler() -> Response:
            key = _key(request)
            body = await _body(request)
            scope = IdempotencyScope("/api/v1/setup-operations", key)

            def work() -> Any:
                repo = store()
                digest = repo.setup_digest(body)
                replay = repo.lookup_setup(scope, digest, "operation")
                if replay is not None:
                    return replay
                payload = SetupStart.model_validate(body)
                return repo.start_operation(
                    scope, digest, payload, fingerprint=observe().fingerprint
                )

            accepted = await call(work)
            wake()
            return json(accepted.status, accepted.body, accepted.headers)

        return await guarded(handler)

    @app.get("/setup-operations")
    async def list_setups(request: Request) -> Response:
        cursor = request.query_params.get("cursor")
        viewer = ResolutionTokens.binding(_session(request))
        result = await call(lambda: store().list_setups(cursor, scope=viewer))
        return json(200, result.to_wire(), {"ETag": f'"{result.revision}"'})

    @app.get("/setup-operations/{operation_id}")
    async def get_setup(operation_id: str) -> Response:
        identifier = setup_id_of(operation_id)
        view = await call(lambda: store().get_setup(identifier))
        return json(200, view.to_wire(), {"ETag": f'"{view.revision}"'})

    @app.post("/setup-operations/{operation_id}/{action}")
    async def setup_action(operation_id: str, action: str, request: Request) -> Response:
        if action not in {"retry", "resume", "cancel"}:
            raise SetupApiError("resource_not_found", 404)
        identifier = setup_id_of(operation_id)

        async def handler() -> Response:
            key = _key(request)
            expected = _match(request)
            body = await _body(request)
            scope = IdempotencyScope(f"/api/v1/setup-operations/{identifier}/{action}", key)
            if action == "cancel":
                if SetupCancelRequest.model_validate(body).mode != "cooperative":
                    # Killing uv / a compiler / a download mid-way is not proven safe.
                    raise SetupApiError("cancel_mode_unsupported", 409)
                accepted = await call(lambda: store().cancel_setup(identifier, expected, scope))
                executor = runtime.setup_executor
                if executor is not None:
                    executor.notify_cancel(identifier)
                return json(accepted.status, accepted.body, accepted.headers)
            SetupRetryRequest.model_validate(body)

            def work() -> Any:
                repo = store()
                observation = observe()
                method = repo.retry_setup if action == "retry" else repo.resume_setup
                return method(
                    identifier,
                    expected,
                    scope,
                    fingerprint=observation.fingerprint,
                    credentials=observation.credentials,
                )

            accepted = await call(work)
            wake()
            return json(accepted.status, accepted.body, accepted.headers)

        return await guarded(handler)
