"""Opt-in B9 routes: SSE ``/events``, ``/sync`` and sanitized diagnostics.

Registered on the *same* v1 sub-application (one ``/api/v1`` mount, the B5 session / Host /
Origin middleware). Only committed outbox rows are streamed, projected through the B6
``EventEnvelope`` allowlist; no worker output or private payload reaches a client. Each
poll is its own short read transaction; no write transaction is held while streaming.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response, StreamingResponse

from utteran_contracts.control import EventEnvelope, EventPage
from utteran_contracts.events import ResyncRequired
from utteran_control.errors import ControlError, ErrorCode
from utteran_control.events_repository import SyncService, diagnostics, watermark
from utteran_control.job_repository import JobControlStore
from utteran_gui.job_admission import ResolutionTokens
from utteran_gui.job_runtime import JobRuntime, RuntimeStateError

T = TypeVar("T")
_RESOURCE = re.compile(r"(job|setup|refresh)_[0-9a-f]{32}|queue")
_JOB_ID = re.compile(r"job_[0-9a-f]{32}")
_SETUP_ID = re.compile(r"setup_[0-9a-f]{32}")


@dataclass(frozen=True)
class EventStreamConfig:
    """Explicit bounds: poll interval, heartbeat, stream lifetime, subscribers, page size."""

    poll_interval: float = 0.5
    heartbeat_seconds: float = 15.0
    max_stream_seconds: float = 3600.0
    max_subscribers: int = 8
    page_size: int = 100


class EventApiError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def _error(code: str, status: int) -> JSONResponse:
    return JSONResponse(
        {
            "schema_version": 1,
            "error": {
                "code": code,
                "message_key": f"events.{code}",
                "request_id": uuid4().hex,
                "retryable": status == 503,
                "issues": [],
            },
        },
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _session(request: Request) -> str:
    return request.headers.get("x-utteran-session") or request.cookies.get(
        "utteran_gui_session", ""
    )


def frame(event: EventEnvelope) -> str:
    """SSE framing: ``id: <epoch>:<seq>``, ``event: <type>``, one-line JSON, blank line."""
    data = json.dumps(event.to_wire(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n"


def resync_frame(reason: str) -> str:
    message = ResyncRequired.model_validate(
        {"schema_version": 1, "type": "resync_required", "reason_code": reason}
    )
    # No ``id:`` line: a control message never advances Last-Event-ID or invents a seq.
    return f"event: resync_required\ndata: {json.dumps(message.to_wire())}\n\n"


def register_event_routes(
    app: FastAPI, runtime: JobRuntime, config: EventStreamConfig | None = None
) -> None:
    cfg = config or EventStreamConfig()
    subscribers = {"count": 0}
    lock = threading.Lock()
    services: dict[int, SyncService] = {}

    @app.exception_handler(EventApiError)
    async def event_failure(request: Request, exc: EventApiError) -> JSONResponse:
        return _error(exc.code, exc.status)

    def store() -> JobControlStore:
        if runtime.state != "active" or runtime.store is None:
            raise RuntimeStateError("events_runtime_inactive")
        return runtime.store

    async def call(work: Callable[[], T]) -> T:
        try:
            return await run_in_threadpool(work)
        except ControlError as exc:
            raise EventApiError(exc.code.value, exc.status) from None
        except RuntimeStateError as exc:
            raise EventApiError(exc.code, 503) from None

    @app.get("/events")
    async def events(request: Request) -> Response:
        after = request.query_params.get("after")
        last = request.headers.get("last-event-id")
        if after is not None and last is not None and after != last:
            raise EventApiError("cursor_conflict", 400)
        resource_id = request.query_params.get("resource_id")
        if resource_id is not None and not _RESOURCE.fullmatch(resource_id):
            raise EventApiError("request_invalid", 400)
        requested = after if after is not None else last

        def first_page() -> tuple[str, EventPage]:
            repo = store()
            start = requested if requested is not None else watermark(repo)
            # Validated before the response starts: invalid 400, expired/other epoch 410.
            page = repo.read_events(start, resource_id=resource_id, limit=cfg.page_size)
            return start, page

        _, page = await call(first_page)
        with lock:
            if subscribers["count"] >= cfg.max_subscribers:
                raise EventApiError("event_stream_limit", 503)
            subscribers["count"] += 1

        async def stream() -> AsyncIterator[str]:
            current = page
            started = beat = time.monotonic()
            try:
                yield "retry: 3000\n\n"
                while True:
                    for event in current.events:
                        yield frame(event)
                    cursor = current.next_cursor
                    if not current.has_more:
                        if await request.is_disconnected():
                            return
                        now = time.monotonic()
                        if now - started >= cfg.max_stream_seconds:
                            return  # client reconnects with Last-Event-ID; nothing is restarted
                        if now - beat >= cfg.heartbeat_seconds:
                            beat = now
                            yield ": keepalive\n\n"
                        await asyncio.sleep(cfg.poll_interval)
                    try:
                        current = await run_in_threadpool(
                            lambda at=cursor: store().read_events(  # type: ignore[misc]
                                at, resource_id=resource_id, limit=cfg.page_size
                            )
                        )
                    except ControlError as exc:
                        if exc.code is ErrorCode.EVENT_CURSOR_EXPIRED:
                            yield resync_frame("event_cursor_expired")
                        return
                    except RuntimeStateError:
                        return
            finally:
                with lock:
                    subscribers["count"] -= 1

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    def sync_service() -> SyncService:
        repo = store()
        with lock:
            service = services.get(id(repo))
            if service is None or service.store is not repo:
                services.clear()
                service = services[id(repo)] = SyncService(repo)
            return service

    @app.get("/sync")
    async def sync(request: Request) -> Response:
        cursor = request.query_params.get("cursor")
        token = request.query_params.get("snapshot_token")
        raw_limit = request.query_params.get("limit", "500")
        if not re.fullmatch(r"[1-9][0-9]{0,2}", raw_limit):
            raise EventApiError("request_invalid", 422)
        viewer = ResolutionTokens.binding(_session(request))
        snapshot = await call(
            lambda: sync_service().page(
                cursor, snapshot_token=token, limit=int(raw_limit), scope=viewer
            )
        )
        return JSONResponse(snapshot.to_wire(), headers={"ETag": f'"{snapshot.queue.revision}"'})

    def diagnostic_route(path: str, pattern: re.Pattern[str], kind: str) -> None:
        async def handler(resource_id: str, request: Request) -> Response:
            if not pattern.fullmatch(resource_id):
                raise EventApiError("resource_not_found", 404)
            cursor = request.query_params.get("cursor")
            result: Any = await call(lambda: diagnostics(store(), resource_id, kind, cursor))
            return JSONResponse(result.to_wire(), headers={"ETag": f'"{result.revision}"'})

        app.add_api_route(path, handler, methods=["GET"])

    diagnostic_route("/jobs/{resource_id}/diagnostics", _JOB_ID, "job")
    diagnostic_route("/setup-operations/{resource_id}/diagnostics", _SETUP_ID, "setup_operation")
