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
from utteran_gui.instance_guard import (
    GuiInstanceLock,
    LaunchMode,
    StartupRefused,
    launch_mode,
    lock_directory,
    validate_dev_data_root,
)
from utteran_gui.logging_runtime import configure_gui_logging, log_stage
from utteran_gui.settings import TokenStore
from utteran_paths import DATA_ROOT_ENV, resolve_data_paths

WINDOWS_APP_USER_MODEL_ID = "Utteran.Utteran"


def _dropped_input_items(event: object) -> list[dict[str, str]]:
    """Extract ephemeral full paths added by pywebview's native drop bridge."""

    if not isinstance(event, dict):
        return []
    transfer = event.get("dataTransfer")
    if not isinstance(transfer, dict):
        return []
    files = transfer.get("files")
    if not isinstance(files, list):
        return []
    items: list[dict[str, str]] = []
    for file in files:
        if not isinstance(file, dict):
            continue
        value = file.get("pywebviewFullPath")
        if not isinstance(value, str) or not value:
            continue
        path = Path(value)
        kind = "folder" if path.is_dir() else "file" if path.is_file() else "unknown"
        items.append({"path": value, "kind": kind})
    return items


def _bind_input_drop(window: Any) -> None:
    """Bind native file drop handling after the WebView2 document is ready."""

    from webview.dom import DOMEventHandler

    target = window.dom.get_element("#input-drop-zone")
    if target is None:
        logging.getLogger(__name__).warning("Input drop target was not found")
        return

    def on_drop(event: object) -> None:
        # Paths remain in process memory and are sent only to the input field.
        # Never log this payload: file names can contain personal information.
        payload = json.dumps(_dropped_input_items(event), ensure_ascii=False)
        window.evaluate_js(
            "window.dispatchEvent(new CustomEvent('utteran-input-dropped', "
            f"{{detail: {payload}}}));"
        )

    def ignore_drag(_event: object) -> None:
        return

    target.events.dragenter += DOMEventHandler(ignore_drag, True, True)
    target.events.dragover += DOMEventHandler(ignore_drag, True, True, debounce=100)
    target.events.drop += DOMEventHandler(on_drop, True, True)
    log_stage("input_drop_bound")


# Per-document marker: a JS global vanishes with its document, so a navigation (new UI ->
# "旧画面を開く" -> legacy UI) gets a fresh binding, while the same document is never bound
# twice (pywebview runs the start function and `loaded` handlers on separate threads).
_DROP_BOUND_MARKER = "__utteranInputDropBound"
_DROP_BIND_LOCK = threading.Lock()


def _bind_input_drop_once(window: Any) -> None:
    """Bind the legacy drop target at most once per loaded document (``--next-ui`` only)."""

    with _DROP_BIND_LOCK:
        if window.evaluate_js(f"Boolean(window.{_DROP_BOUND_MARKER})") is True:
            return
        if window.dom.get_element("#input-drop-zone") is None:
            # The next UI has no drop target yet; it is bound after navigating to "/".
            log_stage("input_drop_target_absent")
            return
        _bind_input_drop(window)
        window.evaluate_js(f"window.{_DROP_BOUND_MARKER} = true")


def _start_next_ui_window(window: Any) -> None:
    """Start function for ``--next-ui``: rebind native drop after every page load.

    The default legacy startup keeps passing ``_bind_input_drop`` itself to
    ``webview.start`` and is unaffected by this function.
    """

    def on_loaded(window: Any) -> None:
        _bind_input_drop_once(window)

    window.events.loaded += on_loaded
    _bind_input_drop_once(window)


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
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
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


def next_ui_requested(argv: list[str]) -> bool:
    """The experimental UI-2 frontend is shown only for the exact developer flag.

    There is no setting, environment variable or automatic migration that selects it:
    without ``--next-ui`` the window opens the legacy GUI exactly as before.
    """
    return launch_mode(argv) == "next_ui"


V1_DEV_WINDOW_TITLE = "utteran [dev: next-ui-v1 - not a product acceptance mode]"
# Legacy modes keep their original join bound; v1 waits for JobRuntime.shutdown (30 s).
SERVER_JOIN_TIMEOUT = {"legacy": 5.0, "next_ui": 5.0, "next_ui_v1": 45.0}


