"""Job-free ASR benchmarking and deterministic aggregation."""

from __future__ import annotations

import statistics
import tempfile
import time
import wave
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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

RECOMMENDED_BENCHMARK_SECONDS = 15 * 60
SHORT_BENCHMARK_WARNING = (
    "この長さの結果は長時間音声を代表しない可能性があります。Phase 3dでは180秒と"
    "24分46秒の素材でVulkan/OpenVINO+Vulkanの順位逆転が観測されました。"
)


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


@dataclass(frozen=True)
class BenchmarkMeasurement:
    audio_duration_seconds: float
    warning: str | None
    results: tuple[BenchmarkResult, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "audio_duration_seconds": self.audio_duration_seconds,
            "warning": self.warning,
            "results": [result.as_dict() for result in self.results],
        }


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


def parse_durations(value: str, full_duration: float) -> tuple[float, ...]:
    """Parse comma-separated seconds and ``full`` while preserving order."""
    durations: list[float] = []
    for token in (item.strip().casefold() for item in value.split(",")):
        if not token:
            continue
        if token == "full":
            duration = full_duration
        else:
            try:
                duration = float(token)
            except ValueError as exc:
                raise ValueError(
                    "--durationsは秒数またはfullをカンマ区切りで指定してください"
                ) from exc
        if duration <= 0:
            raise ValueError("--durationsの秒数は0より大きくしてください")
        if duration > full_duration + 0.001:
            raise ValueError(
                f"測定長{duration:g}秒が入力WAVの長さ{full_duration:.3f}秒を超えています"
            )
        bounded = min(duration, full_duration)
        if not any(abs(existing - bounded) < 0.001 for existing in durations):
            durations.append(bounded)
    if not durations:
        raise ValueError("--durationsに1つ以上の秒数またはfullを指定してください")
    return tuple(durations)


def benchmark_warning(audio_seconds: float) -> str | None:
    return SHORT_BENCHMARK_WARNING if audio_seconds < RECOMMENDED_BENCHMARK_SECONDS else None


@contextmanager
def prepared_audio_lengths(
    source: Path, durations: tuple[float, ...]
) -> Iterator[list[tuple[float, Path]]]:
    """Yield prefix WAVs for requested lengths and remove temporary clips afterward."""
    full_duration = wav_duration(source)
    with tempfile.TemporaryDirectory(prefix="utteran-benchmark-") as temporary:
        prepared: list[tuple[float, Path]] = []
        for index, duration in enumerate(durations):
            if abs(duration - full_duration) < 0.001:
                prepared.append((full_duration, source))
                continue
            target = Path(temporary) / f"duration-{index}.wav"
            actual = _copy_wav_prefix(source, target, duration)
            prepared.append((actual, target))
        yield prepared


def _copy_wav_prefix(source: Path, target: Path, duration: float) -> float:
    try:
        with wave.open(str(source), "rb") as reader, wave.open(str(target), "wb") as writer:
            writer.setparams(reader.getparams())
            frame_rate = reader.getframerate()
            remaining = min(reader.getnframes(), round(duration * frame_rate))
            total = remaining
            while remaining:
                count = min(remaining, frame_rate * 60)
                frames = reader.readframes(count)
                if not frames:
                    break
                writer.writeframes(frames)
                remaining -= count
            return total / frame_rate
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


def apply_variant(path: Path, variant: str, audio_seconds: float) -> None:
    """Record the selected variant and measurement length without changing ASR hashes."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    section = "[asr.whisper_cpp]"
    try:
        start = lines.index(section)
    except ValueError:
        if lines and lines[-1]:
            lines.append("")
        lines.extend(
            [
                section,
                f'variant = "{variant}"',
                f"benchmark_duration_seconds = {audio_seconds:.3f}",
            ]
        )
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
        duration_target = next(
            (
                i
                for i in range(start + 1, end)
                if lines[i].strip().startswith("benchmark_duration_seconds")
            ),
            None,
        )
        duration_line = f"benchmark_duration_seconds = {audio_seconds:.3f}"
        if duration_target is None:
            lines.insert(start + 2, duration_line)
        else:
            lines[duration_target] = duration_line
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
