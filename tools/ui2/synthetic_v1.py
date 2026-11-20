"""Synthetic B4/B3 stand-ins for the S5-A E2E (``serve_next_ui.py --v1``). Not product code.

The *real* B5 store, B7 admission (input identity, token, freeze proof, idempotency), B6/B7
control store, queue CAS and dispatcher run unchanged. Only two things are synthetic:

* :class:`SyntheticProvider` — B4 evidence for one ``fw-cpu`` deployment of ``large-v3-turbo``,
  evaluated by the real pure evaluator/resolver. Every other model has no deployment, so the real
  resolver answers "no runnable candidate" (409 ``execution_unavailable``). It is never "ready"
  for real hardware; nothing is probed, downloaded or inferred.
* :class:`SyntheticRunner` — starts a trivial local child (for the launch journal) and reports a
  synthetic verified result. An input named ``slow-*`` waits for a cooperative cancel (up to 60 s).
  No model is loaded, no audio is read and no file is written.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from utteran.capabilities.evaluator import digest, evidence_fingerprint, make_inventory
from utteran.capabilities.recipes import get_recipe
from utteran.capabilities.resolver import resolve
from utteran_contracts.capabilities import (
    CapabilityObservation,
    DeploymentInventory,
    ExecutionIdentity,
)
from utteran_contracts.settings import Policy, ProcessingSettings
from utteran_contracts.worker import (
    WorkerArtifact,
    WorkerCompleted,
    WorkerErrorCode,
    WorkerRequest,
)
from utteran_control.deletion_repository import DeletionControlStore
from utteran_gui.job_admission import ProviderResult
from utteran_gui.job_runtime import JobRuntime
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.worker_client import WorkerClientError

READY_MODEL = "large-v3-turbo"
FP = digest("synthetic S5-A identity only")


def _deployment(settings: ProcessingSettings, now: datetime) -> DeploymentInventory:
    recipe = get_recipe("fw-cpu")
    identity = ExecutionIdentity.model_validate(
        dict(
            recipe_id="fw-cpu",
            recipe_revision=1,
            adapter_version="b4.1",
            smoke_version=1,
            profile_id="cpu",
            profile_generation="synthetic-generation-1",
            profile_fingerprint=FP,
            platform="Linux",
            python_abi="cp312",
            runtime_fingerprint=FP,
            hardware_fingerprint=FP,
            model_id=READY_MODEL,
            model_family=recipe.model_family,
            english_only=False,
            model_revision="synthetic-revision-1",
            model_files_fingerprint=FP,
            native_fingerprint=None,
            device=recipe.devices[0],
            device_index=0,
            compute_type=recipe.compute_types[0],
            execution_path="worker",
            local_complete_model=True,
            input_count=1,
            diarization_device=None,
            diarization_device_index=None,
            diarization_model=None,
            diarization_fingerprint=None,
        )
    )
    fp = evidence_fingerprint(identity, settings)
    actual = recipe.requested_route.to_wire()
    for component in ("encoder", "decoder"):
        if actual[component] == "selected-device":
            actual[component] = identity.device
    measured = {k: v if v != "backend-default" else "unknown" for k, v in actual.items()}
    observations = []
    for kind in ("hardware", "runtime", "venv", "model", "native", "smoke", "memory"):
        data: dict[str, Any] = dict(
            kind=kind,
            state="passed",
            source="provider",
            observed_at=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=12),
            fingerprint=fp,
            reason_code="supported",
        )
        if kind == "smoke":
            data.update(
                initialized_route=actual,
                encoder_observed=measured["encoder"],
                decoder_observed=measured["decoder"],
            )
        observations.append(CapabilityObservation.model_validate(data))
    return DeploymentInventory(
        identity=identity,
        observations=tuple(observations),
        required_memory_bytes=100,
        available_memory_bytes=200,
    )


class SyntheticProvider:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir

    def resolve(self, settings: ProcessingSettings, policy: Policy, *, revision: int):
        now = datetime.now(UTC)
        deployments = (_deployment(settings, now),) if settings.asr.model == READY_MODEL else ()
        inventory = make_inventory(deployments, settings, "worker", revision=revision, now=now)
        selection = resolve(inventory, policy, now=now)
        models = {a.candidate.id: str(self.model_dir) for a in selection.alternatives if a.eligible}
        return ProviderResult("supported", selection, models)


class SyntheticRunner:
    def __init__(self, output: Path, launch_release: Path | None = None) -> None:
        self.output = output
        self.launch_release = launch_release

    def execute(
        self,
        request: WorkerRequest,
        *,
        cancel: threading.Event | None = None,
        on_event: Any = None,
        on_spawn: Any = None,
    ) -> WorkerCompleted:
        slow = Path(request.input_path).name.startswith("slow-")
        if slow and self.launch_release is not None:
            # The dispatcher has committed dispatch_started; leave launch_confirmed to on_spawn.
            deadline = time.monotonic() + 20
            while not self.launch_release.exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("synthetic launch barrier not released")
                if cancel is not None and cancel.wait(0.02):
                    raise WorkerClientError(WorkerErrorCode.CANCELLED)
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        if on_spawn is not None:
            on_spawn(child)
        child.wait()
        if slow:
            if cancel is not None and cancel.wait(60):
                raise WorkerClientError(WorkerErrorCode.CANCELLED)
        elif cancel is not None and cancel.wait(1.0):
            raise WorkerClientError(WorkerErrorCode.CANCELLED)
        # A real (synthetic, non-transcript) file in the job's own directory, so the
        # read-only artifact view has something to verify. Old snapshots keep the flat name.
        general = request.context.base_config["general"]
        assert isinstance(general, dict)
        target_dir = Path(str(general["output_dir"]))
        stem = request.context.output_stem or "synthetic"
        target = target_dir / f"{stem}.srt"
        content = b"1\n00:00:00,000 --> 00:00:01,000\nsynthetic\n"
        target.write_bytes(content)
        artifact = WorkerArtifact(
            path=str(target), size=len(content), sha256=hashlib.sha256(content).hexdigest()
        )
        manifest = WorkerArtifact(path=str(self.output / "manifest.json"), size=1, sha256="b" * 64)
        return WorkerCompleted(
            sequence=5,
            request_id=request.request_id,
            job_id=request.job_id,
            attempt_id=request.attempt_id,
            core_job_id="0123456789abcdef",
            artifacts=(artifact,),
            manifest=manifest,
        )


def prepare_root(root: Path) -> dict[str, Path]:
    """Synthetic inputs (tiny non-audio bytes) and an output folder inside ``root``."""
    layout = {"inputs": root / "in", "output": root / "out", "model": root / "model"}
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    for name in ("meeting.wav", "slow-meeting.wav", "other.wav"):
        (layout["inputs"] / name).write_bytes(b"RIFF synthetic S5-A bytes, not audio")
    return layout


def synthetic_v1_runtime(
    root: Path, legacy_queue: OperationQueue, *, hold_slow_launch: bool = False
) -> tuple[JobRuntime, Path]:
    layout = prepare_root(root)
    settings_path = root / "settings" / "resources.sqlite3"
    runtime = JobRuntime(
        store_path=root / "control" / "operations.sqlite3",
        settings_path=settings_path,
        legacy_queue=legacy_queue,
        provider=SyntheticProvider(layout["model"]),
        runner=SyntheticRunner(
            layout["output"], root / "release-slow-launch" if hold_slow_launch else None
        ),
        job_dir=root / "jobs",
        poll_interval=0.2,
        # Layout v8 (history visibility + C2c deletion) like the real --next-ui-v1 runtime.
        store_type=DeletionControlStore,
    )
    return runtime, settings_path
