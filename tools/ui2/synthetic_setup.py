"""Synthetic B8 setup world for the 束F E2E (`serve_next_ui.py --v1 --setup-e2e`). Not product.

Not product code: nothing here is imported by the product.

The *real* B8 store, planner, executor (step journal, heavy slot shared with B7 jobs, fence,
cancel/retry/resume), setup routes, diagnostics service and readiness check run unchanged.
Synthetic: the profile CLI (marker files under ``<root>/setup``), the child processes (a
trivial ``python -c pass`` child per step for the launch journal, results scripted here), the
OS/runtime probes, and the B4 evidence the smoke step "records" (a set of verified settings
digests that ``SyntheticProvider`` reads). No venv, model, GPU, driver or network is touched.

File triggers (tests write them under ``<root>/setup``):

* ``profile-<name>``    the profile exists (written by the synthetic venv step too)
* ``fail-verify``       the next verification smoke fails once (``backend_unavailable``)
* ``hold-download``     a model download waits until the file is removed (reload/cancel tests)
* ``runtime-timeout``   the first runtime probe of a pass times out (#89: next one succeeds)
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utteran_contracts import ProcessingSettings
from utteran_contracts.setup import SETUP_MODELS
from utteran_gui import hardware
from utteran_gui.setup_diagnostics import DiagnosticProbes, SetupDiagnostics, VcRedist
from utteran_gui.setup_executor import ChildResult
from utteran_gui.setup_planner import SetupObserver


def settings_digest(settings: ProcessingSettings) -> str:
    text = json.dumps(settings.to_wire(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Info:
    exists: bool
    compatible: bool | None
    compatibility_reason: str | None = None


class SyntheticSetupCli:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.repo_root = root
        (root / "uv.lock").write_text("synthetic lock", encoding="utf-8")

    def profile_info(self, profile: str) -> Info:
        exists = (self.root / f"profile-{profile}").is_file()
        return Info(exists, True if exists else None)


class SyntheticSetupAdapter:
    def __init__(self, cli: SyntheticSetupCli) -> None:
        self.cli = cli
        self.repo_root = cli.root

    def environment(self, profile: str) -> dict[str, str]:
        return {"UTTERAN_PROFILE": profile}

    def profile_command(self, profile: str, arguments: list[str]) -> list[str]:
        return ["synthetic-utteran", profile, *arguments]

    def venv_command(self, profile: str) -> list[str]:
        return ["synthetic-setup.ps1", "-Profile", profile]

    def profile_verified(self, profile: str) -> bool:
        return self.cli.profile_info(profile).exists

    def capability_smoke_command(self, profile: str) -> list[str]:
        return ["synthetic-python", profile, "-m", "utteran.capabilities.smoke", "run"]

    def capability_environment(self, profile: str) -> dict[str, str]:
        return {"UTTERAN_PROFILE": profile}


class SyntheticSetupRunner:
    def __init__(self, root: Path, models: Path, verified: dict[str, str]) -> None:
        self.root = root
        self.models = models
        self.verified = verified  # settings digest -> profile

    def _model_dir(self, ref: str) -> Path:
        model = SETUP_MODELS[ref]
        return self.models / model.backend / model.managed_component

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        on_spawn: Callable[[Any], None],
        capture_status: bool,
        stdin: bytes | None = None,
    ) -> ChildResult:
        # The server watches its stdin for fixture shutdown. A Windows child
        # must not inherit that pipe while another thread is blocked reading it.
        child = subprocess.Popen([sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL)
        on_spawn(child)
        child.wait()
        status: str | None = None
        code = 0
        if argv[0] == "synthetic-setup.ps1":
            (self.root / f"profile-{argv[2]}").write_text("x", encoding="utf-8")
        elif "verify" in argv:
            code = 0 if (self._model_dir(argv[argv.index("verify") + 1]) / "ok").is_file() else 1
        elif "download" in argv:
            hold = self.root / "hold-download"
            deadline = time.monotonic() + 30
            while hold.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            target = self._model_dir(argv[argv.index("download") + 1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "ok").write_text("x", encoding="utf-8")
        elif "token-status" in argv:
            status = json.dumps({"access": "available"})
        elif "transcribe" in argv:
            out = Path(argv[argv.index("--output-dir") + 1])
            (out / "smoke.txt").write_text("", encoding="utf-8")
        elif argv[-2:] == ["utteran.capabilities.smoke", "run"]:
            request = json.loads(stdin or b"{}")
            fail = self.root / "fail-verify"
            if fail.exists():
                fail.unlink()
                outcome = "backend_unavailable"
            else:
                settings = ProcessingSettings.model_validate(request["settings"])
                self.verified[settings_digest(settings)] = request["profile_id"]
                outcome = "passed"
            status = json.dumps({"schema_version": 1, "outcome": outcome, "evidence": "recorded"})
        return ChildResult(code, status if capture_status else None)


def synthetic_setup(
    root: Path, credential_present: Callable[[], bool]
) -> tuple[Any, dict[str, str]]:
    """SetupComponents for a clean synthetic data root (no profile, no model)."""
    from utteran_gui.job_runtime import SetupComponents

    setup_root = root / "setup"
    setup_root.mkdir(parents=True, exist_ok=True)
    models = setup_root / "models"
    models.mkdir(exist_ok=True)
    cli = SyntheticSetupCli(setup_root)
    verified: dict[str, str] = {}
    lock = threading.Lock()

    def runtime(profile: str, refresh: bool) -> dict[str, Any]:
        trigger = setup_root / "runtime-timeout"
        with lock:
            cold = trigger.exists()
            if cold:
                trigger.unlink()  # the first (cold) probe times out once, the next one answers
        if cold:
            raise subprocess.TimeoutExpired("devices", 200)
        return {
            "ctranslate2": {
                "cuda_status": "completed",
                "cuda_device_count": 1,
                "cuda_devices": [{"usable": True}],
            },
            "pytorch": {"cuda_status": "completed", "cuda_available": True},
            "probes": [{"name": "ctranslate2_cuda_count", "status": "completed"}],
        }

    diagnostics = SetupDiagnostics(
        cli,
        probes=DiagnosticProbes(
            gpu=lambda: hardware.GpuReport(
                (hardware.GpuAdapter("NVIDIA Synthetic GPU", "nvidia", "32.0.15.0000"),), "nvidia"
            ),
            memory=lambda: hardware.MemoryReport(16 * 2**30, 8 * 2**30),
            disk=lambda _path: hardware.DiskReport(200 * 2**30),
            vcredist=lambda: VcRedist(
                "missing" if (setup_root / "vcredist-missing").exists() else "present",
                None if (setup_root / "vcredist-missing").exists() else "v14.40.33810.00",
            ),
            runtime=runtime,
        ),
        venv_build_supported=True,  # the synthetic observer plans like Windows
    )
    components = SetupComponents(
        observer=SetupObserver(
            cli,
            models_root=models,
            credential_present=credential_present,
            platform_name="nt",
        ),
        adapter=SyntheticSetupAdapter(cli),
        runner=SyntheticSetupRunner(setup_root, models, verified),
        work_dir=setup_root,
        diagnostics=diagnostics,
    )
    return components, verified
