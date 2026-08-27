"""Native-window launcher for the loopback GUI server."""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

from utteran_gui.api import create_app
from utteran_gui.logging_runtime import configure_gui_logging, log_stage
from utteran_gui.settings import TokenStore

WINDOWS_APP_USER_MODEL_ID = "Utteran.Utteran"


class NativeDialogApi:
    """Small pywebview bridge with no publicly traversable native objects."""

    def __init__(self) -> None:
        # pywebview recursively walks every public attribute of a js_api
        # object. Its Window reaches native .NET objects, whose mutually
        # referential properties never terminate. pywebview deliberately
        # excludes underscore-prefixed attributes from that traversal.
        self._window: Any = None

    def _attach_window(self, window: Any) -> None:
        """Attach the native window without exposing it to JavaScript."""

        self._window = window

    def choose_path(self, kind: str) -> str | None:
        if self._window is None:
            return None
        import webview

        if kind == "input_file":
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=(
                    "Media files (*.wav;*.mp3;*.m4a;*.flac;*.ogg;*.mp4;*.mkv;*.mov;*.webm)",
                    "All files (*.*)",
                ),
            )
        elif kind in {"input_folder", "output_folder"}:
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        else:
            raise ValueError(f"Unknown dialog kind: {kind}")
        return str(result[0]) if result else None

    def report_frontend_error(self, payload: object) -> bool:
        """Record a bounded browser exception without letting logging affect the UI."""

        if not isinstance(payload, dict):
            return False
        allowed = ("kind", "message", "source", "line", "column")
        fields = {key: str(payload[key])[:2000] for key in allowed if key in payload}
        # Diagnostics must never prevent the page or UI thread from progressing.
        with suppress(Exception):
            logging.getLogger("utteran_gui.frontend").warning(
                "frontend_error", extra={"gui_fields": fields}
            )
        # pywebview 6.2.1 must receive a JSON-serializable value. Returning
        # None makes its bridge evaluate JSON.parse(undefined), which itself
        # raises another frontend error.
        return True


def project_root() -> Path:
    """Resolve the checkout used to locate profile virtual environments."""
    configured = os.environ.get("UTTERAN_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    candidate = Path.cwd().resolve()
    if (candidate / "pyproject.toml").is_file():
        return candidate
    return Path(__file__).resolve().parents[2]


def bind_loopback_socket() -> socket.socket:
    """Let the OS allocate a port while binding only the IPv4 loopback address."""
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(128)
    return server_socket


def set_windows_app_user_model_id() -> bool:
    """Give the process a stable Windows shell identity before creating a window."""
    if os.name != "nt":
        return False
    try:
        import ctypes

        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            return False
        shell32 = loader("shell32")
        result = shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_USER_MODEL_ID)
        return int(result) == 0
    except (AttributeError, OSError):
        return False


def main() -> None:
    """Start uvicorn in the background and pywebview on the GUI thread."""
    if len(sys.argv) == 3 and sys.argv[1] == "--diagnose-keyring":
        destination = Path(sys.argv[2]).resolve()
        destination.write_text(
            json.dumps(TokenStore().diagnose(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    started = time.monotonic()
    set_windows_app_user_model_id()
    configure_gui_logging(install_dir=project_root())
    log_stage("gui_boot_start")
    import uvicorn
    import webview

    session_key = secrets.token_urlsafe(32)
    app = create_app(session_key, repo_root=project_root())
    log_stage("fastapi_app_created")
    server_socket = bind_loopback_socket()
    port = int(server_socket.getsockname()[1])
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [server_socket]},
        name="utteran-gui-server",
        daemon=True,
    )
    log_stage("uvicorn_thread_starting", port=port)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        log_stage("uvicorn_server_start_failed", level=logging.ERROR)
        server.should_exit = True
        thread.join(timeout=2.0)
        server_socket.close()
        raise RuntimeError("GUI server failed to start")
    log_stage(
        "uvicorn_server_started", port=port, elapsed_seconds=round(time.monotonic() - started, 3)
    )
    url = f"http://127.0.0.1:{port}/launch?session={quote(session_key)}"
    native_api = NativeDialogApi()
    log_stage("webview_window_creating")
    window = webview.create_window(
        "utteran", url=url, width=1180, height=820, min_size=(900, 660), js_api=native_api
    )
    native_api._attach_window(window)
    log_stage("webview_window_created", elapsed_seconds=round(time.monotonic() - started, 3))
    try:
        icon_path = project_root() / "icon" / "utteran.ico"
        # webview.start() blocks the calling (main) thread until the window
        # closes - this is expected and not itself a hang. Everything after
        # window creation that *looks* like a startup freeze actually
        # happens inside the page the window loaded (see app.js's boot()
        # sequence and /api/environment), which this stage log cannot see;
        # those stages log through utteran_gui.api instead.
        log_stage("webview_start_blocking")
        webview.start(icon=str(icon_path) if icon_path.is_file() else None)
        log_stage("webview_closed")
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        server_socket.close()
        log_stage("gui_shutdown_complete")


if __name__ == "__main__":
    main()
