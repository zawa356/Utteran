"""Opt-in delivery of the next frontend (UI-2, G-11/G-12).

Nothing here is used unless ``create_app(..., enable_next_ui=True)`` is passed. The legacy
UI (``/``, ``/assets``) and its CSP are untouched. The next UI is a Vite build whose
``index.html`` carries the placeholder :data:`NONCE_PLACEHOLDER`; every HTML response gets a
fresh random nonce that is written into the page *and* the CSP header of the same response,
so the UI kit can inject its theme ``<style nonce=...>`` without ``'unsafe-inline'``.
Only ``style-src`` gains the nonce: ``script-src`` stays ``'self'``.

The page also carries the API mode the *server* enabled (UI-3 S5-A): a static, non-secret
``<meta name="utteran-api-mode">`` whose placeholder :data:`API_MODE_PLACEHOLDER` is replaced by
``legacy`` or ``v1-dev``. The frontend never chooses v1 by itself (URL, storage or input): a
missing, unexpanded or unknown value keeps the legacy client.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from pathlib import Path

NONCE_PLACEHOLDER = "__UTTERAN_CSP_NONCE__"
API_MODE_PLACEHOLDER = "__UTTERAN_API_MODE__"
# ``legacy``: the Vue UI talks to the legacy ``/api/*`` only (``--next-ui``).
# ``v1-dev``: the development-only ``--next-ui-v1`` single-job path to ``/api/v1`` (S5-A).
API_MODES = frozenset({"legacy", "v1-dev"})
NEXT_UI_PREFIX = "/next"
DEFAULT_ASSET_ROOT = Path(__file__).with_name("web-next")
# Vite emits content-hashed file names under assets/, so they may be cached long-term.
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
MAX_INDEX_BYTES = 256 * 1024

LEGACY_CSP = (
    "default-src 'self'; style-src 'self'; script-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
)


def content_security_policy(style_nonce: str | None = None) -> str:
    """The legacy policy, optionally with one per-response nonce added to ``style-src``."""
    if style_nonce is None:
        return LEGACY_CSP
    return LEGACY_CSP.replace("style-src 'self'", f"style-src 'self' 'nonce-{style_nonce}'")


def new_nonce() -> str:
    """128 bits from the OS CSPRNG, URL-safe base64 (valid CSP nonce and HTML attribute)."""
    return secrets.token_urlsafe(16)


class NextUiAssetsError(RuntimeError):
    """The opt-in UI was requested but its build output is missing or malformed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NextUiBundle:
    """A validated build: the index template and the hashed asset directory."""

    root: Path
    template: str

    @property
    def assets(self) -> Path:
        return self.root / "assets"

    def render(self, nonce: str, api_mode: str = "legacy") -> str:
        if api_mode not in API_MODES:
            raise NextUiAssetsError("next_ui_api_mode_invalid")
        page = self.template.replace(NONCE_PLACEHOLDER, nonce).replace(
            API_MODE_PLACEHOLDER, api_mode
        )
        # Defensive: never ship an unexpanded placeholder.
        if NONCE_PLACEHOLDER in page or API_MODE_PLACEHOLDER in page:
            raise NextUiAssetsError("next_ui_template_invalid")
        return page


def load_bundle(root: Path) -> NextUiBundle:
    """Validate the build once at startup; raise a coded error instead of guessing."""
    index = root / "index.html"
    if not index.is_file():
        raise NextUiAssetsError("next_ui_assets_missing")
    if not (root / "assets").is_dir():
        raise NextUiAssetsError("next_ui_assets_missing")
    raw = index.read_bytes()
    if len(raw) > MAX_INDEX_BYTES:
        raise NextUiAssetsError("next_ui_template_invalid")
    try:
        template = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise NextUiAssetsError("next_ui_template_invalid") from None
    # Exactly one placeholder: the <meta name="csp-nonce"> read by the frontend bootstrap.
    if template.count(NONCE_PLACEHOLDER) != 1:
        raise NextUiAssetsError("next_ui_template_invalid")
    # Exactly one API-mode placeholder: the <meta name="utteran-api-mode"> (S5-A).
    if template.count(API_MODE_PLACEHOLDER) != 1:
        raise NextUiAssetsError("next_ui_template_invalid")
    return NextUiBundle(root=root, template=template)


def try_load_bundle(root: Path) -> NextUiBundle | NextUiAssetsError:
    try:
        return load_bundle(root)
    except NextUiAssetsError as exc:
        logging.getLogger(__name__).error("Next UI is enabled but unavailable: %s", exc.code)
        return exc
    except OSError:
        logging.getLogger(__name__).error("Next UI is enabled but its assets are unreadable")
        return NextUiAssetsError("next_ui_assets_unreadable")
