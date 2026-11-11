"""G-09: --next-ui-v1 development startup, per-data-root GUI exclusion, recovery boundary.

Everything runs on synthetic, test-owned directories (``tmp_path``). No real user data
root, portable data, model, GPU or WebView is touched:

- ``webview`` is a fake module whose ``start()`` drives a scenario over *real* loopback
  HTTP against the real uvicorn server that ``main()`` started on its pre-bound socket.
- Cross-process exclusion uses real child Python processes (Linux and Windows CI),
  including a hard kill (SIGKILL / TerminateProcess) to prove the OS reclaims the lock.

Not covered here (later gates): real jobs/ready/smoke (B4), product S3-B2, Windows
WebView2/DPI/IME manual acceptance, real user DB restore (G-15).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import http.client
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

import utteran_gui.app as launcher
from utteran_gui.api import V1_OWNS_SLOT
from utteran_gui.app import next_ui_requested
from utteran_gui.instance_guard import (
    DEV_MARKER_NAME,
    GuiInstanceLock,
    StartupRefused,
    validate_dev_data_root,
)
from utteran_gui.job_runtime import JobRuntime, RuntimeStateError

ROOT = Path(__file__).parents[1]
TRANSCRIPTION = {
    "input_path": "synthetic-input.wav",
    "output_dir": "synthetic-output",
    "profile": "cpu",
    "asr_backend": "faster-whisper",
    "asr_model": "tiny",
    "asr_device": "cpu",
    "diarization_enabled": False,
    "formats": ["txt"],
}


@pytest.fixture(autouse=True)
def _no_distribution_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UTTERAN_DISTRIBUTION", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)


def dev_root(tmp_path: Path, name: str = "dev-root") -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def sqlite_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.sqlite3")) if root.exists() else []


def snapshot_tree(root: Path) -> dict[str, bytes]:
    # A held lock file is not readable on Windows (mandatory byte-range lock), and its
    # content is never written anyway: record only that it exists.
    return {
        str(p.relative_to(root)): (
            b"<lock>"
            if p.name == "gui-instance.lock"
            else p.read_bytes()
            if p.is_file()
            else b"<dir>"
        )
        for p in sorted(root.rglob("*"))
    }


# --------------------------------------------------------------------------- 1. flags


def test_next_ui_flag_semantics_are_unchanged() -> None:
    assert next_ui_requested(["utteran-gui", "--next-ui"]) is True
    assert next_ui_requested(["utteran-gui", "--next-ui-v1"]) is False
    assert next_ui_requested(["utteran-gui"]) is False


# --------------------------------------------------------------------------- 4. processes

HOLDER = r"""
import subprocess, sys
from utteran_gui.app import acquire_startup_guard, create_mode_app
from utteran_gui.instance_guard import StartupRefused
mode, extra = sys.argv[1], sys.argv[2:]
try:
    lock = acquire_startup_guard(mode)
except StartupRefused as exc:
    print("REFUSED", exc.code, flush=True)
    sys.exit(2)
if "child" in extra:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"], close_fds=False
    )
    print("CHILD", child.pid, flush=True)
if "activate" in extra:
    from pathlib import Path
    app = create_mode_app(mode, "s" * 43, repo_root=Path.cwd(), authority="127.0.0.1:8765")
    print("ACTIVE", app.state.v1_jobs_runtime.state, flush=True)
