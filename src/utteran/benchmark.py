"""Job-free, backend-neutral ASR benchmarking."""

from __future__ import annotations

import json
import platform
import re
import statistics
import tempfile
import time
import unicodedata
import wave
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from platformdirs import user_log_dir

from utteran import __version__
from utteran.asr.base import ASRBackend
from utteran.asr.faster_whisper import FasterWhisperBackend
from utteran.asr.whisper_cpp import WhisperCppBackend
from utteran.config import Config
from utteran.devices import DeviceReport, detect_devices, device_probe_fingerprint
from utteran.errors import BackendUnavailableError, ModelNotFoundError
from utteran.logging import resolve_log_dir, runtime_logging
from utteran.models.catalog import ModelEntry, get_model, list_models
from utteran.models.manager import ModelManager
from utteran.models.openvino import OpenVINOManager
from utteran.types import ASROptions, CancelToken

RECOMMENDED_BENCHMARK_SECONDS = 900
BASELINE_MODEL = "large-v3-turbo"
BENCHMARK_SCHEMA_VERSION = 3
SCORE_DISCLAIMER = (
    "このスコアは目安です。音声の内容、長さ、同時に動作する他の処理により変動します。"
    "スコアが2倍でも、処理時間が半分になることを保証するものではありません。"
)
SHORT_BENCHMARK_WARNING = (
    "この長さの結果は長時間音声を代表しない可能性があります。Phase 3dでは180秒と"
    "24分46秒の素材でVulkan/OpenVINO+Vulkanの順位逆転が観測されました。"
)
AvailabilityState = Literal["runnable", "preparation", "hidden", "unknown"]
BenchmarkModeName = Literal["quick", "standard", "detailed"]


@dataclass(frozen=True)
class BenchmarkMode:
    name: BenchmarkModeName
    label: str
    durations: tuple[float | str, ...]
    repeat: int
    warmup: int
    accuracy: bool
    multiple_models: bool
    estimated_minutes: str


BENCHMARK_MODES: dict[BenchmarkModeName, BenchmarkMode] = {
    "quick": BenchmarkMode("quick", "簡易", (60.0,), 1, 0, False, False, "10〜20分"),
    "standard": BenchmarkMode("standard", "標準", (180.0,), 2, 1, True, False, "30〜60分"),
    "detailed": BenchmarkMode(
        "detailed", "詳細", (60.0, 180.0, 900.0, "full"), 3, 1, True, True, "数時間"
    ),
}


@dataclass(frozen=True)
class BenchmarkTarget:
    backend: str
    device: str
    model: str
    quantization: str | None
    compute_type: str

    @property
    def target_id(self) -> str:
        return f"{self.backend}/{self.device}/{self.model}"

    @property
    def baseline(self) -> bool:
        return model_family(self.model) == BASELINE_MODEL

    @property
    def legacy_variant(self) -> str:
        return "faster-whisper" if self.backend == "faster-whisper" else self.device


@dataclass(frozen=True)
class TargetAvailability:
    target: BenchmarkTarget
    state: AvailabilityState
    reason: str
    preparation: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BackendAdapter:
    devices: tuple[str, ...]
    factory: Callable[[Config, BenchmarkTarget], ASRBackend]


def _faster_factory(_config: Config, _target: BenchmarkTarget) -> ASRBackend:
    return FasterWhisperBackend()


def _cpp_factory(config: Config, target: BenchmarkTarget) -> ASRBackend:
    settings = config.asr.whisper_cpp.model_copy(update={"variant": target.device})
    return WhisperCppBackend(settings, allow_fallback=False)


BACKEND_REGISTRY = {
    "faster-whisper": BackendAdapter(("cpu", "cuda"), _faster_factory),
    "whisper-cpp": BackendAdapter(("cpu", "openvino", "vulkan", "openvino_vulkan"), _cpp_factory),
}


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
    target: BenchmarkTarget | None = None
    speed_score: int = 0
    accuracy_score: int | None = None
    character_error_rate: float | None = None
    word_timestamps: bool = False

    @property
    def hour_minutes(self) -> int:
        return estimated_minutes_for_hour(self.realtime_factor)

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["hour_audio_minutes"] = self.hour_minutes
        result["is_baseline_model"] = self.target.baseline if self.target else True
        return result


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


def speed_score(factor: float) -> int:
    return round(max(0.0, factor) * 100)


