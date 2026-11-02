"""Explicit opt-in private v1 settings application; no core imports."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import secrets
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from utteran_contracts.editing import EditingSession
from utteran_contracts.settings import Policy, PolicyWrite, Preset, PresetWrite
from utteran_settings.errors import SettingsError
from utteran_settings.inheritance import issue, preview, safe_contract_issues, semantic_issues
from utteran_settings.repository import Kind, Repository

MAX_BODY = 8 * 1024 * 1024


def error_response(error: SettingsError) -> JSONResponse:
    return JSONResponse(
        {
            "schema_version": 1,
            "error": {
                "code": error.code,
                "message_key": f"settings.{error.code}",
                "request_id": uuid4().hex,
                "retryable": error.status == 503,
                "issues": [item.to_wire() for item in error.issues],
            },
        },
        status_code=error.status,
        headers={"Cache-Control": "no-store"},
    )


def _match(request: Request) -> int:
    value = request.headers.get("if-match")
    if value is None:
        raise SettingsError("if_match_required", 428)
    if not re.fullmatch(r'"[0-9]{1,18}"', value):
        raise SettingsError("if_match_invalid", 400)
    return int(value[1:-1])


def _integer(value: str | None) -> int | None:
    if value is None:
        return None
    if not re.fullmatch(r"[1-9][0-9]{0,17}", value):
        raise SettingsError("revision_invalid", 422)
    return int(value)


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError
    return number


def _check_unicode(value: Any) -> None:
    if isinstance(value, str):
        value.encode("utf-8")
    elif isinstance(value, dict):
        for key, item in value.items():
            _check_unicode(key)
            _check_unicode(item)
    elif isinstance(value, list):
        for item in value:
            _check_unicode(item)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


async def _body(request: Request) -> Any:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise SettingsError("content_type_invalid", 400)
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdigit():
            raise SettingsError("content_length_invalid", 400)
        if len(length) > 20 or int(length) > MAX_BODY:
            raise SettingsError("request_too_large", 413)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_BODY:
            raise SettingsError("request_too_large", 413)
        data.extend(chunk)
    try:
        body = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_float=_finite_float,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        _check_unicode(body)
        return body
    except (ValueError, UnicodeError, RecursionError):
        raise SettingsError("malformed_json", 400) from None


def create_settings_app(session_key: str, *, authority: str, store_path: Path) -> FastAPI:
    parsed = urlsplit("http://" + authority)
    try:
        host = parsed.hostname or ""
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
        port = parsed.port
    except ValueError:
        raise ValueError("v1 authority must be the bound loopback authority") from None
    if (
        not loopback
        or not port
        or parsed.netloc != authority
        or parsed.path
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("v1 authority must be the bound loopback authority")
    # Lazy construction: no store creation on ordinary startup or read of legacy APIs.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def protect(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        header = request.headers.get("x-utteran-session")
        supplied = header or request.cookies.get("utteran_gui_session")
        if supplied is None or not secrets.compare_digest(supplied.encode(), session_key.encode()):
            return error_response(SettingsError("session_required", 401))
        if request.headers.get("host") != authority:
            return error_response(SettingsError("host_rejected", 403))
        origin = request.headers.get("origin")
        if origin is not None and origin != "http://" + authority:
            return error_response(SettingsError("origin_rejected", 403))
        if request.method in {"POST", "PUT", "DELETE"} and origin is None and header is None:
            return error_response(SettingsError("origin_required", 403))
        return await call_next(request)

    @app.exception_handler(SettingsError)
    async def failure(request: Request, exc: SettingsError) -> JSONResponse:
        return error_response(exc)

    def repo() -> Repository:
        return Repository(store_path)

    def register(kind: Kind) -> None:
        endpoint = "/api/v1/" + kind
        model = PresetWrite if kind == "presets" else PolicyWrite

        async def collection(request: Request) -> Response:
            if request.method == "GET":
                result = await run_in_threadpool(
                    lambda: repo().list(kind, request.query_params.get("cursor"))
                )
                return JSONResponse(result, headers={"ETag": f'"{result["revision"]}"'})
            key = _key(request)
            body = await _body(request)

            def build() -> PresetWrite | PolicyWrite:
                _version(body)
                payload = model.model_validate(body)
                if isinstance(payload, PresetWrite):
                    issues = semantic_issues(payload.settings, set())
                    if issues:
                        raise SettingsError("settings_invalid", 422, issues)
                return payload

            status, headers, result = await run_in_threadpool(
                lambda: repo().post(endpoint, key, body, payload=build)
            )
            return JSONResponse(result, status_code=status, headers=headers)

        async def resource(resource_id: str, request: Request) -> Response:
            if request.method == "GET":
                revision = _integer(request.query_params.get("revision"))
                result = await run_in_threadpool(lambda: repo().get(kind, resource_id, revision))
            else:
                expected = _match(request)
                payload = None
                if request.method == "PUT":
                    body = await _body(request)
                    _version(body)
                    payload = model.model_validate(body)
                    if isinstance(payload, PresetWrite):
                        issues = semantic_issues(payload.settings, set())
                        if issues:
                            raise SettingsError("settings_invalid", 422, issues)
                updated = await run_in_threadpool(
                    lambda: repo().mutate(kind, resource_id, expected, payload)
                )
                if updated is None:
                    return Response(status_code=204)
                result = updated
            return JSONResponse(result.to_wire(), headers={"ETag": f'"{result.revision}"'})

        app.add_api_route("/" + kind, collection, methods=["GET", "POST"])
        app.add_api_route("/" + kind + "/{resource_id}", resource, methods=["GET", "PUT", "DELETE"])

    register("presets")
    register("execution-policies")

    @app.exception_handler(ValidationError)
    async def invalid_contract(request: Request, exc: ValidationError) -> JSONResponse:
        return error_response(
            SettingsError("contract_validation_failed", 422, safe_contract_issues(exc))
        )

    @app.post("/validations")
    async def validate(request: Request) -> Response:
        key = _key(request)
        body = await _body(request)

        def calculate(db: sqlite3.Connection, repository: Repository) -> dict[str, Any]:
            _version(body)
            session = EditingSession.model_validate(body)
            refs = []
            for kind, ref, path in (
                ("presets", session.preset_ref, "/preset_ref"),
                ("execution-policies", session.policy_ref, "/policy_ref"),
            ):
                try:
                    refs.append(repository._get(db, kind, ref.id, ref.revision))  # type: ignore[arg-type]
                except SettingsError as exc:
                    if exc.status != 404:
                        raise
                    raise SettingsError(
                        "reference_unresolved", 422, (issue(path, "reference_unresolved"),)
                    ) from None
            preset, policy = refs
            assert isinstance(preset, Preset) and isinstance(policy, Policy)
            result = preview(session, preset, policy)
            if not result.valid:
                issues = tuple(
                    issue(f"/files/{index}" + detail.path, detail.code)
                    for index, file in enumerate(result.files)
                    for detail in file.issues
                )
                raise SettingsError("settings_invalid", 422, issues)
            return result.to_wire()

        def perform() -> tuple[int, dict[str, str], dict[str, Any]]:
            repository = repo()
            return repository.post(
                "/api/v1/validations", key, body, evaluate=lambda db: calculate(db, repository)
            )

        status, headers, result = await run_in_threadpool(perform)
        return JSONResponse(result, status_code=status, headers=headers)

    return app


def _key(request: Request) -> str:
    key = request.headers.get("idempotency-key", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{16,128}", key):
        raise SettingsError("idempotency_key_invalid", 400)
    return key


def _version(body: Any) -> None:
    if isinstance(body, dict):
        for data, path in (
            (body, "/schema_version"),
            (body.get("settings"), "/settings/schema_version"),
        ):
            if isinstance(data, dict) and "schema_version" in data:
                version = data["schema_version"]
                if type(version) is not int or version != 1:
                    raise SettingsError(
                        "schema_version_unsupported",
                        422,
                        (issue(path, "schema_version_unsupported"),),
                    )
