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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal, Protocol, cast, get_args

from pydantic import Field, StrictStr, ValidationError

from utteran.capabilities import nvml
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
from utteran.capabilities.recipes import get_recipe
from utteran.models.catalog import get_model
from utteran.models.manager import ModelManager
from utteran.types import TranscriptionResult
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
    "PYANNOTE_METRICS_ENABLED": "0",
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
    recipe_id: Literal["fw-cpu", "fw-cuda", "wc-vulkan", "wc-ov", "wc-ov-vk"]
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
    route: tuple[str, str] | None = None
    diarization_device: str | None = None
    diarization_required_memory_bytes: int | None = None
    diarization_available_memory_bytes: int | None = None


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


def write_speech_fixture(path: Path) -> str:
    """Copy the reviewed product resource through a bounded, digest-checked read."""
    source = files("utteran.capabilities").joinpath("fixtures", "japanese_reference.wav")
    with source.open("rb") as fixture:
        data = fixture.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("speech fixture exceeds limit")
    blob = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    if hashlib.sha1(blob, usedforsecurity=False).hexdigest() != (
        "e8fb29d27fc133bf8a5e956c81033f8e9df1f34d"
    ):
        raise ValueError("speech fixture digest mismatch")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def write_fixture(path: Path) -> str:
    """Write the fixture WAV (16 kHz mono 16-bit PCM) and return its SHA-256."""
    with wave.open(str(path), "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(FIXTURE_RATE)
        sink.writeframes(fixture_bytes())
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- real runtime


def _attested_gpu(ordinal: int) -> str | None:
    """UUID of the GPU this smoke runs on, only where that is provable (PR #91 P2-2).

    Accepted only for ordinal 0 on a host with exactly one NVIDIA GPU and no CUDA
    visibility/order variables (``fingerprints.single_cuda_gpu``), and only when the CUDA
    driver API in this very process reports that same UUID for the ordinal. Otherwise the
    VRAM values stay unknown, so the evaluator keeps the candidate unknown (never ready).
    """
    from utteran.capabilities.fingerprints import single_cuda_gpu

    expected = single_cuda_gpu()
    if ordinal != 0 or expected is None:
        return None
    return expected.uuid if nvml.cuda_ordinal_uuid(ordinal) == expected.uuid else None


class _VramPeak:
    """Bounded sampling of this process's VRAM on one GPU (every 100 ms, at most 6000
    samples). The maximum seen is a lower bound of the true peak for this short fixture,
    not a guarantee for long audio; no sample at all (e.g. WDDM N/A) means unknown."""

    INTERVAL = 0.1
    LIMIT = 6000

    def __init__(self, uuid: str) -> None:
        import threading

        self.uuid = uuid
        self._peak: int | None = None
        self._incomplete = False
        self._reader: nvml.UsageSampler | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @property
    def peak(self) -> int | None:
        return None if self._incomplete else self._peak

    def _sample(self) -> None:
        value = self._reader.read() if self._reader is not None else None
        if value is not None and (self._peak is None or value > self._peak):
            self._peak = value

    def _run(self) -> None:
        try:
            for _ in range(self.LIMIT):
                self._sample()
                if self._stop.wait(self.INTERVAL):
                    return
        finally:
            if self._reader is not None:
                self._reader.__exit__()

    def __enter__(self) -> _VramPeak:
        # One NVML session for all samples (init/shutdown per read is slow on Windows).
        self._reader = nvml.UsageSampler(self.uuid).__enter__()
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            # The sampler owns its session until the in-flight call returns.
            self._incomplete = True


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
        settings: ProcessingSettings | None = None,
    ) -> RuntimeReport:
        from utteran.asr.faster_whisper import FasterWhisperBackend
        from utteran.errors import BackendUnavailableError, ModelNotFoundError, VramExhaustedError
        from utteran.memory import measure_peak, read_memory
        from utteran.types import ASROptions
        from utteran_contracts.prompting import CURRENT_PROMPT_COMPILER, compile_prompt

        available = read_memory("cpu").system_available_bytes if device == "cpu" else None
        gpu_uuid = _attested_gpu(device_index) if device == "cuda" else None
        if gpu_uuid is not None:
            available = nvml.free_memory(gpu_uuid)
        if device == "cuda" and gpu_uuid is None:
            raise SmokeFailure("runtime_mismatch")
        gpu_used: int | None = None
        sampler = _VramPeak(gpu_uuid) if gpu_uuid is not None else contextlib.nullcontext()
        backend = FasterWhisperBackend(local_only=True)
        target = "cpu" if device == "cpu" else f"cuda:{device_index}"
        word_timestamps = settings is not None and (
            settings.asr.word_timestamps != "never" or settings.diarization.enabled
        )
        with measure_peak() as monitor, sampler:
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
                # Exercise the effective options on the reviewed speech fixture.
                result = backend.transcribe(
                    audio,
                    ASROptions(
                        language=language,
                        initial_prompt=(
                            None
                            if settings is None
                            else compile_prompt(
                                settings.asr.initial_prompt,
                                settings.asr.glossary,
                                settings.asr.language,
                                compiler=CURRENT_PROMPT_COMPILER,
                            )
                        ),
                        vad_filter=settings is not None and settings.asr.vad.enabled,
                        beam_size=beam_size,
                        condition_on_previous_text=settings is not None
                        and settings.asr.condition_on_previous_text,
                        word_timestamps=word_timestamps,
                    ),
                )
                duration = float(result.duration)
                if settings is not None and (
                    not result.segments or (word_timestamps and not _has_words(result))
                ):
                    raise SmokeFailure("inference_failed")
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
        if isinstance(sampler, _VramPeak):
            # NVML reports N/A under some drivers (e.g. Windows WDDM) -> unknown, not guessed.
            gpu_used = sampler.peak
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
            required_memory_bytes=peak if device == "cpu" else gpu_used,
            available_memory_bytes=available,
        )


