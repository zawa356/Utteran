"""UI-2 (G-11/G-12): opt-in next UI delivery, per-response CSP nonce, legacy unchanged.

The bundle here is synthetic (tmp_path); the real Vite build is exercised by the
``ui2-frontend`` CI job (Playwright through ``/launch`` and the isolated-wheel check).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.app import next_ui_requested
from utteran_gui.next_ui import (
    API_MODE_PLACEHOLDER,
    LEGACY_CSP,
    NONCE_PLACEHOLDER,
    NextUiAssetsError,
    load_bundle,
)
from utteran_gui.settings import SettingsStore, TokenStore

ROOT = Path(__file__).parents[1]
SESSION = "synthetic-ui2-session"
LEGACY_POLICY = (
    "default-src 'self'; style-src 'self'; script-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
)
NONCE_RE = re.compile(r"style-src 'self' 'nonce-([A-Za-z0-9_-]{22})';")


class _Keyring:
    def get_password(self, _s: str, _u: str) -> str | None:
        return None

    def set_password(self, _s: str, _u: str, _p: str) -> None:
        return None

    def delete_password(self, _s: str, _u: str) -> None:
        return None


def _bundle(root: Path, *, placeholders: int = 1, mode_placeholders: int = 1) -> Path:
    (root / "assets").mkdir(parents=True)
    meta = f'<meta name="csp-nonce" content="{NONCE_PLACEHOLDER}">' * placeholders
    meta += f'<meta name="utteran-api-mode" content="{API_MODE_PLACEHOLDER}">' * mode_placeholders
    (root / "index.html").write_text(
        "<!doctype html><html><head>"
        + meta
        + '<script type="module" src="./assets/app-1a2b3c.js"></script>'
        + '<link rel="stylesheet" href="./assets/app-1a2b3c.css"></head>'
        + '<body><div id="app"></div></body></html>',
        encoding="utf-8",
    )
    (root / "assets" / "app-1a2b3c.js").write_text("export {};\n", encoding="utf-8")
    (root / "assets" / "app-1a2b3c.css").write_text("body{}\n", encoding="utf-8")
    return root


def _app(tmp_path: Path, **kwargs: object) -> TestClient:
    app = create_app(
        SESSION,
        repo_root=tmp_path,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        token_store=TokenStore(_Keyring()),
        **kwargs,  # type: ignore[arg-type]
    )
    return TestClient(app)


def test_legacy_policy_constant_is_unchanged() -> None:
    assert LEGACY_CSP == LEGACY_POLICY


def test_default_app_does_not_expose_next_ui(tmp_path: Path) -> None:
    client = _app(tmp_path, next_ui_root=_bundle(tmp_path / "next"))
    for path in ("/next", "/next/", "/next/assets/app-1a2b3c.js"):
        assert client.get(path, follow_redirects=False).status_code == 404, path
    launched = client.get(f"/launch?session={SESSION}", follow_redirects=False)
    assert launched.status_code == 303
    assert launched.headers["location"] == "/"
    index = client.get("/")
    assert index.status_code == 200
    assert index.headers["content-security-policy"] == LEGACY_POLICY
    assert "nonce" not in index.text


def test_launch_cookie_then_next_ui_html_assets_and_api_session(tmp_path: Path) -> None:
    client = _app(tmp_path, enable_next_ui=True, next_ui_root=_bundle(tmp_path / "next"))
    assert client.get("/launch?session=wrong", follow_redirects=False).status_code == 401
    launched = client.get(f"/launch?session={SESSION}", follow_redirects=False)
    assert launched.status_code == 303
    assert launched.headers["location"] == "/next/"
    cookie = launched.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie

    page = client.get("/next/")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["x-content-type-options"] == "nosniff"
    policy = page.headers["content-security-policy"]
    match = NONCE_RE.search(policy)
    assert match is not None, policy
    nonce = match.group(1)
    assert f'content="{nonce}"' in page.text
    assert NONCE_PLACEHOLDER not in page.text and NONCE_PLACEHOLDER not in policy
    # S5-A: --next-ui (no v1 mounted) always tells the frontend "legacy".
    assert API_MODE_PLACEHOLDER not in page.text
    assert '<meta name="utteran-api-mode" content="legacy">' in page.text
    assert "v1-dev" not in page.text
    # Only style-src changes; scripts, connections and framing stay as strict as legacy.
    assert policy == LEGACY_POLICY.replace("style-src 'self'", f"style-src 'self' 'nonce-{nonce}'")
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert "script-src 'self';" in policy

    # A fresh nonce on every request.
    second = client.get("/next/").headers["content-security-policy"]
    assert NONCE_RE.search(second).group(1) != nonce  # type: ignore[union-attr]

    script = client.get("/next/assets/app-1a2b3c.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert script.headers["content-security-policy"] == LEGACY_POLICY
    css = client.get("/next/assets/app-1a2b3c.css")
    assert css.headers["content-type"].startswith("text/css")
    missing = client.get("/next/assets/absent.js")
    assert missing.status_code == 404
    assert "immutable" not in missing.headers.get("cache-control", "")

    # The same cookie authorizes the legacy API the new UI uses; nothing else is opened.
    assert client.get("/api/settings").status_code == 200
    assert client.get("/api/v1/sync").status_code == 404
    assert client.get("/next", follow_redirects=False).headers["location"] == "/next/"
    # The legacy page stays reachable with the legacy policy.
    assert client.get("/").headers["content-security-policy"] == LEGACY_POLICY


def test_next_ui_still_requires_session_for_api(tmp_path: Path) -> None:
    client = _app(tmp_path, enable_next_ui=True, next_ui_root=_bundle(tmp_path / "next"))
    assert client.get("/next/").status_code == 200  # HTML holds no secret, like "/"
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers={SESSION_HEADER: SESSION}).status_code == 200


def test_assets_path_traversal_is_refused(tmp_path: Path) -> None:
    root = _bundle(tmp_path / "next")
    (tmp_path / "secret.txt").write_text("synthetic", encoding="utf-8")
    client = _app(tmp_path, enable_next_ui=True, next_ui_root=root)
    for path in ("/next/assets/../index.html", "/next/assets/%2e%2e/%2e%2e/secret.txt"):
        response = client.get(path)
        assert response.status_code == 404 or "synthetic" not in response.text, path


@pytest.mark.parametrize(
    "variant",
    [
        "missing",
        "no_placeholder",
        "two_placeholders",
        "no_mode_placeholder",
        "two_mode_placeholders",
    ],
)
def test_missing_or_malformed_build_fails_closed(tmp_path: Path, variant: str) -> None:
    root = tmp_path / "next"
    if variant == "no_placeholder":
        _bundle(root, placeholders=0)
    elif variant == "two_placeholders":
        _bundle(root, placeholders=2)
    elif variant == "no_mode_placeholder":
        _bundle(root, mode_placeholders=0)
    elif variant == "two_mode_placeholders":
        _bundle(root, mode_placeholders=2)
    client = _app(tmp_path, enable_next_ui=True, next_ui_root=root)
    response = client.get("/next/")
    assert response.status_code == 503
    assert "next_ui_" in response.text
    assert response.headers["cache-control"] == "no-store"
    # No silent fallback: /launch still points to the next UI, never to the legacy page.
    launched = client.get(f"/launch?session={SESSION}", follow_redirects=False)
    assert launched.headers["location"] == "/next/"
    assert client.get("/next/assets/app-1a2b3c.js").status_code == 404


def test_render_refuses_an_unknown_api_mode(tmp_path: Path) -> None:
    bundle = load_bundle(_bundle(tmp_path / "next"))
    assert 'content="v1-dev"' in bundle.render("n" * 22, "v1-dev")
    for bad in ("v1", "V1-DEV", "", "legacy ", "v1-dev\n"):
        with pytest.raises(NextUiAssetsError, match="next_ui_api_mode_invalid"):
            bundle.render("n" * 22, bad)


def test_next_ui_api_mode_follows_the_mounted_v1_routes_only(tmp_path: Path) -> None:
    """Only an app with the v1 settings *and* jobs routes says v1-dev (the --next-ui-v1 shape)."""
    from test_b7_api import AUTHORITY, runtime_for  # synthetic B7 runtime, no real model
    from test_b7_jobs import Env
    from utteran_gui.operation_queue import OperationQueue

    (tmp_path / "v1").mkdir()
    env = Env(tmp_path / "v1", None)
    queue = OperationQueue()
    root = _bundle(tmp_path / "next")
    settings_only = _app(
        tmp_path,
        enable_next_ui=True,
        next_ui_root=root,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=env.settings_path,
    )
    assert 'content="legacy"' in settings_only.get("/next/").text
    app = create_app(
        SESSION,
        repo_root=tmp_path,
        settings_store=SettingsStore(tmp_path / "settings.json"),
        token_store=TokenStore(_Keyring()),
        operation_queue=queue,
        enable_next_ui=True,
        next_ui_root=root,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=env.settings_path,
        enable_v1_jobs=True,
        v1_jobs_runtime=runtime_for(env, queue),
    )
    with TestClient(app, base_url="http://" + AUTHORITY) as client:
        page = client.get("/next/")
        assert page.status_code == 200
        assert '<meta name="utteran-api-mode" content="v1-dev">' in page.text
        # The mode is a fixed word: no session key, path or settings in the page.
        assert SESSION not in page.text and str(tmp_path) not in page.text
        # A query string cannot change it.
        assert 'content="v1-dev"' in client.get("/next/?api=legacy").text
    plain = _app(tmp_path, enable_next_ui=True, next_ui_root=root)
    assert 'content="legacy"' in plain.get("/next/?api=v1-dev").text


def test_load_bundle_rejects_non_utf8(tmp_path: Path) -> None:
    root = _bundle(tmp_path / "next")
    (root / "index.html").write_bytes(b"\xff\xfe" + NONCE_PLACEHOLDER.encode())
    with pytest.raises(Exception, match="next_ui_template_invalid"):
        load_bundle(root)


def test_next_ui_flag_is_exact() -> None:
    assert next_ui_requested(["utteran-gui", "--next-ui"]) is True
    assert next_ui_requested(["utteran-gui"]) is False
    assert next_ui_requested(["utteran-gui", "--next-ui", "extra"]) is False
    assert next_ui_requested(["utteran-gui", "--next"]) is False


def test_next_ui_opt_in_imports_no_v1_or_inference_modules(tmp_path: Path) -> None:
    root = _bundle(tmp_path / "next")
    script = """
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from utteran_gui.api import create_app, SESSION_HEADER
app = create_app('synthetic-session', repo_root=Path(sys.argv[1]), enable_next_ui=True,
                 next_ui_root=Path(sys.argv[2]))
