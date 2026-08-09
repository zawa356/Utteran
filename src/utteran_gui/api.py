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
from typing import Literal, cast

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from utteran_gui.cli import CliAdapter, CliError, TranscriptionOptions
from utteran_gui.environment import EnvironmentService
from utteran_gui.jobs import JobBusyError, JobManager, JobUnknownError
from utteran_gui.settings import GuiSettings, SettingsStore, TokenStore

SESSION_COOKIE = "utteran_gui_session"
SESSION_HEADER = "x-utteran-session"


class SettingsPayload(BaseModel):
    theme: Literal["dark", "light"] = "dark"
    language: Literal["ja", "en"] = "ja"
    default_profile: Literal["cpu", "cuda", "intel", "vulkan"] | None = None
    default_input_dir: str = Field(default="", max_length=4096)
    default_output_dir: str = Field(default="", max_length=4096)


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


def create_app(
    session_key: str,
    *,
    repo_root: Path,
    cli: CliAdapter | None = None,
    settings_store: SettingsStore | None = None,
    token_store: TokenStore | None = None,
    environment_service: EnvironmentService | None = None,
    job_manager: JobManager | None = None,
) -> FastAPI:
    """Create one session-scoped application without importing the CLI package."""
    if not session_key:
        raise ValueError("session_key must not be empty")
    selected_cli = cli or CliAdapter(repo_root)
    selected_settings = settings_store or SettingsStore()
    selected_tokens = token_store or TokenStore()
    selected_environment = environment_service or EnvironmentService(selected_cli)
    selected_jobs = job_manager or JobManager(selected_cli)
    web_root = Path(__file__).with_name("web")
    app = FastAPI(title="utteran GUI", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/assets", StaticFiles(directory=web_root), name="assets")

    @app.middleware("http")
    async def secure_api(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path.startswith("/api/"):
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
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/launch", include_in_schema=False)
    def launch(session: str = "") -> Response:
        if not secrets.compare_digest(session, session_key):
            logging.getLogger(__name__).warning("GUI launch session-key mismatch")
            raise HTTPException(status_code=401, detail="Unauthorized")
        response = RedirectResponse("/", status_code=303)
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

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/environment")
    def environment(profile: str | None = None) -> dict[str, object]:
        selected = profile or selected_settings.load().default_profile
        return selected_environment.snapshot(selected)

    @app.get("/api/settings")
    def get_settings() -> dict[str, object]:
        payload = selected_settings.load().to_dict()
        payload["token_configured"] = selected_tokens.is_configured()
        return payload

    @app.put("/api/settings")
    def put_settings(payload: SettingsPayload) -> dict[str, object]:
        saved = selected_settings.save(GuiSettings.from_dict(payload.model_dump()))
        response = saved.to_dict()
        response["token_configured"] = selected_tokens.is_configured()
        return response

    @app.get("/api/token")
    def token_status() -> dict[str, bool]:
        return {"configured": selected_tokens.is_configured()}

    @app.put("/api/token")
    def set_token(payload: TokenPayload) -> dict[str, bool]:
        try:
            selected_tokens.set(payload.token)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "OS keyring rejected token storage: %s", type(exc).__name__
            )
            raise HTTPException(status_code=503, detail="OS keyring is unavailable") from None
        return {"configured": True}

    @app.delete("/api/token")
    def clear_token() -> dict[str, bool]:
        try:
            selected_tokens.clear()
        except Exception:
            raise HTTPException(status_code=503, detail="OS keyring is unavailable") from None
        return {"configured": False}

    @app.post("/api/jobs", status_code=202)
    def start_job(payload: TranscriptionPayload) -> dict[str, object]:
        snapshot = selected_environment.snapshot(payload.profile)
        _validate_dynamic_selection(payload, snapshot)
        try:
            return selected_jobs.start(_to_options(payload))
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
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
            )
        except OSError:
            raise HTTPException(status_code=503, detail="Could not open folder") from None
        return Response(status_code=204)

    return app


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