def estimated_minutes_for_hour(factor: float) -> int:
    if factor <= 0:
        raise ValueError("実時間比は0より大きくしてください")
    return max(1, round(60 / factor))


def normalize_accuracy_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def character_error_rate(reference: str, hypothesis: str) -> float:
    """CER is preferable to whitespace-dependent WER for Japanese."""
    expected, actual = normalize_accuracy_text(reference), normalize_accuracy_text(hypothesis)
    if not expected:
        raise ValueError("精度測定の正解テキストが空です")
    previous = list(range(len(actual) + 1))
    for row, left in enumerate(expected, 1):
        current = [row]
        for column, right in enumerate(actual, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left != right),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def accuracy_score(cer: float) -> int:
    return round(max(0.0, 1.0 - cer) * 100)


def aggregate(
    variant: str,
    samples: list[BenchmarkSample],
    audio_seconds: float,
    *,
    target: BenchmarkTarget | None = None,
    reference_text: str | None = None,
    hypothesis_text: str | None = None,
    word_timestamps: bool = False,
) -> BenchmarkResult:
    if not samples:
        raise ValueError("測定サンプルがありません")
    totals = [sample.total_seconds for sample in samples]
    median = statistics.median(totals)
    factor = audio_seconds / median
    peaks = [sample.peak_ram_bytes for sample in samples if sample.peak_ram_bytes is not None]
    cer = (
        character_error_rate(reference_text, hypothesis_text)
        if reference_text is not None and hypothesis_text is not None
        else None
    )
    return BenchmarkResult(
        variant,
        tuple(samples),
        median,
        statistics.median(sample.load_seconds for sample in samples),
        factor,
        max(peaks) if peaks else None,
        target,
        speed_score(factor),
        accuracy_score(cer) if cer is not None else None,
        cer,
        word_timestamps,
    )


def model_family(model: str) -> str:
    return re.sub(r"-q(?:5_0|5_1|8_0)$", "", model)


def _entry(model: str, backend: str) -> ModelEntry:
    try:
        return get_model(model, backend=backend)
    except Exception:
        matches = [
            item for item in list_models(backend=backend) if item.model_size == model_family(model)
        ]
        if not matches:
            raise ValueError(f"{backend}で利用できないモデルです: {model}") from None
        return matches[0]


def resolve_target(
    backend: str, device: str, model: str, *, compute_type: str = "auto"
) -> BenchmarkTarget:
    adapter = BACKEND_REGISTRY.get(backend)
    if adapter is None:
        raise ValueError(f"未対応のベンチマークバックエンドです: {backend}")
    kind = "cuda" if device.startswith("cuda:") else device
    if kind not in adapter.devices:
        raise ValueError(f"無効な組み合わせです: {backend} / {device}")
    entry = _entry(model, backend)
    return BenchmarkTarget(
        backend,
        device,
        entry.model_id,
        entry.quantization,
        compute_type if backend == "faster-whisper" else "ggml",
    )


def parse_target(value: str, config: Config) -> BenchmarkTarget:
    parts = value.split("/", 2)
    if len(parts) not in {2, 3}:
        raise ValueError("--targetsはbackend/device[/model]形式で指定してください")
    return resolve_target(
        parts[0],
        parts[1],
        parts[2] if len(parts) == 3 else config.asr.model,
        compute_type=config.asr.compute_type,
    )


def resolve_legacy_variants(config: Config, variants: Sequence[str]) -> tuple[BenchmarkTarget, ...]:
    targets = []
    for variant in variants:
        if variant == "faster-whisper":
            device = config.asr.device if config.asr.device != "auto" else "cpu"
            targets.append(
                resolve_target(
                    "faster-whisper", device, config.asr.model, compute_type=config.asr.compute_type
                )
            )
        else:
            targets.append(resolve_target("whisper-cpp", variant, config.asr.model))
    return tuple(targets)