def _has_words(result: TranscriptionResult) -> bool:
    return any(
        word.text.strip() and 0 <= word.start < word.end <= result.duration + 0.1
        for segment in result.segments
        for word in segment.words
    )


def _native_smoke(
    identity: ExecutionIdentity, model: Path, audio: Path, settings: ProcessingSettings
) -> RuntimeReport:
    from utteran.asr.whisper_cpp import WhisperCppBackend
    from utteran.config import Config
    from utteran.config_adapter import ExecutionContext, adapt_settings
    from utteran.errors import BackendUnavailableError, ModelNotFoundError, VramExhaustedError
    from utteran.pipeline import _asr_options

    recipe = get_recipe(identity.recipe_id)
    variant = recipe.requested_route.native_variant
    assert variant is not None
    config = adapt_settings(
        settings,
        ExecutionContext(
            Config(),
            recipe.backend,
            identity.model_id,
            variant,
            identity.diarization_device or "cpu",
            identity.compute_type,
            variant,
        ),
    ).config
    backend = WhisperCppBackend(config.asr.whisper_cpp, private_model=(identity.model_id, model))
    try:
        backend.load(str(model), variant, identity.compute_type)
        options = _asr_options(config, backend.name)
        result = backend.transcribe(audio, options)
        if options.word_timestamps and not _has_words(result):
            raise SmokeFailure("inference_failed")
        if not result.segments or backend.observed_route is None:
            raise SmokeFailure("inference_failed")
        return RuntimeReport(
            "gpu", (0,), "ggml", result.duration, None, None, backend.observed_route
        )
    except (MemoryError, VramExhaustedError):
        raise SmokeFailure("out_of_memory") from None
    except ModelNotFoundError:
        raise SmokeFailure("model_load_failed") from None
    except BackendUnavailableError:
        raise SmokeFailure("backend_unavailable") from None
    finally:
        backend.unload()