print("LOCKED", flush=True)
sys.stdin.read()
"""


def readline(proc: subprocess.Popen[str], timeout: float = 90.0) -> str:
    assert proc.stdout is not None
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(proc.stdout.readline).result(timeout=timeout).strip()


def spawn(mode: str, root: Path, *extra: str, hold: bool = True) -> subprocess.Popen[str]:
    env = {**os.environ, "UTTERAN_DATA_ROOT": str(root), "PYTHONUTF8": "1"}
    env.pop("UTTERAN_DISTRIBUTION", None)
    return subprocess.Popen(
        [sys.executable, "-c", HOLDER, mode, *extra],
        stdin=subprocess.PIPE if hold else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
        cwd=ROOT,
    )


def finish(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)
    for stream in (proc.stdin, proc.stdout):
        if stream is not None:
            stream.close()


# Windows documents that byte-range locks of a terminated process are released by the OS
# after a delay that "depends upon available system resources"; POSIX releases at once.
RECLAIM_SECONDS = 15.0


def reacquire_after_kill(acquire: Callable[[], Any]) -> Any:
    deadline = time.monotonic() + RECLAIM_SECONDS
    while True:
        try:
            return acquire()
        except StartupRefused as refused:
            if refused.code != "gui_already_running" or time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def spawned_lock(mode: str, root: Path) -> Any:
    proc = spawn(mode, root, hold=False)
    try:
        line = readline(proc)
    finally:
        finish(proc)
    if line != "LOCKED":
        raise StartupRefused(line.split()[-1])  # type: ignore[arg-type]
    return line


@pytest.fixture
def marked_root(tmp_path: Path) -> Path:
    root = dev_root(tmp_path)
    validate_dev_data_root({"UTTERAN_DATA_ROOT": str(root)})
    return root


@pytest.mark.parametrize(
    ("holder", "contender"),
    [
        ("legacy", "next_ui_v1"),
        ("next_ui_v1", "legacy"),
        ("next_ui_v1", "next_ui_v1"),
        ("legacy", "legacy"),
        ("next_ui", "next_ui_v1"),
        ("next_ui_v1", "next_ui"),
    ],
)
def test_same_root_second_gui_process_is_refused(
    marked_root: Path, holder: str, contender: str
) -> None:
    first = spawn(holder, marked_root)
    try:
        assert readline(first) == "LOCKED"
        before = snapshot_tree(marked_root)
        second = spawn(contender, marked_root, hold=False)
        try:
            assert readline(second) == "REFUSED gui_already_running"
            assert second.wait(timeout=60) == 2
        finally:
            finish(second)
        assert snapshot_tree(marked_root) == before  # the refused GUI changed nothing
    finally:
        finish(first)


def test_different_roots_run_side_by_side(tmp_path: Path) -> None:
    a, b = dev_root(tmp_path, "a"), dev_root(tmp_path, "b")
    first = spawn("next_ui_v1", a)
    try:
        assert readline(first) == "LOCKED"
        second = spawn("next_ui_v1", b, hold=False)
        try:
            assert readline(second) == "LOCKED"
            assert second.wait(timeout=60) == 0
        finally:
            finish(second)
    finally:
        finish(first)


def test_hard_kill_releases_the_lock_even_with_a_surviving_child(marked_root: Path) -> None:
    first = spawn("next_ui_v1", marked_root, "child")
    child_pid: int | None = None
    try:
        line = readline(first)
        assert line.startswith("CHILD ")
        child_pid = int(line.split()[1])
        assert readline(first) == "LOCKED"
        first.kill()  # SIGKILL / TerminateProcess: no cleanup code runs
        first.wait(timeout=30)
        # The inherited-handle child is still alive but never holds the lock.
        assert reacquire_after_kill(lambda: spawned_lock("legacy", marked_root)) == "LOCKED"
    finally:
        finish(first)
        if child_pid is not None:
            with contextlib.suppress(OSError):
                os.kill(child_pid, signal.SIGTERM)


def test_killed_v1_gui_restarts_with_dispatch_paused_and_no_second_writer(
    marked_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = spawn("next_ui_v1", marked_root, "activate")
    try:
        assert readline(first) == "ACTIVE active"
        assert readline(first) == "LOCKED"
        first.kill()  # crash while the v1 dispatcher owns the control store
        first.wait(timeout=30)
    finally:
        finish(first)
    store = marked_root / "data" / "control-v1" / "operations.sqlite3"
    assert store.is_file()
    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(marked_root))
    lock = reacquire_after_kill(lambda: launcher.acquire_startup_guard("next_ui_v1"))
    try:
        from fastapi.testclient import TestClient

        app = launcher.create_mode_app(
            "next_ui_v1", "k" * 43, repo_root=ROOT, authority="127.0.0.1:8765"
        )
        with TestClient(
            app, base_url="http://127.0.0.1:8765", headers={"x-utteran-session": "k" * 43}
        ) as client:
            assert client.get("/api/v1/queue").json()["dispatch"] == "paused"
            assert client.post("/api/jobs", json=TRANSCRIPTION).status_code == 409
        # The restart backed the crashed store up before opening it (pre-v1 backup).
        assert list(store.parent.glob("operations-pre-v1-*.sqlite3"))
    finally:
        lock.release()


# --------------------------------------------------------------------------- 5. launcher


class FakeWebview:
    """Stands in for pywebview; ``start()`` runs a scenario against the live server."""

    def __init__(self, scenario: Callable[[SimpleNamespace], None] | None = None) -> None:
        self.scenario = scenario
        self.windows: list[SimpleNamespace] = []
        self.started = False

    def create_window(self, title: str, **kwargs: Any) -> SimpleNamespace:
        window = SimpleNamespace(title=title, **kwargs)
        self.windows.append(window)
        return window

    def start(self, _func: Any, window: SimpleNamespace, icon: Any = None) -> None:
        self.started = True
        if self.scenario is not None:
            self.scenario(window)


@pytest.fixture
def launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[..., list[tuple[str, dict[str, Any]]]]]:
    project = tmp_path / "project"
    project.mkdir()
    stages: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(launcher, "project_root", lambda: project)
    monkeypatch.setattr(launcher, "set_windows_app_user_model_id", lambda: True)
    monkeypatch.setattr(
        launcher, "configure_gui_logging", lambda **_: SimpleNamespace(log_dir=tmp_path / "logs")
    )
    monkeypatch.setattr(launcher, "log_stage", lambda stage, **f: stages.append((stage, f)))
    monkeypatch.setenv("UTTERAN_GUI_CHILD_LOG_DIR", str(tmp_path / "logs"))

    def run(mode_args: list[str], root: Path, webview: FakeWebview) -> list[tuple[str, Any]]:
        monkeypatch.setenv("UTTERAN_DATA_ROOT", str(root))
        monkeypatch.setitem(sys.modules, "webview", webview)
        monkeypatch.setattr(sys, "argv", ["utteran-gui", *mode_args])
        launcher.main()
        return stages

    yield run


def loopback(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
    read: bool = True,
) -> tuple[int, dict[str, str], str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        selected = dict(headers or {})
        connection.putrequest(method, path, skip_host="Host" in selected)
        payload = None if body is None else json.dumps(body).encode()
        if payload is not None:
            selected.setdefault("Content-Type", "application/json")
            selected["Content-Length"] = str(len(payload))
        for key, value in selected.items():
            connection.putheader(key, value)
        connection.endheaders(payload)
        response = connection.getresponse()
        text = response.read().decode() if read else ""
        return response.status, {k.lower(): v for k, v in response.getheaders()}, text
    finally:
        connection.close()


def session_of(window: SimpleNamespace) -> tuple[int, str]:
    parsed = urlsplit(window.url)
    assert parsed.hostname == "127.0.0.1" and parsed.path == "/launch"
    return int(parsed.port or 0), parse_qs(parsed.query)["session"][0]


def test_v1_dev_launch_end_to_end_over_real_loopback(
    launch: Callable[..., Any], marked_root: Path
) -> None:
    seen: dict[str, Any] = {}
    transcript: list[str] = []

    def scenario(window: SimpleNamespace) -> None:
        port, session = session_of(window)
        authority = f"127.0.0.1:{port}"
        origin = "http://" + authority

        def call(method: str, path: str, **kw: Any) -> tuple[int, dict[str, str], str]:
            result = loopback(port, method, path, **kw)
            transcript.append(result[2])
            return result

        # /launch issues the cookie and redirects to the next UI.
        status, headers, _ = call("GET", f"/launch?session={session}")
        assert status == 303 and headers["location"] == "/next/"
        cookie = headers["set-cookie"].split(";", 1)[0]
        assert "httponly" in headers["set-cookie"].lower()
        jar = {"Cookie": cookie}
        # Correct Host (the real bound authority) + session -> v1 reads succeed.
        for path in (
            "/api/v1/jobs",
            "/api/v1/queue",
            "/api/v1/setup-operations",
            "/api/v1/sync",
            "/api/v1/presets",
        ):
            assert call("GET", path, headers=jar)[0] == 200, path
            assert call("GET", path)[0] == 401, path  # no session
            wrong_host = {**jar, "Host": f"localhost:{port}"}
            assert call("GET", path, headers=wrong_host)[0] == 403, path
            wrong_origin = {**jar, "Origin": "http://127.0.0.1:1"}
            assert call("GET", path, headers=wrong_origin)[0] == 403, path
        queue = json.loads(call("GET", "/api/v1/queue", headers=jar)[2])
        assert queue["dispatch"] == "paused" and queue["active_item_id"] is None
        # CSRF rule: a cookie-only unsafe request needs Origin or the session header.
        status, _, text = call("POST", "/api/v1/presets", headers=jar, body={})
        assert status == 403 and json.loads(text)["error"]["code"] == "origin_required"
        for accepted in ({**jar, "Origin": origin}, {"x-utteran-session": session}):
            status, _, text = call("POST", "/api/v1/presets", headers=accepted, body={})
            assert status not in (401, 403), text  # past the guard (body is invalid)
        # SSE rides the same cookie; only the response head is read here.
        status, headers, _ = loopback(port, "GET", "/api/v1/events", headers=jar, read=False)
        assert status == 200 and headers["content-type"].startswith("text/event-stream")
        assert call("GET", "/api/v1/events")[0] == 401
        # Legacy pages and reads stay reachable; new legacy work is refused (409).
        assert call("GET", "/", headers=jar)[0] == 200
        assert call("GET", "/api/queue", headers=jar)[0] == 200
        for path, body in (
            ("/api/jobs", TRANSCRIPTION),
            ("/api/wizard/jobs", {"kind": "venv_build", "profile": "cpu"}),
            (
                "/api/models/actions",
                {"profile": "cpu", "action": "download", "model_ref": "synthetic:model"},
            ),
        ):
            status, _, text = call("POST", path, headers=jar, body=body)
            assert status == 409 and json.loads(text)["detail"] == V1_OWNS_SLOT, path
        # The lock is held for the whole session.
        with pytest.raises(StartupRefused, match="gui_already_running"):
            GuiInstanceLock(marked_root).acquire()
        seen["session"] = session
        seen["title"] = window.title
        seen["text_select"] = window.text_select

    webview = FakeWebview(scenario)
    stages = launch(["--next-ui-v1"], marked_root, webview)
    assert webview.started and "session" in seen
    assert "dev" in seen["title"] and "not a product acceptance mode" in seen["title"]
    assert seen["text_select"] is True
    # No private value (session key) in any response body.
    assert not any(seen["session"] in text for text in transcript)
    names = [name for name, _ in stages]
    assert names.index("gui_instance_lock_acquired") < names.index("fastapi_app_created")
    assert "next_ui_v1_dev_mode" in names and names[-1] == "gui_shutdown_complete"
    store = marked_root / "data" / "control-v1" / "operations.sqlite3"
    assert store.is_file() and list(store.parent.glob("operations-rollback-*.sqlite3"))
    with sqlite3.connect(store) as db:
        assert int(db.execute("PRAGMA user_version").fetchone()[0]) == 6
    with GuiInstanceLock(marked_root):  # released after shutdown
        pass
    assert not any(t.name == "utteran-v1-dispatch" for t in threading.enumerate())


@pytest.mark.parametrize("mode_args", [[], ["--next-ui"]])
def test_legacy_modes_lock_the_root_but_never_enable_v1(
    launch: Callable[..., Any], tmp_path: Path, mode_args: list[str]
) -> None:
    root = tmp_path / "plain-root"  # a legacy root needs no marker and may not exist yet
    checks: dict[str, Any] = {}

    def scenario(window: SimpleNamespace) -> None:
        port, session = session_of(window)
        _, headers, _ = loopback(port, "GET", f"/launch?session={session}")
        checks["location"] = headers["location"]
        jar = {"Cookie": headers["set-cookie"].split(";", 1)[0]}
        checks["v1"] = [
            loopback(port, "GET", p, headers=jar)[0]
            for p in ("/api/v1/queue", "/api/v1/jobs", "/api/v1/events")
        ]
        checks["queue"] = loopback(port, "GET", "/api/queue", headers=jar)[0]
        checks["csp"] = loopback(port, "GET", "/", headers=jar)[1]["content-security-policy"]
        with pytest.raises(StartupRefused, match="gui_already_running"):
            GuiInstanceLock(root).acquire()
        checks["title"] = window.title

    launch(mode_args, root, FakeWebview(scenario))
    assert checks["v1"] == [404, 404, 404]
    assert checks["queue"] == 200
    assert checks["location"] == ("/next/" if mode_args else "/")
    assert checks["title"] == "utteran"
    assert "unsafe-inline" not in checks["csp"].split("script-src", 1)[1].split(";", 1)[0]
    assert sqlite_files(root) == []  # no v1 DB side effect
    assert not (root / DEV_MARKER_NAME).exists()  # legacy modes never write the marker
    with GuiInstanceLock(root):
        pass


def test_refused_start_creates_no_app_store_or_log(
    launch: Callable[..., Any], marked_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = Mock()
    logging_setup = Mock()
    monkeypatch.setattr(launcher, "create_mode_app", create)
    monkeypatch.setattr(launcher, "configure_gui_logging", logging_setup)
    webview = FakeWebview()
    with GuiInstanceLock(marked_root):
        before = snapshot_tree(marked_root)
        for mode_args in ([], ["--next-ui"], ["--next-ui-v1"]):
            with pytest.raises(SystemExit) as exited:
                launch(mode_args, marked_root, webview)
            assert exited.value.code == 2
        assert snapshot_tree(marked_root) == before
    create.assert_not_called()
    logging_setup.assert_not_called()
    assert webview.windows == [] and sqlite_files(marked_root) == []


def test_invalid_dev_root_refuses_before_anything_and_prints_only_a_code(
    launch: Callable[..., Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = dev_root(tmp_path, "existing-data")
    (root / "config").mkdir()
    (root / "config" / "gui-settings.json").write_text("{}", encoding="utf-8")
    before = snapshot_tree(root)
    with pytest.raises(SystemExit):
        launch(["--next-ui-v1"], root, FakeWebview())
    assert snapshot_tree(root) == before
    err = capsys.readouterr().err
    assert "[dev_root_not_marked]" in err and str(root) not in err and str(tmp_path) not in err


# --------------------------------------------------------------------------- 6. failures


def assert_released(root: Path) -> None:
    with GuiInstanceLock(root):
        pass


def test_socket_bind_failure_releases_the_lock(
    launch: Callable[..., Any], marked_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create = Mock()
    monkeypatch.setattr(launcher, "create_mode_app", create)

    def fail() -> Any:
        raise OSError("synthetic bind failure")

    monkeypatch.setattr(launcher, "bind_loopback_socket", fail)
    with pytest.raises(OSError, match="synthetic bind failure"):
        launch(["--next-ui-v1"], marked_root, FakeWebview())
    create.assert_not_called()
    assert sqlite_files(marked_root) == []
    assert_released(marked_root)


def test_app_construction_failure_closes_the_socket_and_releases(
    launch: Callable[..., Any], marked_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sockets: list[Any] = []
    real_bind = launcher.bind_loopback_socket

    def bind() -> Any:
        sockets.append(real_bind())
        return sockets[-1]

    monkeypatch.setattr(launcher, "bind_loopback_socket", bind)
    monkeypatch.setattr(launcher, "create_app", Mock(side_effect=RuntimeError("synthetic")))
    with pytest.raises(RuntimeError, match="synthetic"):
        launch(["--next-ui-v1"], marked_root, FakeWebview())
    assert sockets and sockets[0].fileno() == -1  # closed
    assert_released(marked_root)


def test_late_create_app_failure_stops_the_activated_runtime(
    marked_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import utteran_gui.events_api as events_api

    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(marked_root))
    runtimes: list[JobRuntime] = []
    real_activate = JobRuntime.activate

    def activate(self: JobRuntime) -> Any:
        runtimes.append(self)
        return real_activate(self)

    monkeypatch.setattr(JobRuntime, "activate", activate)
    monkeypatch.setattr(
        events_api, "register_event_routes", Mock(side_effect=RuntimeError("synthetic"))
    )
    with pytest.raises(RuntimeError, match="synthetic"):
        launcher.create_mode_app("next_ui_v1", "k" * 43, repo_root=ROOT, authority="127.0.0.1:1")
    assert runtimes and runtimes[0].state == "inactive"
    assert not runtimes[0].legacy_queue.closed  # safe rollback reopened the legacy queue
    assert not any(t.name == "utteran-v1-dispatch" for t in threading.enumerate())


def test_unreadable_control_store_fails_closed_without_losing_bytes(
    launch: Callable[..., Any], marked_root: Path
) -> None:
    store = marked_root / "data" / "control-v1" / "operations.sqlite3"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"synthetic, not a sqlite database" * 64)
    before = store.read_bytes()
    with pytest.raises(Exception):  # noqa: B017 - any refusal; the point is no damage
        launch(["--next-ui-v1"], marked_root, FakeWebview())
    assert store.read_bytes() == before
    assert_released(marked_root)


@pytest.mark.parametrize("failure", ["create_window", "keyboard_interrupt"])
def test_webview_failure_or_ctrl_c_stops_server_runtime_then_lock(
    launch: Callable[..., Any], marked_root: Path, failure: str
) -> None:
    webview = FakeWebview()
    if failure == "create_window":
        webview.create_window = Mock(side_effect=RuntimeError("synthetic webview"))  # type: ignore[method-assign]
        expected: type[BaseException] = RuntimeError
    else:

        def interrupt(_window: SimpleNamespace) -> None:
            raise KeyboardInterrupt

        webview.scenario = interrupt
        expected = KeyboardInterrupt
    with pytest.raises(expected):
        launch(["--next-ui-v1"], marked_root, webview)
    store = marked_root / "data" / "control-v1"
    assert list(store.glob("operations-rollback-*.sqlite3"))  # safe rollback ran
    assert not any(t.name == "utteran-v1-dispatch" for t in threading.enumerate())
    assert_released(marked_root)


class FakeServer:
    def __init__(self, started: bool, block: threading.Event | None = None) -> None:
        self.started, self.should_exit, self.block = started, False, block

    def run(self, sockets: Any = None) -> None:
        if self.block is not None:
            self.block.wait(30)


def test_server_start_failure_stops_the_runtime_the_lifespan_never_saw(
    launch: Callable[..., Any], marked_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = SimpleNamespace(Config=Mock(), Server=lambda _config: FakeServer(started=False))
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    runtimes: list[Any] = []
    real = launcher.create_mode_app

    def create(*args: Any, **kwargs: Any) -> Any:
        app = real(*args, **kwargs)
        runtimes.append(app.state.v1_jobs_runtime)
        return app

    monkeypatch.setattr(launcher, "create_mode_app", create)
    with pytest.raises(RuntimeError, match="GUI server failed to start"):
        launch(["--next-ui-v1"], marked_root, FakeWebview())
    assert runtimes[0].state == "inactive"
    assert_released(marked_root)


def test_server_that_does_not_stop_keeps_the_lock_until_process_exit(
    launch: Callable[..., Any], marked_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    block = threading.Event()
    fake = SimpleNamespace(Config=Mock(), Server=lambda _config: FakeServer(True, block))
    monkeypatch.setitem(sys.modules, "uvicorn", fake)
    monkeypatch.setattr(launcher, "create_mode_app", Mock(return_value=SimpleNamespace()))
    monkeypatch.setitem(launcher.SERVER_JOIN_TIMEOUT, "next_ui_v1", 0.05)
    try:
        stages = launch(["--next-ui-v1"], marked_root, FakeWebview())
        assert ("gui_shutdown_incomplete", {"level": 40}) in stages
        with pytest.raises(StartupRefused, match="gui_already_running"):
            GuiInstanceLock(marked_root).acquire()  # not released early
    finally:
        block.set()


# --------------------------------------------------------------------------- 7. shutdown codes


def bare_runtime() -> JobRuntime:
    return JobRuntime(
        store_path=Path("unused.sqlite3"),
        settings_path=Path("unused-settings.sqlite3"),
        legacy_queue=Mock(),
        provider=Mock(),
        runner=Mock(),
        job_dir=Path("unused-jobs"),
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RuntimeStateError("not_active"), "not_active"),
        (RuntimeStateError("active_attempt"), "active_attempt"),
        (RuntimeStateError("ownership_lost"), "ownership_lost"),
        (OSError("synthetic backup failure"), "shutdown_failed"),
        (None, "rolled_back"),
    ],
)
def test_shutdown_reports_refusals_instead_of_pretending_success(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: BaseException | None,
    code: str,
) -> None:
    runtime = bare_runtime()

    def deactivate(timeout: float = 30.0) -> Path:
        if error is not None:
            raise error
        return Path("backup")

    monkeypatch.setattr(runtime, "deactivate", deactivate)
    with caplog.at_level("ERROR"):
        assert runtime.shutdown() == code
    logged = [r for r in caplog.records if r.getMessage() == "v1_runtime_shutdown_refused"]
    assert bool(logged) is (code not in {"rolled_back", "not_active"})
    for record in logged:
        assert "synthetic" not in json.dumps(record.gui_fields)  # type: ignore[attr-defined]