def target_availability(
    target: BenchmarkTarget, report: DeviceReport, manager: ModelManager | None = None
) -> TargetAvailability:
    manager = manager or ModelManager()
    entry = _entry(target.model, target.backend)
    if report.backends.get(target.backend) is not True:
        return TargetAvailability(target, "preparation", "バックエンドが未導入です", "setup.ps1")
    if target.backend == "faster-whisper":
        status = report.ctranslate2.cpu_status
        if target.device != "cpu":
            status = report.ctranslate2.cuda_status
        if status in {"timeout", "error"}:
            return TargetAvailability(target, "unknown", "実行可否を判定できません")
        if target.device != "cpu" and not any(
            item.usable
            and (not target.device.startswith("cuda:") or item.index == int(target.device[5:]))
            for item in report.ctranslate2.cuda_devices
        ):
            return TargetAvailability(target, "hidden", "利用可能なCUDA GPUがありません")
        if manager.find_installed(entry)[0] is None:
            return TargetAvailability(
                target,
                "preparation",
                "モデルが未取得です",
                f"utteran models download {entry.key}",
            )
        return TargetAvailability(target, "runnable", "CTranslate2で実行できます")
    if target.device in {"vulkan", "openvino_vulkan"}:
        if report.vulkan.status in {"timeout", "error"}:
            return TargetAvailability(target, "unknown", "Vulkanを判定できません")
        if not report.vulkan.runtime_available:
            return TargetAvailability(target, "hidden", "利用可能なVulkan GPUがありません")
    if target.device in {"openvino", "openvino_vulkan"}:
        if report.openvino.status in {"timeout", "error"}:
            return TargetAvailability(target, "unknown", "OpenVINOを判定できません")
        if not report.openvino.available:
            return TargetAvailability(
                target, "preparation", "OpenVINOが未導入です", "setup.ps1 -Profile intel"
            )
    if manager.find_installed(entry)[0] is None:
        return TargetAvailability(
            target, "preparation", "モデルが未取得です", f"utteran models download {entry.key}"
        )
    if not report.native.variants.get(target.device, False):
        return TargetAvailability(
            target,
            "preparation",
            "ネイティブ構成が未ビルドです",
            f"utteran native build --variant {target.device}",
        )
    if target.device in {"openvino", "openvino_vulkan"}:
        ir_status = next(
            (
                item
                for item in OpenVINOManager(manager).list()
                if item.model_size == entry.model_size
            ),
            None,
        )
        if ir_status is None or not ir_status.installed:
            return TargetAvailability(
                target,
                "preparation",
                "OpenVINO encoder IRが未生成です",
                f"utteran models prepare-openvino {entry.key} --device GPU",
            )
    return TargetAvailability(target, "runnable", "実行できます")


def discover_targets(
    config: Config,
    report: DeviceReport,
    *,
    multiple_models: bool = False,
    manager: ModelManager | None = None,
) -> tuple[TargetAvailability, ...]:
    manager = manager or ModelManager()
    rows = []
    for backend, adapter in BACKEND_REGISTRY.items():
        entries = list(list_models(backend=backend))
        if not multiple_models:
            entries = [item for item in entries if model_family(item.model_id) == BASELINE_MODEL][
                :1
            ]
        for entry in entries:
            for device in adapter.devices:
                row = target_availability(
                    resolve_target(
                        backend, device, entry.model_id, compute_type=config.asr.compute_type
                    ),
                    report,
                    manager,
                )
                if row.state != "hidden":
                    rows.append(row)
    return tuple(rows)


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            return audio.getnframes() / audio.getframerate()
    except (OSError, wave.Error, ZeroDivisionError) as exc:
        raise ValueError("benchmarkの入力はデコード可能なWAVを指定してください") from exc


def parse_durations(value: str, full_duration: float) -> tuple[float, ...]:
    durations: list[float] = []
    for token in (item.strip().casefold() for item in value.split(",")):
        if not token:
            continue
        try:
            duration = full_duration if token == "full" else float(token)
        except ValueError as exc:
            raise ValueError("--durationsは秒数またはfullをカンマ区切りで指定してください") from exc
        if duration <= 0:
            raise ValueError("--durationsの秒数は0より大きくしてください")
        if duration > full_duration + 0.001:
            raise ValueError(
                f"測定長{duration:g}秒が入力WAVの長さ{full_duration:.3f}秒を超えています"
            )
        duration = min(duration, full_duration)
        if not any(abs(item - duration) < 0.001 for item in durations):
            durations.append(duration)
    if not durations:
        raise ValueError("--durationsに1つ以上の秒数またはfullを指定してください")
    return tuple(durations)


def mode_durations(mode: BenchmarkMode, full: float) -> tuple[float, ...]:
    values = [item for item in mode.durations if item == "full" or float(item) <= full + 0.001]
    return parse_durations(",".join(map(str, values or ["full"])), full)


def benchmark_warning(seconds: float) -> str | None:
    return SHORT_BENCHMARK_WARNING if seconds < RECOMMENDED_BENCHMARK_SECONDS else None