def acquire_startup_guard(mode: LaunchMode) -> GuiInstanceLock:
    """Validate the data root for ``mode`` and take its GUI instance lock (G-09).

    Runs before any application, store or runtime exists, so a refusal has no side effect
    beyond (for ``--next-ui-v1`` on an empty directory) the fixed development marker.
    """
    if mode == "next_ui_v1":
        root = validate_dev_data_root()
        # Canonical spelling for every later resolution and for child processes, so an
        # alias (symlink/junction) can never name a second, separately locked root.
        os.environ[DATA_ROOT_ENV] = str(root)
        return GuiInstanceLock(root).acquire()
    paths = resolve_data_paths(project_root())
    return GuiInstanceLock(lock_directory(paths.root)).acquire(create_parent=True)


def create_mode_app(mode: LaunchMode, session_key: str, *, repo_root: Path, authority: str) -> Any:
    """Build the FastAPI app for one launch mode. Only ``next_ui_v1`` enables ``/api/v1``."""
    if mode == "next_ui_v1":
        # Same create_app() boundary as the tests: B5 session/Host/Origin middleware and
        # the B7 activate() cutover are reused, never re-implemented or relaxed here.
        return create_app(
            session_key,
            repo_root=repo_root,
            enable_next_ui=True,
            enable_v1_settings=True,
            enable_v1_jobs=True,
            enable_v1_setup=True,
            enable_v1_events=True,
            v1_authority=authority,
        )
    if mode == "next_ui":
        return create_app(session_key, repo_root=repo_root, enable_next_ui=True)
    return create_app(session_key, repo_root=repo_root)


def _v1_runtime(app: Any) -> Any:
    return getattr(getattr(app, "state", None), "v1_jobs_runtime", None)


def shutdown_v1_runtime(app: Any, timeout: float = 30.0) -> str | None:
    """Stop a v1 runtime the server lifespan did not stop; return its fixed result code."""
    runtime = _v1_runtime(app)
    if runtime is None or getattr(runtime, "state", None) != "active":
        return None
    return str(runtime.shutdown(timeout))


def v1_runtime_released(app: Any) -> bool:
    """True only when no v1 runtime exists or its rollback provably completed.

    ``JobRuntime`` leaves ``state == "active"`` whenever deactivate() was refused
    (``active_attempt``, ``ownership_lost``) or failed (``shutdown_failed``), so an
    unproven stop never counts as stopped and the GUI lock is kept until process exit.
    """
    runtime = _v1_runtime(app)
    return runtime is None or getattr(runtime, "state", None) != "active"


def report_startup_refusal(refusal: StartupRefused) -> None:
    """Fixed code and fixed text only: no path, token or argv is ever shown."""
    text = f"utteran-gui: startup refused [{refusal.code}] {refusal.message}"
    with suppress(Exception):
        print(text, file=sys.stderr)
    if os.name == "nt" and getattr(sys, "frozen", False):
        # A packaged GUI has no console; show the same fixed text natively.
        with suppress(Exception):
            import ctypes

            windll: Any = getattr(ctypes, "windll", None)
            if windll is not None:
                windll.user32.MessageBoxW(None, text, "utteran", 0x10)


