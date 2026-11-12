"""Explicit one-shot B4 smoke child: ``python -I -B -m utteran.capabilities.smoke run``.

Launched only by the development entry ``python -m utteran_gui.capability_smoke`` (never
by GUI startup, B4 refresh/resolve, B7 admission/prestart or any HTTP route). One run =
one recipe (``fw-cpu``/``fw-cuda``), one complete managed local model, one generated
synthetic WAV, no diarization. It reuses the existing ``FasterWhisperBackend`` with an
explicit device (its ``auto`` CPU fallback is never entered) and reads back what the
loaded CTranslate2 replica reports. See docs/architecture/b4/smoke.md.

Private request on stdin, fixed codes on stdout. No traceback, transcript, path, prompt
or digest is printed; all logging is disabled for the life of the process.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import hashlib
import importlib.metadata
import json
import logging
import os
import stat
import sys
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast, get_args

from pydantic import Field, StrictStr, ValidationError

from utteran.capabilities.collector import collect_managed_deployments
from utteran.capabilities.evaluator import (
    _verify_smoke_route,
    evaluate_candidate,
    evidence_fingerprint,
)
from utteran.capabilities.evidence import (
    EVIDENCE_TTL,
    EvidenceWriteError,
    Fingerprints,
    SmokeEvidence,
    SmokeRecordOutcome,
    evidence_directory,
    remove_evidence,
    remove_temporaries,
    seal_evidence,
    smoke_identity,
    write_evidence,
)
from utteran.capabilities.fingerprints import current_fingerprints
from utteran.models.catalog import get_model
from utteran.models.manager import ModelManager
from utteran_contracts import ProcessingSettings
from utteran_contracts.base import VersionedContract
from utteran_contracts.capabilities import CapabilityObservation, ExecutionIdentity, ModelID
from utteran_paths import DataPaths, resolve_data_paths

REQUEST_LIMIT = 1024 * 1024
# Forced in-process too, so neither faster-whisper nor huggingface_hub can go online.
OFFLINE_ENVIRONMENT = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
}

# CTranslate2 reports the *effective* compute type. On CPU a requested ``int8`` runs as,
# and is reported as, ``int8_float32`` (observed with CTranslate2 4.8.1 in the Cloud
# code-path check). Only this explicit alias is accepted; every other difference,
# including any CUDA difference, is a compute_mismatch (fail closed).
EQUIVALENT_COMPUTE: dict[tuple[str, str], frozenset[str]] = {
    ("cpu", "int8"): frozenset({"int8", "int8_float32"}),
}

FIXTURE_RATE = 16_000
FIXTURE_SECONDS = 3
FIXTURE_NAME = "smoke-fixture.wav"

SmokeOutcome = Literal[
    "passed",
    "route_unverified",
    "route_mismatch",
    "compute_mismatch",
    "runtime_mismatch",
    "request_invalid",
    "profile_mismatch",
    "dev_root_invalid",
    "settings_unsupported",
    "recipe_unavailable",
    "profile_incompatible",
    "model_missing",
    "model_load_failed",
    "backend_unavailable",
    "out_of_memory",
    "inference_failed",
    "evidence_write_failed",
    "evidence_limit",
    "internal_error",
]
SMOKE_OUTCOMES: frozenset[str] = frozenset(get_args(SmokeOutcome))


class SmokeRequest(VersionedContract):
    """Private (settings may hold a prompt). Sent on stdin only, never on argv."""

    profile_id: Literal["cpu", "cuda", "intel", "vulkan"]
    recipe_id: Literal["fw-cpu", "fw-cuda"]
    model_id: ModelID
    settings: ProcessingSettings
    work_dir: Annotated[StrictStr, Field(min_length=1, max_length=4096)]


@dataclass(frozen=True)
class RuntimeReport:
    """Values read back from the runtime after the inference completed."""

    device: str
    device_indices: tuple[int, ...]
    compute_type: str
    duration_seconds: float
    required_memory_bytes: int | None
    available_memory_bytes: int | None


class SmokeFailure(Exception):
    def __init__(self, outcome: SmokeRecordOutcome) -> None:
        self.outcome: SmokeRecordOutcome = outcome
        super().__init__(outcome)


class SmokeRuntime(Protocol):
    def run(
        self,
        *,
        model_dir: Path,
        device: str,
        device_index: int,
        compute_type: str,
        audio: Path,
        language: str | None,
        beam_size: int,
    ) -> RuntimeReport: ...


@dataclass(frozen=True)
class SmokeResult:
    outcome: SmokeOutcome
    evidence: Literal["none", "recorded", "invalidated"]

    def to_wire(self) -> dict[str, object]:
        return {"schema_version": 1, "outcome": self.outcome, "evidence": self.evidence}


# --------------------------------------------------------------------------- fixture


@functools.cache
def fixture_bytes() -> bytes:
    """Deterministic synthetic tone mixture (integer arithmetic only: platform-stable).

    No recording, voice or personal data; generated, never committed as a binary.
    """
    frames = bytearray()
    total = FIXTURE_RATE * FIXTURE_SECONDS
    for n in range(total):
        value = 0
        for frequency in (220, 330, 495):
            phase = (n * frequency * 65536 // FIXTURE_RATE) % 65536
            value += abs(phase - 32768) - 16384  # triangle wave, +-16384
        envelope = min(n, total - n, 1600)  # 100 ms linear fade in/out
        sample = value * envelope // (1600 * 5)
        frames += int(sample).to_bytes(2, "little", signed=True)
    return bytes(frames)


def write_fixture(path: Path) -> str:
    """Write the fixture WAV (16 kHz mono 16-bit PCM) and return its SHA-256."""
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(FIXTURE_RATE)
        sink.writeframes(fixture_bytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- real runtime


def _peak_rss_bytes() -> int | None:
    if sys.platform == "win32":
        return None
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB; macOS (not a supported recipe platform) reports bytes.
    return int(value) * (1 if sys.platform == "darwin" else 1024) if value > 0 else None


class FasterWhisperSmokeRuntime:
    """The only production runtime. Not selectable from the command line."""

    def run(
        self,
        *,
        model_dir: Path,
        device: str,
        device_index: int,
        compute_type: str,
        audio: Path,
        language: str | None,
        beam_size: int,
    ) -> RuntimeReport:
        from utteran.asr.faster_whisper import FasterWhisperBackend
        from utteran.errors import BackendUnavailableError, ModelNotFoundError, VramExhaustedError
        from utteran.memory import measure_peak, read_memory
        from utteran.types import ASROptions

        available = read_memory("cpu").system_available_bytes if device == "cpu" else None
        backend = FasterWhisperBackend()
        target = "cpu" if device == "cpu" else f"cuda:{device_index}"
        with measure_peak() as monitor:
            try:
                backend.load(str(model_dir), target, compute_type)
            except VramExhaustedError:
                raise SmokeFailure("out_of_memory") from None
            except MemoryError:
                raise SmokeFailure("out_of_memory") from None
            except BackendUnavailableError:
                raise SmokeFailure("backend_unavailable") from None
            except ModelNotFoundError:
                raise SmokeFailure("model_load_failed") from None
            except Exception:
                raise SmokeFailure("model_load_failed") from None
            try:
                # VAD off: a VAD that removes the whole fixture would skip the model,
                # so encoder and decoder are guaranteed to run. No prompt, no words.
                result = backend.transcribe(
                    audio,
                    ASROptions(
                        language=language,
                        initial_prompt=None,
                        vad_filter=False,
                        beam_size=beam_size,
                        condition_on_previous_text=False,
                        word_timestamps=False,
                    ),
                )
                duration = float(result.duration)
                del result  # never retained, logged or printed
            except (VramExhaustedError, MemoryError):
                raise SmokeFailure("out_of_memory") from None
            except Exception:
                raise SmokeFailure("inference_failed") from None
            # CTranslate2 replica read-back after completion (smoke.md §1.1). The
            # requested target is not copied: a missing or odd value stays unknown.
            replica = getattr(getattr(backend, "_model", None), "model", None)
            observed_device = getattr(replica, "device", None)
            observed_indices = getattr(replica, "device_index", None)
            observed_compute = getattr(replica, "compute_type", None)
        peak = max((v for v in (monitor.peak_bytes, _peak_rss_bytes()) if v), default=None)
        backend.unload()
        import ctranslate2
        import faster_whisper

        if ctranslate2.__version__ != importlib.metadata.version("ctranslate2") or (
            faster_whisper.__version__ != importlib.metadata.version("faster-whisper")
        ):
            raise SmokeFailure("runtime_mismatch")
        indices = (
            tuple(observed_indices)
            if isinstance(observed_indices, (list, tuple))
            and all(isinstance(i, int) and not isinstance(i, bool) for i in observed_indices)
            else ()
        )
        return RuntimeReport(
            device=observed_device if isinstance(observed_device, str) else "",
            device_indices=indices,
            compute_type=observed_compute if isinstance(observed_compute, str) else "",
            duration_seconds=duration,
            required_memory_bytes=peak if device == "cpu" else None,
            available_memory_bytes=available,
        )


# --------------------------------------------------------------------------- orchestration


def _empty_private_dir(value: str) -> Path | None:
    path = Path(value)
    try:
        if not path.is_absolute() or not stat.S_ISDIR(os.lstat(path).st_mode):
            return None
        with os.scandir(path) as entries:
            if next(entries, None) is not None:
                return None
    except OSError:
        return None
    return path


def _route_observation(record: SmokeEvidence, scope: str) -> CapabilityObservation:
    return CapabilityObservation.model_validate(
        dict(
            kind="smoke",
            state="passed",
            source="smoke",
            observed_at=record.observed_at,
            expires_at=record.expires_at,
            fingerprint=scope,
            reason_code="supported",
            encoder_observed=record.encoder_observed,
            decoder_observed=record.decoder_observed,
            encoder_device_index_observed=record.encoder_device_index_observed,
            decoder_device_index_observed=record.decoder_device_index_observed,
        )
    )


def _precheck(
    identity: ExecutionIdentity, candidate_issues: set[tuple[str, str]]
) -> SmokeOutcome | None:
    kinds = {kind for kind, _code in candidate_issues}
    if "venv" in kinds:
        return "profile_incompatible"
    if "model" in kinds or not identity.local_complete_model:
        return "model_missing"
    if kinds & {"settings", "software_support", "execution_path"}:
        return "settings_unsupported"
    return None


def run_smoke(
    request: SmokeRequest,
    *,
    runtime: SmokeRuntime,
    now: Callable[[], datetime],
    paths: DataPaths,
    profile_root: Path,
    repo_root: Path,
    fingerprints: Fingerprints = current_fingerprints,
) -> SmokeResult:
    directory = evidence_directory(paths)
    if directory is None:
        return SmokeResult("dev_root_invalid", "none")
    work_dir = _empty_private_dir(request.work_dir)
    if work_dir is None:
        return SmokeResult("request_invalid", "none")
    settings = request.settings
    if settings.diarization.enabled or settings.asr.backend not in {"auto", "faster-whisper"}:
        return SmokeResult("settings_unsupported", "none")
    started = now()
    deployments = collect_managed_deployments(
        request.profile_id,
        settings,
        "worker",
        now=started,
        profile_root=profile_root,
        repo_root=repo_root,
        model_root=paths.models,
        native_root=paths.native,
    )
    deployment = next(
        (
            d
            for d in deployments
            if d.identity.recipe_id == request.recipe_id and d.identity.model_id == request.model_id
        ),
        None,
    )
    if deployment is None:
        return SmokeResult("recipe_unavailable", "none")
    base = evaluate_candidate(deployment, settings, "worker", now=started)
    refused = _precheck(deployment.identity, {(i.kind, i.code) for i in base.issues})
    if refused is not None:
        return SmokeResult(refused, "none")
    identity = smoke_identity(deployment.identity, fingerprints)
    if identity is None:
        return SmokeResult("recipe_unavailable", "none")
    model_dir = ModelManager(paths.models).managed_path(
        get_model(request.model_id, backend="faster-whisper")
    )
    if not model_dir.is_dir():
        return SmokeResult("model_missing", "none")
    scope = evidence_fingerprint(identity, settings)
    fixture_sha = write_fixture(work_dir / FIXTURE_NAME)
    remove_temporaries(directory)

    def record(state: str, outcome: str, at: datetime, **values: object) -> SmokeEvidence:
        fields: dict[str, object] = dict(
            scope=scope,
            recipe_id=request.recipe_id,
            state=state,
            outcome=outcome,
            observed_at=at,
            expires_at=at + EVIDENCE_TTL,
            encoder_observed="unknown",
            decoder_observed="unknown",
            encoder_device_index_observed=None,
            decoder_device_index_observed=None,
            required_memory_bytes=None,
            available_memory_bytes=None,
            fixture_sha256=fixture_sha,
        )
        fields.update(values)
        return seal_evidence(**fields)

    # Invalidate any earlier result for this scope *before* inference, so a killed,
    # timed-out or failed re-smoke can never leave an old success adoptable.
    try:
        write_evidence(directory, record("pending", "running", started))
    except EvidenceWriteError as exc:
        removed = remove_evidence(directory, scope)
        return SmokeResult(exc.code, "invalidated" if removed else "none")

    def failed(outcome: SmokeRecordOutcome) -> SmokeResult:
        # If this write fails the pending record stays, which is equally not adoptable.
        with contextlib.suppress(EvidenceWriteError):
            write_evidence(directory, record("failed", outcome, now()))
        return SmokeResult(cast(SmokeOutcome, outcome), "invalidated")

    language = settings.asr.language.strip()
    try:
        report = runtime.run(
            model_dir=model_dir,
            device=identity.device,
            device_index=identity.device_index,
            compute_type=identity.compute_type,
            audio=work_dir / FIXTURE_NAME,
            language=None if language.casefold() in {"", "auto"} else language,
            beam_size=settings.asr.beam_size,
        )
    except SmokeFailure as failure:
        return failed(failure.outcome)
    except Exception:
        return failed("inference_failed")
    accepted = EQUIVALENT_COMPUTE.get(
        (identity.device, identity.compute_type), frozenset({identity.compute_type})
    )
    if report.compute_type not in accepted:
        return failed("compute_mismatch")
    if not report.duration_seconds > 0:
        return failed("inference_failed")
    observed = report.device if report.device in {"cpu", "cuda"} else "unknown"
    index = report.device_indices[0] if len(report.device_indices) == 1 else None
    if index is not None and index < 0:
        index = None
    # CTranslate2 reports one replica device for the whole Whisper model; it is the
    # attestation for both components (smoke.md §1.1). CPU has no meaningful index.
    attested_index = index if observed == "cuda" else None
    finished = now()
    final = record(
        "passed",
        "passed",
        finished,
        encoder_observed=observed,
        decoder_observed=observed,
        encoder_device_index_observed=attested_index,
        decoder_device_index_observed=attested_index,
        required_memory_bytes=report.required_memory_bytes,
        available_memory_bytes=report.available_memory_bytes,
    )
    try:
        write_evidence(directory, final)
    except EvidenceWriteError:
        removed = remove_evidence(directory, scope)
        return SmokeResult("evidence_write_failed", "invalidated" if removed else "none")
    route = _verify_smoke_route(_route_observation(final, scope), identity)
    outcomes: dict[str, SmokeOutcome] = {
        "verified": "passed",
        "unverified": "route_unverified",
        "mismatch": "route_mismatch",
    }
    return SmokeResult(outcomes[route], "recorded")


def _read_request() -> SmokeRequest:
    raw = sys.stdin.buffer.read(REQUEST_LIMIT + 1)
    if len(raw) > REQUEST_LIMIT:
        raise ValueError("request too large")
    return SmokeRequest.model_validate_json(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B4 explicit smoke child (development only)")
    parser.add_argument("action", choices=("run",))
    parser.parse_args(argv)
    os.environ.update(OFFLINE_ENVIRONMENT)
    logging.disable(logging.CRITICAL)
    try:
        request = _read_request()
    except (ValidationError, ValueError, UnicodeError, OSError):
        result = SmokeResult("request_invalid", "none")
    else:
        if os.environ.get("UTTERAN_PROFILE") != request.profile_id:
            result = SmokeResult("profile_mismatch", "none")
        else:
            try:
                result = run_smoke(
                    request,
                    runtime=FasterWhisperSmokeRuntime(),
                    now=lambda: datetime.now(UTC),
                    paths=resolve_data_paths(),
                    profile_root=Path(sys.prefix),
                    repo_root=Path.cwd(),
                )
            except Exception:
                result = SmokeResult("internal_error", "none")
    sys.stdout.buffer.write((json.dumps(result.to_wire()) + "\n").encode("utf-8"))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
