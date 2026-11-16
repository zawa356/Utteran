"""Serve the *real* ``create_app(enable_next_ui=True)`` with synthetic services (UI-2).

Used by the Playwright E2E (``frontend/playwright.config.ts``) and for manual browser checks.
Only the profile CLI (behind the *real* ``EnvironmentService``, see ``synthetic_environment.py``),
the keyring and the log-location probe are replaced by synthetic stand-ins: routing, ``/launch``
cookie, session check, CSP nonce, asset serving, ``/api/environment``, ``/api/settings``,
``/api/token`` (through the real ``TokenStore``), ``/api/wizard/status``, ``/api/open-folder`` and
(UI-3 S3-A) ``/api/jobs*`` and ``/api/queue`` through the real ``JobManager``/``OperationQueue``
are the production code paths; only the transcription command is a synthetic process
(``synthetic_jobs.py``: no model download, no ASR, no file written).
Nothing here is imported by the product.

    uv run python tools/ui2/serve_next_ui.py --port 8791

prints the launch URL. ``--v1 --data-root DIR`` (UI-3 S5-A) builds the ``--next-ui-v1`` shape
instead: the same app plus the real B5/B7/B9 ``/api/v1`` sub-app on the real loopback authority
(session/Host/Origin middleware unchanged), with the synthetic B4 provider and worker runner of
``synthetic_v1.py``. ``DIR`` must be empty or absent; synthetic inputs are created under
``DIR/in``. Settings are written to a temporary directory that is removed on exit.
No real profile, model, path or token is read or shown.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from utteran_gui import logging_runtime  # noqa: E402
from utteran_gui.api import create_app  # noqa: E402
from utteran_gui.settings import SettingsStore, TokenStore  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from synthetic_environment import synthetic_environment_service  # noqa: E402
from synthetic_jobs import synthetic_job_manager  # noqa: E402

SESSION = "ui2-e2e-synthetic-session-key"


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


def build_app(
    data_dir: Path,
    next_ui_root: Path | None = None,
    *,
    v1_root: Path | None = None,
    port: int | None = None,
) -> Any:
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
    jobs = synthetic_job_manager(data_dir)
    v1: dict[str, Any] = {}
    if v1_root is not None:
        from synthetic_v1 import synthetic_v1_runtime

        runtime, settings_path = synthetic_v1_runtime(v1_root, jobs.queue)
        v1 = dict(
            operation_queue=jobs.queue,
            enable_v1_settings=True,
            v1_authority=f"127.0.0.1:{port}",
            v1_store_path=settings_path,
            enable_v1_jobs=True,
            v1_jobs_runtime=runtime,
            enable_v1_events=True,
        )
    return create_app(
        SESSION,
        repo_root=data_dir,
        settings_store=SettingsStore(data_dir / "gui-settings.json"),
        token_store=TokenStore(SyntheticKeyring()),
        environment_service=synthetic_environment_service(),
        job_manager=jobs,
        enable_next_ui=True,
        next_ui_root=next_ui_root,
        **v1,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--v1", action="store_true", help="S5-A: serve the --next-ui-v1 shape")
    parser.add_argument("--data-root", type=Path, help="empty or absent folder for the v1 data")
    args = parser.parse_args()
    import uvicorn

    v1_root: Path | None = None
    if args.v1:
        if args.data_root is None:
            parser.error("--v1 needs --data-root")
        v1_root = args.data_root.resolve()
        if v1_root.exists() and any(v1_root.iterdir()):
            parser.error("--data-root must be empty or absent")
        v1_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="utteran-ui2-") as temp:
        app = build_app(Path(temp), v1_root=v1_root, port=args.port)
        print(f"http://127.0.0.1:{args.port}/launch?session={SESSION}", flush=True)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