with TestClient(app, headers={SESSION_HEADER: 'synthetic-session'}) as client:
    assert client.get('/next/').status_code == 200
    assert client.get('/api/v1/sync').status_code == 404
banned = ('utteran_control', 'utteran_settings', 'torch', 'faster_whisper', 'pyannote')
loaded = [n for n in sys.modules if n.split('.')[0] in banned]
assert not loaded, loaded
"""
    env = {**os.environ, "UTTERAN_DATA_ROOT": str(tmp_path / "data")}
    subprocess.run(
        [sys.executable, "-c", script, str(ROOT), str(root)],
        check=True,
        capture_output=True,
        env=env,
    )
    assert not (tmp_path / "data").exists() or not list((tmp_path / "data").rglob("*.sqlite3"))


BUILT = ROOT / "src" / "utteran_gui" / "web-next"


# The ui2-frontend CI job sets UTTERAN_UI2_REQUIRE_BUILD=1 after `npm run build`, which
# turns an absent build into a failure there; Python-only jobs have no Node build step.
@pytest.mark.skipif(
    not (BUILT / "index.html").is_file() and os.environ.get("UTTERAN_UI2_REQUIRE_BUILD") != "1",
    reason="frontend build output absent (built and checked by the ui2-frontend CI job)",
)
def test_real_build_output_is_servable(tmp_path: Path) -> None:
    client = _app(tmp_path, enable_next_ui=True)
    page = client.get("/next/")
    assert page.status_code == 200
    nonce = NONCE_RE.search(page.headers["content-security-policy"]).group(1)  # type: ignore[union-attr]
    assert f'content="{nonce}"' in page.text
    references = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', page.text)
    assert references, "index.html must reference hashed assets relative to /next/"
    for reference in references:
        assert client.get(f"/next/{reference}").status_code == 200, reference
    # No external origin anywhere in the entry page (no CDN, no remote fonts).
    assert not re.search(r"""(?:src|href)=["']?(?:https?:)?//""", page.text)
    # S5-A: the real build carries exactly one API-mode tag, "legacy" for --next-ui.
    assert page.text.count('name="utteran-api-mode"') == 1
    assert 'name="utteran-api-mode" content="legacy"' in page.text


