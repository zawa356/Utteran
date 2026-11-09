"""Serve the *real* ``create_app(enable_next_ui=True)`` with synthetic services (UI-2).

Used by the Playwright E2E (``frontend/playwright.config.ts``) and for manual browser checks.
Only the environment probe, the keyring and the log-location probe are replaced by synthetic
stand-ins: routing, ``/launch`` cookie, session check, CSP nonce, asset serving,
``/api/settings``, ``/api/token`` (through the real ``TokenStore``), ``/api/wizard/status`` and
``/api/open-folder`` are the production code paths. Nothing here is imported by the product.

    uv run python tools/ui2/serve_next_ui.py --port 8791

prints the launch URL. Settings are written to a temporary directory that is removed on exit.
No real profile, model, path or token is read or shown.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from utteran_gui import logging_runtime  # noqa: E402
from utteran_gui.api import create_app  # noqa: E402
from utteran_gui.settings import SettingsStore, TokenStore  # noqa: E402

SESSION = "ui2-e2e-synthetic-session-key"
# Seconds each synthetic profile takes to answer, so ordering races are deterministic.
DELAYS = {"cuda": 1.5, "intel": 0.2}


# A token value containing REJECT_MARKER makes the synthetic keyring fail like a broken OS
# credential store before anything is written. READBACK_MARKER stores the value but fails the
# read-back that ``TokenStore.set()`` performs right after writing, so the real
# ``PUT /api/token`` answers 503 although the token *was* saved (PR #40 review P1-2).
REJECT_MARKER = "synthetic-keyring-reject"
READBACK_MARKER = "synthetic-keyring-readback-fail"


class SyntheticKeyring:
    """In-memory stand-in for the OS keyring (never the real one); one slot per process."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}
        self._fail_next_get = False
        self._lock = threading.Lock()

    def get_password(self, service: str, username: str) -> str | None:
        with self._lock:
            if self._fail_next_get:
                self._fail_next_get = False
                raise RuntimeError("synthetic keyring read-back failed")
            return self._values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if REJECT_MARKER in password:
            raise RuntimeError("synthetic keyring rejected the credential")
        with self._lock:
            self._values[(service, username)] = password
            self._fail_next_get = READBACK_MARKER in password

    def delete_password(self, service: str, username: str) -> None:
        with self._lock:
            self._values.pop((service, username), None)


class SyntheticEnvironment:
    """Stands in for ``EnvironmentService``; same response shape as the legacy route."""

    def __init__(self) -> None:
        self.calls: list[tuple[str | None, bool]] = []
        self._lock = threading.Lock()

    def snapshot(
        self, requested_profile: str | None = None, *, refresh_devices: bool = False
    ) -> dict[str, object]:
        with self._lock:
            self.calls.append((requested_profile, refresh_devices))
        profile = requested_profile if requested_profile in {"cpu", "cuda", "intel"} else "cpu"
        time.sleep(DELAYS.get(profile, 0.05) + (0.5 if refresh_devices else 0.0))
        device = {"cpu": "cpu", "cuda": "cuda:0", "intel": "cpu"}[profile]
        return {
            "profiles": [
                {
                    "name": name,
                    "path": "",
                    "exists": name != "vulkan",
                    "updated_at": None,
                    "compatible": name != "intel",
                    "compatibility_reason": None,
                }
                for name in ("cpu", "cuda", "intel", "vulkan")
            ],
            "active_profile": profile,
            "devices": None,
            "models": [],
            "native": None,
            "options": {
                "asr": [
                    {
                        "id": "faster-whisper",
                        "label": "faster-whisper",
                        "models": [{"id": "synthetic-small", "key": "s", "label": "synthetic"}],
                        "devices": [{"id": device, "label": device}],
                    }
                ],
                "diarization": [],
                "languages": [{"id": "ja"}, {"id": "en"}],
                "formats": [],
                "defaults": {},
                "guidance": [],
            },
            "errors": [],
            "profile_warnings": [],
        }


def build_app(data_dir: Path, next_ui_root: Path | None = None) -> Any:
    # Fix the log location inside the temporary directory so the harness never reads the real
    # core config (``config.toml``) or user log folders. Fallback/raw-log variants are tested by
    # rewriting the GET response in the browser test.
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging_runtime._STATUS = logging_runtime.GuiLoggingStatus(logs, False, False)
    # The legacy page (``/``) asks for ``/logo`` from ``repo_root``; give it the repository icon.
    icon = data_dir / "icon"
    icon.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "icon" / "utteran-glyph-512.png", icon / "utteran-glyph-512.png")
    return create_app(
        SESSION,
        repo_root=data_dir,
        settings_store=SettingsStore(data_dir / "gui-settings.json"),
        token_store=TokenStore(SyntheticKeyring()),
        environment_service=SyntheticEnvironment(),  # type: ignore[arg-type]
        enable_next_ui=True,
        next_ui_root=next_ui_root,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8791)
    args = parser.parse_args()
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="utteran-ui2-") as temp:
        app = build_app(Path(temp))
        print(f"http://127.0.0.1:{args.port}/launch?session={SESSION}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
