"""Job-free ASR benchmarking and deterministic aggregation."""

from __future__ import annotations

import statistics
import time
import wave
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from utteran.asr.base import ASRBackend
from utteran.asr.faster_whisper import FasterWhisperBackend
from utteran.asr.whisper_cpp import WhisperCppBackend
from utteran.config import Config
from utteran.errors import BackendUnavailableError, ModelNotFoundError
from utteran.models.catalog import get_model, list_models
from utteran.models.manager import ModelManager
from utteran.types import ASROptions


@dataclass(frozen=True)
class BenchmarkSample:
    load_seconds: float
    transcribe_seconds: float
    peak_ram_bytes: int | None = None

    @property
    def total_seconds(self) -> float:
        return self.load_seconds + self.transcribe_seconds


@dataclass(frozen=True)
class BenchmarkResult:
    variant: str
    samples: tuple[BenchmarkSample, ...]
    median_total_seconds: float
    median_load_seconds: float
    realtime_factor: float
    peak_ram_bytes: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def aggregate(
    variant: str, samples: list[BenchmarkSample], audio_seconds: float
) -> BenchmarkResult:
    """Aggregate injected samples with medians, keeping tests independent of models."""
    totals = [sample.total_seconds for sample in samples]
    peaks = [sample.peak_ram_bytes for sample in samples if sample.peak_ram_bytes is not None]
    median_total = statistics.median(totals)
    return BenchmarkResult(
        variant=variant,
        samples=tuple(samples),
        median_total_seconds=median_total,
        median_load_seconds=statistics.median(sample.load_seconds for sample in samples),
        realtime_factor=audio_seconds / median_total,
        peak_ram_bytes=max(peaks) if peaks else None,
    )


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    except (OSError, wave.Error, ZeroDivisionError) as exc:
        raise ValueError("benchmarkの入力はデコード可能なWAVを指定してください") from exc


def run_benchmark(
    config: Config,
    audio: Path,
    variants: tuple[str, ...],
    *,
    word_timestamps: bool,
    repeat: int,
    warmup: int,
    clock: Callable[[], float] = time.perf_counter,
) -> list[BenchmarkResult]:
    """Run backends directly so benchmark activity never creates jobs or transcripts."""
    duration = wav_duration(audio)
    results: list[BenchmarkResult] = []
    for variant in variants:
        samples: list[BenchmarkSample] = []
        try:
            backend_name = "faster-whisper" if variant == "faster-whisper" else "whisper-cpp"
            model = _installed_model(config.asr.model, backend_name)
            for index in range(warmup + repeat):
                if variant == "faster-whisper":
                    backend: ASRBackend = FasterWhisperBackend()
                    device = config.asr.device if config.asr.device != "auto" else "cpu"
                    compute = config.asr.compute_type
                else:
                    settings = config.asr.whisper_cpp.model_copy(update={"variant": variant})
                    backend = WhisperCppBackend(settings, allow_fallback=False)
                    device, compute = variant, "ggml"
                started = clock()
                backend.load(model, device, compute)
                loaded = clock()
                backend.transcribe(
                    audio,
                    ASROptions(
                        language=config.asr.language,
                        initial_prompt=config.asr.initial_prompt,
                        vad_filter=config.asr.vad_filter,
                        beam_size=config.asr.beam_size,
                        condition_on_previous_text=config.asr.condition_on_previous_text,
                        word_timestamps=word_timestamps,
                    ),
                )
                finished = clock()
                backend.unload()
                if index >= warmup:
                    samples.append(BenchmarkSample(loaded - started, finished - loaded))
        except (BackendUnavailableError, ModelNotFoundError):
            continue
        results.append(aggregate(variant, samples, duration))
    return results


def _installed_model(configured: str, backend: str) -> str:
    manager = ModelManager()
    try:
        entry = get_model(configured, backend=backend)
        if manager.find_installed(entry)[0] is not None:
            return entry.model_id
    except Exception:
        pass
    entries = list_models(backend=backend)
    preferred = sorted(entries, key=lambda item: not item.model_id.startswith(configured))
    for entry in preferred:
        if manager.find_installed(entry)[0] is not None:
            return entry.model_id
    raise ModelNotFoundError(f"{backend}の導入済みモデルがありません")


def apply_variant(path: Path, variant: str) -> None:
    """Update only [asr.whisper_cpp].variant while preserving the rest of TOML."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    section = "[asr.whisper_cpp]"
    try:
        start = lines.index(section)
    except ValueError:
        if lines and lines[-1]:
            lines.append("")
        lines.extend([section, f'variant = "{variant}"'])
    else:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines)
        )
        target = next(
            (i for i in range(start + 1, end) if lines[i].strip().startswith("variant")), None
        )
        if target is None:
            lines.insert(start + 1, f'variant = "{variant}"')
        else:
            lines[target] = f'variant = "{variant}"'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
