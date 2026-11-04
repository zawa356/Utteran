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

import pytest
from fastapi.testclient import TestClient

from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.app import next_ui_requested
from utteran_gui.next_ui import LEGACY_CSP, NONCE_PLACEHOLDER, load_bundle
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


def _bundle(root: Path, *, placeholders: int = 1) -> Path:
    (root / "assets").mkdir(parents=True)
    meta = f'<meta name="csp-nonce" content="{NONCE_PLACEHOLDER}">' * placeholders
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


@pytest.mark.parametrize("variant", ["missing", "no_placeholder", "two_placeholders"])
def test_missing_or_malformed_build_fails_closed(tmp_path: Path, variant: str) -> None:
    root = tmp_path / "next"
    if variant == "no_placeholder":
        _bundle(root, placeholders=0)
    elif variant == "two_placeholders":
        _bundle(root, placeholders=2)
    client = _app(tmp_path, enable_next_ui=True, next_ui_root=root)
    response = client.get("/next/")
    assert response.status_code == 503
    assert "next_ui_" in response.text
    assert response.headers["cache-control"] == "no-store"
    # No silent fallback: /launch still points to the next UI, never to the legacy page.
    launched = client.get(f"/launch?session={SESSION}", follow_redirects=False)
    assert launched.headers["location"] == "/next/"
    assert client.get("/next/assets/app-1a2b3c.js").status_code == 404


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
