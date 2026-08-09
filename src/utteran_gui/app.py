"""Native-window launcher for the loopback GUI server."""

from __future__ import annotations

import os
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import quote

from utteran_gui.api import create_app


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
    webview.create_window("utteran", url=url, width=1180, height=820, min_size=(900, 660))
    try:
        webview.start()
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        server_socket.close()


if __name__ == "__main__":
    main()
