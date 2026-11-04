"""Serve the *real* ``create_app(enable_next_ui=True)`` with synthetic services (UI-2).

Used by the Playwright E2E (``frontend/playwright.config.ts``) and for manual browser checks.
Only the environment probe and the keyring are replaced by synthetic stand-ins: routing,
``/launch`` cookie, session check, CSP nonce, asset serving and ``/api/settings`` are the
production code paths. Nothing here is imported by the product.

    uv run python tools/ui2/serve_next_ui.py --port 8791

prints the launch URL. Settings are written to a temporary directory that is removed on exit.
No real profile, model, path or token is read or shown.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from utteran_gui.api import create_app  # noqa: E402
from utteran_gui.settings import SettingsStore, TokenStore  # noqa: E402

SESSION = "ui2-e2e-synthetic-session-key"
# Seconds each synthetic profile takes to answer, so ordering races are deterministic.
DELAYS = {"cuda": 1.5, "intel": 0.2}


class SyntheticKeyring:
    def get_password(self, _service: str, _username: str) -> str | None:
        return None

    def set_password(self, _service: str, _username: str, _password: str) -> None:
        raise RuntimeError("synthetic keyring is read-only")

    def delete_password(self, _service: str, _username: str) -> None:
        return None


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