def main() -> None:
    """Start uvicorn in the background and pywebview on the GUI thread."""
    if len(sys.argv) == 3 and sys.argv[1] == "--diagnose-keyring":
        destination = Path(sys.argv[2]).resolve()
        destination.write_text(
            json.dumps(TokenStore().diagnose(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if len(sys.argv) == 2 and sys.argv[1] == "--delete-keyring-token":
        TokenStore().clear()
        return
    started = time.monotonic()
    mode = launch_mode(sys.argv)
    set_windows_app_user_model_id()
    try:
        # Before logging, the app, the socket and every store: a refused second GUI
        # touches nothing that the running one owns.
        instance_lock = acquire_startup_guard(mode)
    except StartupRefused as refusal:
        report_startup_refusal(refusal)
        raise SystemExit(2) from None
    session = _GuiSession()
    try:
        _run_gui(mode, started, session)
    finally:
        # Released only once the server and any v1 runtime have stopped (also after a
        # startup exception). Otherwise, or on a crash/kill, the OS releases it at exit.
        if session.stopped:
            instance_lock.release()


class _GuiSession:
    """Whether everything this GUI session started has stopped (lock may be released)."""

    stopped = True  # nothing owned yet


def _run_gui(mode: LaunchMode, started: float, session: _GuiSession) -> None:
    """Run one GUI session; ``session.stopped`` tells the caller when it is safe to unlock."""
    logging_status = configure_gui_logging(
        install_dir=project_root(), packaged=bool(getattr(sys, "frozen", False))
    )
    os.environ["UTTERAN_GUI_CHILD_LOG_DIR"] = str(logging_status.log_dir)
    next_ui = mode != "legacy"
    log_stage("gui_boot_start", mode=mode)
    log_stage("gui_instance_lock_acquired")
    if mode == "next_ui_v1":
        # Development-only: start/auth/exclusion/sync safety, not a real-job UI acceptance.
        log_stage("next_ui_v1_dev_mode", level=logging.WARNING, dev_root_marked=True)
    import uvicorn
    import webview

    session_key = secrets.token_urlsafe(32)
    # Bind first: v1 Host/Origin checks need the real authority, and this socket is the
    # one handed to uvicorn (never re-bound), so the port cannot be lost in between.
    server_socket = bind_loopback_socket()
    try:
        host, port = server_socket.getsockname()[:2]
        port = int(port)
        # activate() is create_app()'s last step: when create_app() raises, no v1
        # runtime is running (activate() itself stops what it started on failure).
        app = create_mode_app(
            mode, session_key, repo_root=project_root(), authority=f"{host}:{port}"
        )
    except BaseException:
        server_socket.close()
        raise
    # From here on a v1 runtime may be active: every exit path goes through the
    # finally below, and only a proven stop lets the caller release the lock.
    session.stopped = False
    server: Any = None
    thread: threading.Thread | None = None
    try:
        log_stage("fastapi_app_created", next_ui=next_ui, mode=mode)
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
            raise RuntimeError("GUI server failed to start")
        log_stage(
            "uvicorn_server_started",
            port=port,
            elapsed_seconds=round(time.monotonic() - started, 3),
        )
        url = f"http://127.0.0.1:{port}/launch?session={quote(session_key)}"
        native_api = NativeDialogApi()
        log_stage("webview_window_creating")
        # pywebview's default text-selection blocker injects a nonce-less style.
        # Let the opt-in frontend own selection styles without relaxing its CSP.
        # Omitting the option for legacy startup preserves its existing behavior.
        selection_options = {"text_select": True} if next_ui else {}
        window = webview.create_window(
            V1_DEV_WINDOW_TITLE if mode == "next_ui_v1" else "utteran",
            url=url,
            width=1180,
            height=820,
            min_size=(900, 660),
            js_api=native_api,
            **selection_options,
        )
        native_api._attach_window(window)
        log_stage("webview_window_created", elapsed_seconds=round(time.monotonic() - started, 3))
        icon_path = project_root() / "icon" / "utteran.ico"
        # webview.start() blocks the calling (main) thread until the window
        # closes - this is expected and not itself a hang. Everything after
        # window creation that *looks* like a startup freeze actually
        # happens inside the page the window loaded (see app.js's boot()
        # sequence and /api/environment), which this stage log cannot see;
        # those stages log through utteran_gui.api instead.
        log_stage("webview_start_blocking")
        webview.start(
            _start_next_ui_window if next_ui else _bind_input_drop,
            window,
            icon=str(icon_path) if icon_path.is_file() else None,
        )
        log_stage("webview_closed")
    finally:
        # Order: frontend/uvicorn (its lifespan shutdown stops the v1 runtime) ->
        # any v1 runtime the lifespan did not stop -> the caller releases the lock.
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=SERVER_JOIN_TIMEOUT[mode])
        server_socket.close()
        if thread is not None and thread.is_alive():
            log_stage("gui_shutdown_incomplete", level=logging.ERROR, reason="server_running")
        else:
            # The lifespan normally stopped the runtime; retry briefly only if it did not
            # (server never started, or its rollback was refused).
            code = shutdown_v1_runtime(app, timeout=5.0)
            if code is not None:
                level = logging.INFO if code == "rolled_back" else logging.ERROR
                log_stage("v1_runtime_shutdown", level=level, code=code)
            if v1_runtime_released(app):
                session.stopped = True
                log_stage("gui_shutdown_complete")
            else:
                # active_attempt / ownership_lost / shutdown_failed: the v1 executor may
                # still own work a legacy GUI cannot see, so keep the lock until exit.
                log_stage("gui_shutdown_incomplete", level=logging.ERROR, reason="v1_runtime")


if __name__ == "__main__":
    main()