def _diarization_smoke(
    report: RuntimeReport,
    identity: ExecutionIdentity,
    audio: Path,
    settings: ProcessingSettings,
    model_root: Path,
) -> RuntimeReport:
    from utteran.capabilities.fingerprints import available_memory
    from utteran.diarization.pyannote import PyannoteBackend
    from utteran.errors import ModelNotFoundError, VramExhaustedError
    from utteran.memory import measure_peak, read_process_tree_memory
    from utteran.types import DiarizationOptions

    device = identity.diarization_device
    if (
        device is None
        or identity.diarization_model is None
        or identity.diarization_fingerprint is None
    ):
        raise SmokeFailure("model_load_failed")
    model = ModelManager(model_root).managed_path(
        get_model(identity.diarization_model, backend="pyannote")
    )
    target = device if device == "cpu" else f"{device}:0"
    backend = PyannoteBackend(offline=True)
    # Sample after ASR unload. Only compare readings in the same memory domain.
    gpu_uuid = _attested_gpu(0) if device == "cuda" else None
    available = available_memory(device)
    baseline = (
        read_process_tree_memory(os.getpid())
        if device == "cpu"
        else nvml.process_used_memory(gpu_uuid)
        if gpu_uuid is not None
        else None
    )
    cpu_memory = (
        measure_peak() if baseline is not None and device == "cpu" else contextlib.nullcontext()
    )
    gpu_memory = (
        _VramPeak(gpu_uuid)
        if baseline is not None and gpu_uuid is not None
        else contextlib.nullcontext()
    )
    try:
        with cpu_memory as cpu_monitor, gpu_memory:
            backend.load(str(model), target)
            result = backend.diarize(
                audio,
                DiarizationOptions(
                    num_speakers=settings.diarization.num_speakers,
                    min_speakers=settings.diarization.min_speakers,
                    max_speakers=settings.diarization.max_speakers,
                ),
            )
            if not result.turns or not result.exclusive_turns or result.device != target:
                raise SmokeFailure("inference_failed")
    except (MemoryError, VramExhaustedError):
        raise SmokeFailure("out_of_memory") from None
    except ModelNotFoundError:
        raise SmokeFailure("model_load_failed") from None
    finally:
        backend.unload()
    peak = (
        cpu_monitor.peak_bytes
        if cpu_monitor is not None
        else gpu_memory.peak
        if isinstance(gpu_memory, _VramPeak)
        else None
    )
    required = (
        peak - baseline if peak is not None and baseline is not None and peak > baseline else None
    )
    return replace(
        report,
        diarization_device=device,
        diarization_required_memory_bytes=required,
        diarization_available_memory_bytes=available,
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
            initialized_route=(
                route
                if (route := get_recipe(record.recipe_id).requested_route).native_variant
                is not None
                else None
            ),
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
    if (
        "model" in kinds
        or not identity.local_complete_model
        or (identity.diarization_model is not None and identity.diarization_fingerprint is None)
    ):
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
    recipe = get_recipe(request.recipe_id)
    if settings.asr.backend not in {"auto", recipe.backend}:
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
    if identity is None or (identity.device == "gpu" and identity.hardware_fingerprint is None):
        return SmokeResult("recipe_unavailable", "none")
    model_dir = ModelManager(paths.models).managed_path(
        get_model(request.model_id, backend=recipe.backend)
    )
    if not model_dir.is_dir():
        return SmokeResult("model_missing", "none")
    scope = evidence_fingerprint(identity, settings)
    audio = work_dir / FIXTURE_NAME
    use_speech = isinstance(runtime, FasterWhisperSmokeRuntime)
    if use_speech:
        try:
            fixture_sha = write_speech_fixture(audio)
        except (OSError, ValueError):
            return SmokeResult("recipe_unavailable", "none")
    else:
        fixture_sha = write_fixture(audio)
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
        if recipe.backend == "whisper-cpp":
            report = _native_smoke(identity, model_dir, audio, settings)
        else:
            runtime_args = dict(
                model_dir=model_dir,
                device=identity.device,
                device_index=identity.device_index,
                compute_type=identity.compute_type,
                audio=audio,
                language=None if language.casefold() in {"", "auto"} else language,
                beam_size=settings.asr.beam_size,
            )
            if isinstance(runtime, FasterWhisperSmokeRuntime):
                report = runtime.run(**runtime_args, settings=settings)  # type: ignore[arg-type]
            else:
                report = runtime.run(**runtime_args)  # type: ignore[arg-type]
        if settings.diarization.enabled:
            report = _diarization_smoke(report, identity, audio, settings, paths.models)
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
    encoder, decoder = report.route or (observed, observed)
    encoder_index = index if encoder in {"cuda", "vulkan", "openvino-gpu"} else None
    decoder_index = index if decoder in {"cuda", "vulkan"} else None
    finished = now()
    final = record(
        "passed",
        "passed",
        finished,
        encoder_observed=encoder,
        decoder_observed=decoder,
        encoder_device_index_observed=encoder_index,
        decoder_device_index_observed=decoder_index,
        diarization_observed=report.diarization_device,
        diarization_required_memory_bytes=report.diarization_required_memory_bytes,
        diarization_available_memory_bytes=report.diarization_available_memory_bytes,
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
    output_fd = os.dup(sys.stdout.fileno())
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
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
    with os.fdopen(output_fd, "wb") as output:
        output.write((json.dumps(result.to_wire()) + "\n").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