# ------------------------------------------------- PR #36 review P2: native drop after navigation


class _Handlers(list[Any]):
    def __iadd__(self, item: Any) -> _Handlers:  # type: ignore[override,misc]
        self.append(item)
        return self


class _Element:
    def __init__(self) -> None:
        self.events = SimpleNamespace(dragenter=_Handlers(), dragover=_Handlers(), drop=_Handlers())


class _Window:
    """Fake pywebview window: each navigation is a new document with fresh JS globals."""

    def __init__(self) -> None:
        self.events = SimpleNamespace(loaded=_Handlers())
        self.documents: list[dict[str, Any]] = []
        self.navigate(has_target=False)  # --next-ui opens /next/ first

    def navigate(self, *, has_target: bool) -> None:
        self.documents.append(
            {"globals": {}, "target": _Element() if has_target else None, "dispatched": []}
        )

    @property
    def doc(self) -> dict[str, Any]:
        return self.documents[-1]

    @property
    def dom(self) -> SimpleNamespace:
        return SimpleNamespace(get_element=lambda selector: self.doc["target"])

    def fire_loaded(self) -> None:
        for handler in list(self.events.loaded):
            handler(self)

    def evaluate_js(self, script: str) -> Any:
        marker = "__utteranInputDropBound"
        if script == f"Boolean(window.{marker})":
            return bool(self.doc["globals"].get(marker))
        if script == f"window.{marker} = true":
            self.doc["globals"][marker] = True
            return True
        self.doc["dispatched"].append(script)
        return None


