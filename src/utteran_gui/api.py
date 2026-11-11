"""Loopback-only FastAPI surface used by the native webview window."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from utteran_gui import __version__, hardware
from utteran_gui.cli import CliAdapter, CliError, RegenerationOptions, TranscriptionOptions
from utteran_gui.environment import EnvironmentService
from utteran_gui.history import HistoryError, HistoryService
from utteran_gui.jobs import JobBusyError, JobManager, JobUnknownError
from utteran_gui.logging_runtime import gui_logging_status
from utteran_gui.next_ui import (
    ASSET_CACHE_CONTROL,
    DEFAULT_ASSET_ROOT,
    NEXT_UI_PREFIX,
    NextUiAssetsError,
    content_security_policy,
    new_nonce,
    try_load_bundle,
)
from utteran_gui.operation_queue import OperationQueue, QueueClosedError
from utteran_gui.processes import ProcessSupervisor, build_creation_kwargs
from utteran_gui.settings import (
    GuiSettings,
    SessionTokenStore,
    SettingsStore,
    TokenStore,
    TokenStoreUnavailable,
)
from utteran_gui.setup_wizard import (
    SetupWizardService,
    WizardBusyError,
    WizardNotReadyError,
    WizardProfileMissingError,
    WizardUnknownJobError,
)
from utteran_paths import is_portable_distribution, uses_session_token

SESSION_COOKIE = "utteran_gui_session"
SESSION_HEADER = "x-utteran-session"
V1_OWNS_SLOT = "The opt-in v1 job runtime owns the execution slot"


class SettingsPayload(BaseModel):
    theme: Literal["system", "dark", "light"] = "system"
    language: Literal["ja", "en"] = "ja"
    default_profile: Literal["cpu", "cuda", "intel", "vulkan"] | None = None
    default_input_dir: str = Field(default="", max_length=4096)
    default_output_dir: str = Field(default="", max_length=4096)


class SettingsPatchPayload(BaseModel):
    theme: Literal["system", "dark", "light"] | None = None
    language: Literal["ja", "en"] | None = None
    default_profile: Literal["cpu", "cuda", "intel", "vulkan"] | None = None
    default_input_dir: str | None = Field(default=None, max_length=4096)
    default_output_dir: str | None = Field(default=None, max_length=4096)


class TokenPayload(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


class TranscriptionPayload(BaseModel):
    input_path: str = Field(min_length=1, max_length=32768)
    output_dir: str = Field(min_length=1, max_length=32768)
    profile: Literal["cpu", "cuda", "intel", "vulkan"]
    asr_backend: str = Field(min_length=1, max_length=100)
    asr_model: str = Field(min_length=1, max_length=1000)
    asr_device: str = Field(min_length=1, max_length=100)
    diarization_enabled: bool = True
    diarization_backend: str = Field(default="pyannote", max_length=100)
    diarization_model: str = Field(default="", max_length=1000)
    diarization_device: str = Field(default="cpu", max_length=100)
    num_speakers: int | None = Field(default=None, ge=1, le=100)
    min_speakers: int | None = Field(default=None, ge=1, le=100)
    max_speakers: int | None = Field(default=None, ge=1, le=100)
    language: str = Field(default="ja", max_length=32)
    formats: list[Literal["srt", "vtt", "json", "txt", "md"]] = Field(min_length=1)
    resume_mode: Literal["resume", "fresh", "force"] = "resume"
    recursive: bool = False
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class OpenFolderPayload(BaseModel):
    path: str = Field(min_length=1, max_length=32768)


class WizardJobPayload(BaseModel):
    kind: Literal["venv_build", "model_download", "native_build", "smoke_test"]
    profile: Literal["cpu", "cuda", "intel", "vulkan"]
    model_ref: str | None = Field(default=None, max_length=200)
    diarization_enabled: bool = False


class ModelActionPayload(BaseModel):
    profile: Literal["cpu", "cuda", "intel", "vulkan"]
    action: Literal["download", "remove", "verify", "prepare_openvino", "remove_openvino"]
    model_ref: str = Field(min_length=1, max_length=200)


class WizardTokenPreflightPayload(BaseModel):
    profile: Literal["cpu", "cuda", "intel", "vulkan"]
    check_model: str = Field(
        default="pyannote:pyannote/speaker-diarization-community-1", max_length=200
    )


class WizardStatePayload(BaseModel):
    step: Literal[
        "welcome", "profile", "diarization", "token", "model", "confirm", "execution", "done"
    ]
    profile: Literal["cpu", "cuda", "intel", "vulkan"] | None = None
    diarization_enabled: bool | None = None
    model_ref: str | None = Field(default=None, max_length=200)
    token_error: (
        Literal["token_missing", "token_invalid", "agreement_required", "network_error"] | None
    ) = None


class RegenerationPayload(BaseModel):
    profile: Literal["cpu", "cuda", "intel", "vulkan"]
    output_dir: str = Field(min_length=1, max_length=32768)
    formats: list[Literal["srt", "vtt", "json", "txt", "md"]] = Field(min_length=1)
    speaker_labels: dict[str, str] = Field(default_factory=dict)


def create_app(
    session_key: str,
    *,
    repo_root: Path,
    cli: CliAdapter | None = None,
    settings_store: SettingsStore | None = None,
    token_store: TokenStore | None = None,
    environment_service: EnvironmentService | None = None,
    job_manager: JobManager | None = None,
    history_service: HistoryService | None = None,
    wizard_service: SetupWizardService | None = None,
    operation_queue: OperationQueue | None = None,
    process_supervisor: ProcessSupervisor | None = None,
    enable_v1_settings: bool = False,
    v1_authority: str | None = None,
    v1_store_path: Path | None = None,
    enable_v1_jobs: bool = False,
    v1_jobs_runtime: Any = None,
    enable_v1_setup: bool = False,
    enable_v1_events: bool = False,
    v1_events_config: Any = None,
    enable_next_ui: bool = False,
    next_ui_root: Path | None = None,
) -> FastAPI:
    """Create one session-scoped application without importing the CLI package.

    ``enable_next_ui`` (default off) additionally serves the UI-2 frontend under ``/next/``
    and makes ``/launch`` redirect there. It never enables any ``/api/v1`` surface.
    """
    if not session_key:
        raise ValueError("session_key must not be empty")
    supervisor = process_supervisor or ProcessSupervisor()
    selected_settings = settings_store or SettingsStore()
    selected_tokens = token_store or (SessionTokenStore() if uses_session_token() else TokenStore())
    selected_cli = cli or CliAdapter(
        repo_root,
        popen_factory=supervisor.popen,
        session_token=selected_tokens.session_token,
    )
    selected_environment = environment_service or EnvironmentService(selected_cli)
    selected_queue = operation_queue or (
        job_manager.queue
        if job_manager is not None
        else wizard_service.queue
        if wizard_service is not None
        else OperationQueue()
    )
    selected_jobs = job_manager or JobManager(
        selected_cli, popen_factory=supervisor.popen, operation_queue=selected_queue
    )
    selected_history = history_service or HistoryService(selected_cli)
    selected_wizard = wizard_service or SetupWizardService(
        selected_cli,
        settings_store=selected_settings,
        popen_factory=supervisor.popen,
        operation_queue=selected_queue,
    )
    web_root = Path(__file__).with_name("web")
    app = FastAPI(title="utteran GUI", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.process_supervisor = supervisor
    if enable_v1_settings:
        from utteran_gui.settings_api import create_settings_app
        from utteran_paths import resolve_data_paths

        if v1_authority is None:
            raise ValueError("v1_authority is required when enabling v1 settings")
        settings_path = v1_store_path or resolve_data_paths(repo_root).settings_resources
        v1_app = create_settings_app(session_key, authority=v1_authority, store_path=settings_path)
        if enable_v1_jobs:
            # Same sub-app: one /api/v1 mount, one session/Host/Origin boundary.
            from utteran_gui.jobs_api import register_job_routes

            v1_runtime = v1_jobs_runtime or _default_job_runtime(
                repo_root,
                settings_path,
                selected_queue,
                selected_cli,
                setup=enable_v1_setup,
                tokens=selected_tokens,
                popen_factory=supervisor.popen,
            )
            if enable_v1_setup and getattr(v1_runtime, "setup", None) is None:
                raise ValueError("v1 setup requires a job runtime with setup components")
            # activate() (the explicit opt-in cutover) is deferred to the very end of
            # create_app(): routes read the runtime lazily, so every step that can fail
            # (route registration, mounts, StaticFiles, middleware) runs before any
            # legacy-queue close, DB backup/migration or dispatcher start (G-09).
            app.state.v1_jobs_runtime = v1_runtime
            register_job_routes(v1_app, v1_runtime)
            if enable_v1_setup:
                # Same sub-app and same runtime: setup and jobs share one heavy slot.
                from utteran_gui.setup_api import register_setup_routes

                register_setup_routes(v1_app, v1_runtime)
            if enable_v1_events:
                # B9: SSE /events, /sync and diagnostics on the same sub-app and session.
                from utteran_gui.events_api import register_event_routes

                register_event_routes(v1_app, v1_runtime, v1_events_config)
        elif enable_v1_setup or enable_v1_events:
            raise ValueError("v1 setup/events require the v1 job runtime")
        app.mount("/api/v1", v1_app)
    elif enable_v1_jobs or enable_v1_setup or enable_v1_events:
        raise ValueError("v1 jobs/setup/events require the v1 settings application")

    def shutdown_processes() -> None:
        runtime = getattr(app.state, "v1_jobs_runtime", None)
        if runtime is not None:
            runtime.shutdown()
        selected_jobs.shutdown()
        selected_wizard.shutdown()
        supervisor.shutdown()

    app.router.add_event_handler("shutdown", shutdown_processes)
    app.mount("/assets", StaticFiles(directory=web_root), name="assets")
    next_bundle = try_load_bundle(next_ui_root or DEFAULT_ASSET_ROOT) if enable_next_ui else None
    if next_bundle is not None and not isinstance(next_bundle, NextUiAssetsError):
        app.mount(
            f"{NEXT_UI_PREFIX}/assets",
            StaticFiles(directory=next_bundle.assets),
            name="next-assets",
        )

    @app.middleware("http")
    async def secure_api(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path.startswith("/api/") and not (
            enable_v1_settings and request.url.path.startswith("/api/v1/")
        ):
            supplied = request.headers.get(SESSION_HEADER) or request.cookies.get(SESSION_COOKIE)
            if supplied is None or not secrets.compare_digest(supplied, session_key):
                logging.getLogger(__name__).warning(
                    "GUI API session-key mismatch from %s",
                    request.client.host if request.client else "unknown",
                )
                return Response(status_code=401, content="Unauthorized")
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif (
            next_bundle is not None
            and request.url.path.startswith(f"{NEXT_UI_PREFIX}/assets/")
            and response.status_code == 200
        ):
            response.headers["Cache-Control"] = ASSET_CACHE_CONTROL
        # Only the opt-in next UI page sets a nonce; every other response keeps the
        # legacy policy byte for byte. script-src is never relaxed.
        response.headers["Content-Security-Policy"] = content_security_policy(
            getattr(request.state, "csp_nonce", None)
        )
        return response

    @app.get("/launch", include_in_schema=False)
    def launch(session: str = "") -> Response:
        if not secrets.compare_digest(session, session_key):
            logging.getLogger(__name__).warning("GUI launch session-key mismatch")
            raise HTTPException(status_code=401, detail="Unauthorized")
        # The next UI is chosen only by the explicit create_app flag, never by settings.
        target = f"{NEXT_UI_PREFIX}/" if enable_next_ui else "/"
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            session_key,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_root / "index.html")

    if next_bundle is not None:

        @app.get(NEXT_UI_PREFIX, include_in_schema=False)
        def next_index_redirect() -> Response:
            return RedirectResponse(f"{NEXT_UI_PREFIX}/", status_code=307)

        @app.get(f"{NEXT_UI_PREFIX}/", include_in_schema=False)
        def next_index(request: Request) -> Response:
            if isinstance(next_bundle, NextUiAssetsError):
                # Fail closed and diagnosable: never fall back to the legacy page silently.
                return PlainTextResponse(
                    f"Utteran next UI is unavailable ({next_bundle.code}). "
                    "Build it with: npm ci && npm run build (in frontend/).",
                    status_code=503,
                    headers={"Cache-Control": "no-store"},
                )
            nonce = new_nonce()
            request.state.csp_nonce = nonce
            return HTMLResponse(next_bundle.render(nonce), headers={"Cache-Control": "no-store"})

    @app.get("/logo", include_in_schema=False)
    def logo() -> FileResponse:
        return FileResponse(repo_root / "icon" / "utteran-glyph-512.png")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/version")
    def version() -> dict[str, str]:
        return {"version": __version__}

    @app.get("/api/environment")
    def environment(profile: str | None = None, refresh: bool = False) -> dict[str, object]:
        selected = profile or selected_settings.load().default_profile
        return selected_environment.snapshot(selected, refresh_devices=refresh)

    @app.get("/api/queue")
    def operation_queue_status() -> list[dict[str, object]]:
        return selected_queue.list()

    @app.post("/api/queue/{item_id}/cancel", status_code=202)
    def cancel_queue_item(item_id: str) -> dict[str, object]:
        try:
            return selected_queue.cancel(item_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Queue item not found") from None

    @app.get("/api/settings")
    def get_settings() -> dict[str, object]:
        payload = selected_settings.load().to_dict()
        token_status = selected_tokens.status()
        payload["token_configured"] = token_status.configured
        payload["token_store_available"] = token_status.available
        payload["token_store_backend"] = token_status.backend
        payload["token_store_error"] = token_status.error_type
        payload["portable_distribution"] = is_portable_distribution()
        payload["token_session_only"] = uses_session_token()
        payload.update(_logging_status(repo_root))
        return payload

    @app.put("/api/settings")
    def put_settings(payload: SettingsPayload) -> dict[str, object]:
        saved = selected_settings.save(GuiSettings.from_dict(payload.model_dump()))
        response = saved.to_dict()
        token_status = selected_tokens.status()
        response["token_configured"] = token_status.configured
        response["token_store_available"] = token_status.available
        response["portable_distribution"] = is_portable_distribution()
        response["token_session_only"] = uses_session_token()
        response.update(_logging_status(repo_root))
        return response

    @app.patch("/api/settings")
    def patch_settings(payload: SettingsPatchPayload) -> dict[str, object]:
        saved = selected_settings.update(payload.model_dump(exclude_unset=True))
        response = saved.to_dict()
        token_status = selected_tokens.status()
        response["token_configured"] = token_status.configured
        response["token_store_available"] = token_status.available
        response["portable_distribution"] = is_portable_distribution()
        response["token_session_only"] = uses_session_token()
        response.update(_logging_status(repo_root))
        return response

    @app.get("/api/token")
    def token_status() -> dict[str, object]:
        return selected_tokens.status().to_dict()

    @app.put("/api/token")
    def set_token(payload: TokenPayload) -> dict[str, object]:
        try:
            selected_tokens.set(payload.token)
        except TokenStoreUnavailable as exc:
            logging.getLogger(__name__).warning(
                "OS keyring rejected token storage: %s", type(exc).__name__
            )
            raise HTTPException(status_code=503, detail="OS keyring is unavailable") from None
        return selected_tokens.status().to_dict()

    @app.delete("/api/token")
    def clear_token() -> dict[str, object]:
        try:
            selected_tokens.clear()
        except TokenStoreUnavailable:
            raise HTTPException(status_code=503, detail="OS keyring is unavailable") from None
        return selected_tokens.status().to_dict()

    @app.get("/api/wizard/status")
    def wizard_status() -> dict[str, object]:
        return selected_wizard.status()

    @app.post("/api/wizard/start")
    def start_wizard() -> dict[str, object]:
        return selected_wizard.start()

    @app.put("/api/wizard/state")
    def save_wizard_state(payload: WizardStatePayload) -> dict[str, object]:
        return selected_wizard.save_state(
            payload.step,
            profile=payload.profile,
            diarization_enabled=payload.diarization_enabled,
            model_ref=payload.model_ref,
            token_error=payload.token_error,
        )

    @app.post("/api/wizard/token-preflight")
    def wizard_token_preflight(payload: WizardTokenPreflightPayload) -> dict[str, object]:
        try:
            result = selected_cli.run_json(
                payload.profile,
                ["config", "token-status", "--json", "--check-model", payload.check_model],
            )
        except CliError as exc:
            logging.getLogger(__name__).warning(
                "Profile token preflight failed: %s", type(exc).__name__
            )
            selected_wizard.record_preflight("network_error")
            raise HTTPException(
                status_code=409, detail="Profile CLI token preflight failed"
            ) from None
        if not isinstance(result, dict):
            raise HTTPException(status_code=409, detail="Profile CLI returned invalid token status")
        allowed = {
            "configured": bool(result.get("configured")),
            "source": result.get("source")
            if result.get("source") in {"environment", "dotenv", "keyring", "none"}
            else "none",
            "keyring_available": bool(result.get("keyring_available")),
            "access": result.get("access")
            if result.get("access")
            in {
                "available",
                "token_missing",
                "token_invalid",
                "agreement_required",
                "network_error",
            }
            else "network_error",
        }
        selected_wizard.record_preflight(str(allowed["access"]))
        return allowed

    @app.get("/api/wizard/hardware")
    def wizard_hardware() -> dict[str, object]:
        return hardware.detect_hardware(
            repo_root, probes=hardware.system_probes(popen_factory=supervisor.popen)
        ).to_dict()

    def refuse_when_v1_owns_slot() -> None:
        # G-09: once the opt-in v1 runtime closed the legacy queue, new legacy work is
        # refused before any probe/subprocess/settings side effect. Reads and cancels
        # of existing legacy items stay available. submit() still checks under its lock.
        if selected_queue.closed:
            raise HTTPException(status_code=409, detail=V1_OWNS_SLOT)

    @app.post("/api/wizard/jobs", status_code=202)
    def start_wizard_job(payload: WizardJobPayload) -> dict[str, object]:
        refuse_when_v1_owns_slot()
        try:
            if payload.kind == "venv_build":
                return selected_wizard.start_venv_build(payload.profile)
            if payload.kind == "model_download":
                if not payload.model_ref or not payload.model_ref.strip():
                    raise HTTPException(status_code=422, detail="model_ref is required")
                return selected_wizard.start_model_download(payload.profile, payload.model_ref)
            if payload.kind == "native_build":
                if not payload.model_ref or not payload.model_ref.strip():
                    raise HTTPException(status_code=422, detail="model_ref is required")
                return selected_wizard.start_native_build(payload.profile, payload.model_ref)
            return selected_wizard.start_smoke_test(
                payload.profile,
                asr_model_ref=payload.model_ref,
                diarization_enabled=payload.diarization_enabled,
            )
        except WizardBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except WizardProfileMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except QueueClosedError:
            raise HTTPException(status_code=409, detail=V1_OWNS_SLOT) from None

    @app.get("/api/wizard/jobs/{job_id}")
    def get_wizard_job(job_id: str) -> dict[str, object]:
        try:
            return selected_wizard.snapshot(job_id)
        except WizardUnknownJobError:
            raise HTTPException(status_code=404, detail="Job not found") from None

    @app.post("/api/models/actions", status_code=202)
    def start_model_action(payload: ModelActionPayload) -> dict[str, object]:
        refuse_when_v1_owns_slot()
        try:
            return selected_wizard.start_model_action(
                payload.profile, payload.action, payload.model_ref
            )
        except WizardBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except WizardProfileMissingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except QueueClosedError:
            raise HTTPException(status_code=409, detail=V1_OWNS_SLOT) from None

    @app.post("/api/wizard/jobs/{job_id}/cancel", status_code=202)
    def cancel_wizard_job(job_id: str) -> dict[str, object]:
        try:
            return selected_wizard.cancel(job_id)
        except WizardUnknownJobError:
            raise HTTPException(status_code=404, detail="Job not found") from None

    @app.get("/api/wizard/jobs/{job_id}/events")
    async def stream_wizard_events(job_id: str, request: Request) -> StreamingResponse:
        try:
            selected_wizard.snapshot(job_id)
        except WizardUnknownJobError:
            raise HTTPException(status_code=404, detail="Job not found") from None

        async def generate() -> AsyncIterator[str]:
            cursor = 0
            idle_ticks = 0
            while not await request.is_disconnected():
                events, terminal = selected_wizard.events_since(job_id, cursor)
                for event in events:
                    cursor += 1
                    event_name = str(event.get("event", "message"))
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor - 1}\nevent: {event_name}\ndata: {data}\n\n"
                if terminal and not events:
                    break
                idle_ticks += 1
                if idle_ticks % 30 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/api/wizard/complete")
    def complete_wizard() -> dict[str, object]:
        try:
            return selected_wizard.complete()
        except WizardNotReadyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    @app.post("/api/jobs", status_code=202)
    def start_job(payload: TranscriptionPayload) -> dict[str, object]:
        refuse_when_v1_owns_slot()
        snapshot = selected_environment.snapshot(payload.profile)
        _validate_dynamic_selection(payload, snapshot)
        try:
            result = selected_jobs.start(_to_options(payload))
            try:
                selected_settings.remember_directories(
                    input_path=payload.input_path,
                    output_dir=payload.output_dir,
                )
            except OSError:
                logging.getLogger(__name__).warning("Could not remember job directories")
            return result
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except QueueClosedError:
            raise HTTPException(status_code=409, detail=V1_OWNS_SLOT) from None
        except CliError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        try:
            return selected_jobs.snapshot(job_id)
        except JobUnknownError:
            raise HTTPException(status_code=404, detail="Job not found") from None

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    def cancel_job(job_id: str) -> dict[str, object]:
        try:
            return selected_jobs.cancel(job_id)
        except JobUnknownError:
            raise HTTPException(status_code=404, detail="Job not found") from None

    @app.get("/api/jobs/{job_id}/events")
    async def stream_events(job_id: str, request: Request) -> StreamingResponse:
        try:
            selected_jobs.snapshot(job_id)
        except JobUnknownError:
            raise HTTPException(status_code=404, detail="Job not found") from None

        async def generate() -> AsyncIterator[str]:
            cursor = 0
            idle_ticks = 0
            while not await request.is_disconnected():
                events, terminal = selected_jobs.events_since(job_id, cursor)
                for event in events:
                    cursor += 1
                    event_name = str(event.get("event", "message"))
                    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    yield f"id: {cursor - 1}\nevent: {event_name}\ndata: {data}\n\n"
                if terminal and not events:
                    break
                idle_ticks += 1
                if idle_ticks % 30 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/api/history")
    def history(profile: Literal["cpu", "cuda", "intel", "vulkan"]) -> object:
        try:
            return selected_history.list(profile)
        except (CliError, HistoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/history/{job_id}")
    def history_detail(
        job_id: str,
        profile: Literal["cpu", "cuda", "intel", "vulkan"],
    ) -> object:
        try:
            return selected_history.detail(profile, job_id)
        except (CliError, HistoryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.delete("/api/history/{job_id}")
    def delete_history(
        job_id: str,
        profile: Literal["cpu", "cuda", "intel", "vulkan"],
    ) -> object:
        try:
            result = selected_cli.delete_job(profile, job_id)
            selected_history.invalidate(profile, job_id)
            return result
        except CliError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/history/{job_id}/regenerate")
    def regenerate_history(job_id: str, payload: RegenerationPayload) -> object:
        if any(len(key) > 100 or len(value) > 100 for key, value in payload.speaker_labels.items()):
            raise HTTPException(
                status_code=422, detail="Speaker labels must be 100 characters or less"
            )
        try:
            result = selected_cli.regenerate(
                RegenerationOptions(
                    job_id=job_id,
                    profile=payload.profile,
                    output_dir=payload.output_dir,
                    formats=tuple(payload.formats),
                    speaker_labels=dict(payload.speaker_labels),
                )
            )
            selected_history.invalidate(payload.profile, job_id)
            try:
                selected_settings.remember_directories(output_dir=payload.output_dir)
            except OSError:
                logging.getLogger(__name__).warning("Could not remember output directory")
            return result
        except CliError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/open-folder", status_code=204)
    def open_folder(payload: OpenFolderPayload) -> Response:
        path = Path(payload.path).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(status_code=400, detail="Folder does not exist")
        command = ["explorer.exe", str(path)] if os.name == "nt" else ["xdg-open", str(path)]
        try:
            subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **build_creation_kwargs(),
            )
        except OSError:
            raise HTTPException(status_code=503, detail="Could not open folder") from None
        return Response(status_code=204)

    v1_runtime_to_activate = getattr(app.state, "v1_jobs_runtime", None)
    if v1_runtime_to_activate is not None:
        # Last step, nothing after it can fail: refuses if legacy work exists; on its own
        # failure activate() stops what it started and reopens only the queue it closed.
        v1_runtime_to_activate.activate()
    return app


def _default_job_runtime(
    repo_root: Path,
    settings_path: Path,
    queue: OperationQueue,
    cli: CliAdapter,
    *,
    setup: bool = False,
    tokens: Any = None,
    popen_factory: Any = None,
) -> Any:
    """Opt-in only: imported lazily so default startup never loads the control store."""
    from utteran_gui.capability_inventory import CapabilityInventoryAdapter
    from utteran_gui.job_admission import BridgeResolutionProvider
    from utteran_gui.job_runtime import JobRuntime
    from utteran_gui.worker_client import WorkerClient
    from utteran_paths import resolve_data_paths

    paths = resolve_data_paths(repo_root)
    components = None
    if setup:
        from utteran_gui.job_runtime import SetupComponents
        from utteran_gui.setup_executor import ProcessChildRunner, ProductionStepAdapter
        from utteran_gui.setup_planner import SetupObserver

        def credential_present() -> bool:
            # Presence only; the value is never read into the control store.
            return bool(tokens is not None and tokens.status().configured)

        components = SetupComponents(
            observer=SetupObserver(
                cli, models_root=paths.models, credential_present=credential_present
            ),
            adapter=ProductionStepAdapter(cli),
            runner=ProcessChildRunner(popen_factory),
        )
    return JobRuntime(
        store_path=paths.control_store,
        settings_path=settings_path,
        legacy_queue=queue,
        provider=BridgeResolutionProvider(CapabilityInventoryAdapter(cli)),
        runner=WorkerClient(repo_root),
        job_dir=paths.jobs,
        setup=components,
    )


def _logging_status(repo_root: Path) -> dict[str, object]:
    """Return non-sensitive state needed to keep raw logging visibly apparent."""
    active = gui_logging_status(install_dir=repo_root)
    return {
        "log_dir": str(active.log_dir),
        "log_dir_fallback": active.fell_back,
        "raw_subprocess_logs": active.raw_subprocess_logs,
    }


def _to_options(payload: TranscriptionPayload) -> TranscriptionOptions:
    return TranscriptionOptions(
        input_path=payload.input_path,
        output_dir=payload.output_dir,
        profile=payload.profile,
        asr_backend=payload.asr_backend,
        asr_model=payload.asr_model,
        asr_device=payload.asr_device,
        diarization_enabled=payload.diarization_enabled,
        diarization_backend=payload.diarization_backend,
        diarization_model=payload.diarization_model,
        diarization_device=payload.diarization_device,
        num_speakers=payload.num_speakers,
        min_speakers=payload.min_speakers,
        max_speakers=payload.max_speakers,
        language=payload.language,
        formats=tuple(payload.formats),
        resume_mode=payload.resume_mode,
        recursive=payload.recursive,
        include=tuple(payload.include),
        exclude=tuple(payload.exclude),
    )


def _validate_dynamic_selection(
    payload: TranscriptionPayload,
    snapshot: dict[str, object],
) -> None:
    """Reject stale or forged choices that current detection did not advertise."""
    options = snapshot.get("options")
    if not isinstance(options, dict):
        raise HTTPException(status_code=409, detail="Environment detection is unavailable")
    asr = _find_option(options.get("asr"), payload.asr_backend)
    if asr is None:
        raise HTTPException(status_code=409, detail="ASR backend is unavailable")
    if not _contains(asr.get("models"), "id", payload.asr_model):
        raise HTTPException(status_code=409, detail="ASR model is unavailable")
    if not _contains(asr.get("devices"), "id", payload.asr_device):
        raise HTTPException(status_code=409, detail="ASR device is unavailable")
    if (
        payload.asr_backend == "openvino-genai"
        and payload.diarization_enabled
        and payload.language.casefold() in {"auto", "ja", "zh", "th", "lo", "my", "yue"}
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "OpenVINO GenAI cannot be combined with diarization for auto or a non-space "
                "language because reliable word timestamps are unavailable. Use whisper.cpp."
            ),
        )
    if payload.diarization_enabled:
        diarization = _find_option(options.get("diarization"), payload.diarization_backend)
        if diarization is None:
            raise HTTPException(status_code=409, detail="Diarization backend is unavailable")
        if not _contains(diarization.get("models"), "id", payload.diarization_model):
            raise HTTPException(status_code=409, detail="Diarization model is unavailable")
        if not _contains(diarization.get("devices"), "id", payload.diarization_device):
            raise HTTPException(status_code=409, detail="Diarization device is unavailable")


def _find_option(value: object, identifier: str) -> dict[str, object] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and item.get("id") == identifier:
            return cast(dict[str, object], item)
    return None


def _contains(value: object, key: str, expected: str) -> bool:
    if not isinstance(value, list):
        return False
    return any(isinstance(item, dict) and item.get(key) == expected for item in value)
