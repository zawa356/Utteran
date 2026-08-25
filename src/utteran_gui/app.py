"""Native-window launcher for the loopback GUI server."""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from utteran_gui.api import create_app
from utteran_gui.settings import TokenStore


class NativeDialogApi:
    """pywebview dialog bridge; returned paths are deliberately not persisted."""

    def __init__(self) -> None:
        self.window: Any = None

    def choose_path(self, kind: str) -> str | None:
        if self.window is None:
            return None
        import webview

        if kind == "input_file":
            result = self.window.create_file_dialog(
                webview.OPEN_DIALOG,
                allow_multiple=False,
                file_types=(
                    "Media files (*.wav;*.mp3;*.m4a;*.flac;*.ogg;*.mp4;*.mkv;*.mov;*.webm)",
                    "All files (*.*)",
                ),
            )
        elif kind in {"input_folder", "output_folder"}:
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        else:
            raise ValueError(f"Unknown dialog kind: {kind}")
        return str(result[0]) if result else None


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


def main() -> None:
    """Start uvicorn in the background and pywebview on the GUI thread."""
    if len(sys.argv) == 3 and sys.argv[1] == "--diagnose-keyring":
        destination = Path(sys.argv[2]).resolve()
        destination.write_text(
            json.dumps(TokenStore().diagnose(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    import uvicorn
    import webview

    session_key = secrets.token_urlsafe(32)
    app = create_app(session_key, repo_root=project_root())
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
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2.0)
        server_socket.close()
        raise RuntimeError("GUI server failed to start")
    url = f"http://127.0.0.1:{port}/launch?session={quote(session_key)}"
    native_api = NativeDialogApi()
    window = webview.create_window(
        "utteran", url=url, width=1180, height=820, min_size=(900, 660), js_api=native_api
    )
    native_api.window = window
    try:
        webview.start()
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        server_socket.close()


if __name__ == "__main__":
    main()