@contextmanager
def prepared_audio_lengths(
    source: Path, durations: tuple[float, ...]
) -> Iterator[list[tuple[float, Path]]]:
    full = wav_duration(source)
    with tempfile.TemporaryDirectory(prefix="utteran-benchmark-") as temporary:
        prepared = []
        for index, duration in enumerate(durations):
            if abs(duration - full) < 0.001:
                prepared.append((full, source))
            else:
                target = Path(temporary) / f"duration-{index}.wav"
                prepared.append((_copy_wav_prefix(source, target, duration), target))
        yield prepared


def _copy_wav_prefix(source: Path, target: Path, duration: float) -> float:
    try:
        with wave.open(str(source), "rb") as reader, wave.open(str(target), "wb") as writer:
            writer.setparams(reader.getparams())
            rate = reader.getframerate()
            remaining = min(reader.getnframes(), round(duration * rate))
            total = remaining
            while remaining:
                count = min(remaining, rate * 60)
                data = reader.readframes(count)
                if not data:
                    break
                writer.writeframes(data)
                remaining -= count
            return total / rate
    except (OSError, wave.Error, ZeroDivisionError) as exc:
        raise ValueError("benchmarkの入力はデコード可能なWAVを指定してください") from exc


def _installed_model(model: str, backend: str) -> str:
    manager = ModelManager()
    preferred = sorted(
        list_models(backend=backend),
        key=lambda item: (item.model_id != model, not item.model_id.startswith(model)),
    )
    for entry in preferred:
        if manager.find_installed(entry)[0] is not None:
            return entry.model_id
    raise ModelNotFoundError(f"{backend}の導入済みモデルがありません")


def run_benchmark(
    config: Config,
    audio: Path,
    variants: tuple[str, ...] = (),
    *,
    targets: Sequence[BenchmarkTarget] | None = None,
    word_timestamps: bool,
    repeat: int,
    warmup: int,
    reference_text: str | None = None,
    cancel: CancelToken | None = None,
    result_callback: Callable[[BenchmarkResult], None] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> list[BenchmarkResult]:
    duration = wav_duration(audio)
    selected = tuple(targets) if targets is not None else resolve_legacy_variants(config, variants)
    results = []
    for target in selected:
        samples, hypothesis = [], None
        try:
            model = _installed_model(target.model, target.backend)
            for index in range(warmup + repeat):
                if cancel:
                    cancel.raise_if_cancelled()
                backend = BACKEND_REGISTRY[target.backend].factory(config, target)
                started = clock()
                try:
                    backend.load(model, target.device, target.compute_type)
                    loaded = clock()
                    transcription = backend.transcribe(
                        audio,
                        ASROptions(
                            language=config.asr.language,
                            initial_prompt=config.asr.initial_prompt,
                            vad_filter=config.asr.vad_filter,
                            beam_size=config.asr.beam_size,
                            condition_on_previous_text=config.asr.condition_on_previous_text,
                            word_timestamps=word_timestamps,
                        ),
                        cancel=cancel,
                    )
                    finished = clock()
                finally:
                    backend.unload()
                if index >= warmup:
                    samples.append(BenchmarkSample(loaded - started, finished - loaded))
                    hypothesis = "".join(segment.text for segment in transcription.segments)
        except (BackendUnavailableError, ModelNotFoundError):
            continue
        result = aggregate(
            target.legacy_variant,
            samples,
            duration,
            target=target,
            reference_text=reference_text,
            hypothesis_text=hypothesis,
            word_timestamps=word_timestamps,
        )
        results.append(result)
        if result_callback:
            result_callback(result)
    return results


def detect_benchmark_environment(config: Config) -> DeviceReport:
    return detect_devices(
        config.ffmpeg.path,
        venv_dir=config.general.venv_dir,
        native_dir=config.general.native_dir,
        probe_timeout_seconds=config.general.device_probe_timeout_seconds,
    )


def collect_environment(report: DeviceReport) -> dict[str, object]:
    return {
        "utteran_version": __version__,
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "cpu": asdict(report.cpu),
        "cuda": [item.name for item in report.ctranslate2.cuda_devices if item.usable],
        "vulkan": report.vulkan.runtime_device,
        "openvino_devices": list(report.openvino.values),
        "ctranslate2_version": report.ctranslate2.version,
        "whisper_cpp_tag": report.native.whisper_cpp_tag,
        "device_fingerprint": device_probe_fingerprint(),
    }


def default_result_dir(config: Config) -> Path:
    runtime = runtime_logging()
    if runtime:
        return runtime.log_dir / "benchmarks"
    selected, _, _ = resolve_log_dir(
        config.general.log_dir, fallback_dir=Path(user_log_dir("utteran"))
    )
    return selected / "benchmarks"


def new_run_payload(
    mode: BenchmarkModeName,
    source_duration: float,
    environment: Mapping[str, object],
    availability: Sequence[TargetAvailability],
) -> dict[str, object]:
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "mode": mode,
        "source_audio_duration_seconds": source_duration,
        "environment": dict(environment),
        "availability": [item.as_dict() for item in availability],
        "measurements": [],
        "recommendation": None,
        "disclaimer": SCORE_DISCLAIMER,
    }