def _bound_count(window: _Window, index: int) -> int:
    target = window.documents[index]["target"]
    return 0 if target is None else len(target.events.drop)


def test_next_ui_rebinds_native_drop_after_navigating_to_the_legacy_ui() -> None:
    pytest.importorskip("webview")
    from utteran_gui.app import _start_next_ui_window

    window = _Window()
    _start_next_ui_window(window)  # /next/: no drop target, nothing bound
    assert len(window.events.loaded) == 1
    window.fire_loaded()  # the initial load event may also arrive: still nothing to bind

    window.navigate(has_target=True)  # "旧画面を開く" -> /
    window.fire_loaded()
    window.fire_loaded()  # a duplicate loaded event must not add a second handler
    assert _bound_count(window, 1) == 1
    assert len(window.doc["target"].events.dragover) == 1

    # A drop on the legacy page reaches the input field through the existing event.
    window.doc["target"].events.drop[0].callback(
        {"dataTransfer": {"files": [{"pywebviewFullPath": ""}]}}
    )
    assert any("utteran-input-dropped" in script for script in window.doc["dispatched"])

    window.navigate(has_target=False)  # back to /next/
    window.fire_loaded()
    window.navigate(has_target=True)  # and to / again: a new document, bound once
    window.fire_loaded()
    assert _bound_count(window, 3) == 1
    assert _bound_count(window, 1) == 1  # the old document was not touched again


def test_default_startup_keeps_the_original_start_function() -> None:
    source = (ROOT / "src" / "utteran_gui" / "app.py").read_text(encoding="utf-8")
    assert "_start_next_ui_window if next_ui else _bind_input_drop" in source


@pytest.mark.parametrize("next_ui", [False, True])
def test_native_startup_avoids_host_inline_style_only_for_next_ui(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, next_ui: bool
) -> None:
    """Exercise the launcher boundary: host CSS must not bypass the strict CSP."""
    from unittest.mock import Mock

    import utteran_gui.app as launcher

    captured: dict[str, Any] = {}
    window = SimpleNamespace()

    def create_window(_title: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return window

    webview = SimpleNamespace(create_window=create_window, start=Mock())
    server = SimpleNamespace(started=True, run=Mock(), should_exit=False)
    server_socket = Mock()
    server_socket.getsockname.return_value = ("127.0.0.1", 12345)
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(
        sys.modules, "uvicorn", SimpleNamespace(Config=Mock(), Server=lambda _: server)
    )
    # A finished server thread: the launcher then stops everything and releases the lock.
    thread = Mock()
    thread.is_alive.return_value = False
    monkeypatch.setattr(launcher, "threading", SimpleNamespace(Thread=Mock(return_value=thread)))
    # G-09: every GUI mode takes the per-data-root instance lock; keep it in tmp_path,
    # never in the real default user data directory.
    monkeypatch.setenv("UTTERAN_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setattr(launcher, "bind_loopback_socket", lambda: server_socket)
    monkeypatch.setattr(launcher, "project_root", lambda: tmp_path)
    monkeypatch.setattr(launcher, "set_windows_app_user_model_id", lambda: True)
    monkeypatch.setattr(
        launcher, "configure_gui_logging", lambda **_: SimpleNamespace(log_dir=tmp_path)
    )
    monkeypatch.setattr(launcher, "create_app", Mock())
    monkeypatch.setattr(launcher, "log_stage", Mock())
    monkeypatch.setattr(sys, "argv", ["utteran-gui"] + (["--next-ui"] if next_ui else []))
    # main sets this for child processes; restore it after exercising the launcher.
    monkeypatch.setenv("UTTERAN_GUI_CHILD_LOG_DIR", str(tmp_path))

    launcher.main()

    if next_ui:
        assert captured["text_select"] is True
        expected_start = launcher._start_next_ui_window
    else:
        assert "text_select" not in captured
        expected_start = launcher._bind_input_drop
    assert captured["min_size"] == (900, 660)
    assert webview.start.call_args.args == (expected_start, window)
    app_kwargs = launcher.create_app.call_args.kwargs  # type: ignore[attr-defined]
    assert app_kwargs == {"repo_root": tmp_path, **({"enable_next_ui": True} if next_ui else {})}