def save_run(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    staged.replace(path)


def load_run(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("measurements"), list):
        raise ValueError("ベンチマーク結果JSONの形式が不正です")
    return value


def latest_run(
    directory: Path, exclude: Path | None = None
) -> tuple[Path, dict[str, object]] | None:
    for path in sorted(directory.glob("benchmark-*.json"), reverse=True):
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        try:
            return path, load_run(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return None


def version_changed(previous: Mapping[str, object]) -> bool:
    environment = previous.get("environment")
    return not isinstance(environment, Mapping) or environment.get("utteran_version") != __version__


def recommend(measurements: Sequence[BenchmarkMeasurement]) -> BenchmarkResult | None:
    if not measurements:
        return None
    longest = max(measurements, key=lambda item: item.audio_duration_seconds)
    candidates = [
        result for result in longest.results if result.target is None or result.target.baseline
    ]
    candidates = candidates or list(longest.results)
    return max(
        candidates,
        key=lambda item: (item.speed_score, item.accuracy_score or -1),
        default=None,
    )


def markdown_report(payload: Mapping[str, object]) -> str:
    lines = [
        "# utteran benchmark",
        "",
        "| 音声長 | 構成 | モデル | 速度スコア | 1時間換算 | 精度スコア | CER |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    measurements = payload.get("measurements", [])
    if isinstance(measurements, list):
        for measurement in measurements:
            if not isinstance(measurement, dict):
                continue
            for result in measurement.get("results", []):
                target = result.get("target") or {}
                cer = result.get("character_error_rate")
                accuracy = result.get("accuracy_score")
                accuracy_label = accuracy if accuracy is not None else "-"
                cer_label = f"{cer * 100:.1f}%" if cer is not None else "-"
                lines.append(
                    f"| {measurement['audio_duration_seconds']:.1f}秒 | "
                    f"{target.get('backend', '')} / "
                    f"{target.get('device', result['variant'])} | {target.get('model', '')} | "
                    f"{result['speed_score']} | 約{result['hour_audio_minutes']}分 | "
                    f"{accuracy_label} | {cer_label} |"
                )
    return "\n".join([*lines, "", SCORE_DISCLAIMER, ""])


def _set_value(lines: list[str], section: str, key: str, value: str) -> None:
    try:
        start = lines.index(section)
    except ValueError:
        if lines and lines[-1]:
            lines.append("")
        lines.extend([section, f"{key} = {value}"])
        return
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    index = next(
        (i for i in range(start + 1, end) if lines[i].strip().startswith(f"{key} =")), None
    )
    if index is None:
        lines.insert(end, f"{key} = {value}")
    else:
        lines[index] = f"{key} = {value}"


def apply_target(path: Path, target: BenchmarkTarget, duration: float, measured_at: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for key, value in (
        ("backend", target.backend),
        ("model", target.model),
        ("device", target.device),
    ):
        _set_value(lines, "[asr]", key, json.dumps(value))
    if target.backend == "whisper-cpp":
        _set_value(lines, "[asr.whisper_cpp]", "variant", json.dumps(target.device))
    _set_value(lines, "[benchmark]", "target", json.dumps(target.target_id))
    _set_value(lines, "[benchmark]", "audio_duration_seconds", f"{duration:.3f}")
    _set_value(lines, "[benchmark]", "measured_at", json.dumps(measured_at))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def apply_variant(path: Path, variant: str, audio_seconds: float) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    _set_value(lines, "[asr.whisper_cpp]", "variant", json.dumps(variant))
    _set_value(lines, "[asr.whisper_cpp]", "benchmark_duration_seconds", f"{audio_seconds:.3f}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
