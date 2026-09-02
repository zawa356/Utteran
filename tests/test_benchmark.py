from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from utteran.asr.base import ASRBackend
from utteran.benchmark import (
    BACKEND_REGISTRY,
    BENCHMARK_TARGET_ROUTES,
    SCORE_DISCLAIMER,
    SHORT_BENCHMARK_WARNING,
    BackendAdapter,
    BenchmarkMeasurement,
    BenchmarkResult,
    BenchmarkSample,
    BenchmarkTargetRoute,
    TargetAvailability,
    accuracy_score,
    aggregate,
    apply_target,
    apply_variant,
    benchmark_warning,
    character_error_rate,
    estimated_minutes_for_hour,
    load_run,
    markdown_report,
    new_run_payload,
    parse_durations,
    prepared_audio_lengths,
    resolve_benchmark_targets,
    resolve_legacy_variants,
    resolve_target,
    run_benchmark,
    save_run,
    speed_score,
    target_availability,
    wav_duration,
)
from utteran.config import Config
from utteran.devices import AcceleratorDevice, CTranslate2Report, DeviceReport
from utteran.errors import BackendUnavailableError


def _device_report(*, cuda_usable: bool) -> DeviceReport:
    cuda_devices = (
        (
            AcceleratorDevice(
                0,
                "test CUDA",
                8 * 1024**3,
                ("int8", "float32"),
                usable=True,
            ),
        )
        if cuda_usable
        else ()
    )
    return cast(
        DeviceReport,
        SimpleNamespace(
            ctranslate2=CTranslate2Report(
                True,
                "test",
                ("int8", "float32"),
                len(cuda_devices),
                cuda_devices,
            ),
            auto_selection=SimpleNamespace(
                asr_backend="faster-whisper",
                asr_device="cuda:0" if cuda_usable else "cpu",
                asr_compute_type="int8",
            ),
        ),
    )


def test_aggregate_uses_medians_and_peak() -> None:
    result = aggregate(
        "vulkan",
        [BenchmarkSample(1, 9, 100), BenchmarkSample(2, 18, 300), BenchmarkSample(1, 14, 200)],
        100,
    )
    assert result.median_total_seconds == 15
    assert result.median_load_seconds == 1
    assert result.realtime_factor == 100 / 15
    assert result.median_transcribe_seconds == 14
    assert result.speed_score_excluding_load == 714
    assert result.peak_ram_bytes == 300
    assert result.speed_score == 667
    assert result.hour_minutes == 9


def test_scores_use_rtf_and_japanese_cer() -> None:
    assert speed_score(7.8) == 780
    assert estimated_minutes_for_hour(7.8) == 8
    cer = character_error_rate("今日は、会議です。", "今日は会議でした")
    assert cer == 2 / 7
    assert accuracy_score(cer) == 71


def test_target_registry_rejects_invalid_combinations() -> None:
    target = resolve_target("whisper-cpp", "vulkan", "large-v3-turbo-q5_0")
    assert target.backend == "whisper-cpp"
    assert target.device == "vulkan"
    assert target.quantization == "q5_0"
    assert target.baseline is True
    with pytest.raises(ValueError, match="無効な組み合わせ"):
        resolve_target("faster-whisper", "vulkan", "large-v3-turbo")
    genai = resolve_target("openvino-genai", "npu", "large-v3-turbo-int8")
    assert genai.compute_type == "int8"
    assert genai.baseline is True


def test_every_benchmark_target_route_is_declared() -> None:
    assert BENCHMARK_TARGET_ROUTES == ("targets", "variants", "mode")


def test_legacy_variant_auto_uses_detected_cuda_instead_of_cpu() -> None:
    config = Config()
    target = resolve_legacy_variants(
        config, ("faster-whisper",), _device_report(cuda_usable=True)
    )[0]
    assert target.device == "cuda:0"
    assert target.compute_type == "int8"
    assert target.device_resolution is not None
    assert target.device_resolution.requested == "auto"
    assert target.device_resolution.source == "auto"
    assert "CTranslate2" in target.device_resolution.reason


def test_targets_route_auto_uses_the_same_device_resolver() -> None:
    target = resolve_target(
        "faster-whisper",
        "auto",
        "large-v3-turbo",
        report=_device_report(cuda_usable=True),
    )
    assert target.device == "cuda:0"
    assert target.device_resolution is not None
    assert target.device_resolution.source == "auto"


@pytest.mark.parametrize(
    ("route", "variants", "targets"),
    (
        ("variants", ("faster-whisper",), ()),
        ("targets", (), ("faster-whisper/auto/large-v3-turbo",)),
    ),
)
def test_user_routes_pass_through_shared_device_resolution(
    monkeypatch: pytest.MonkeyPatch,
    route: BenchmarkTargetRoute,
    variants: tuple[str, ...],
    targets: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        "utteran.benchmark.target_availability",
        lambda target, _report: TargetAvailability(target, "runnable", "test"),
    )
    availability = resolve_benchmark_targets(
        Config(),
        _device_report(cuda_usable=True),
        route,
        variants=variants,
        targets=targets,
    )
    assert availability[0].target.device == "cuda:0"
    assert availability[0].target.device_resolution is not None
    assert availability[0].target.device_resolution.source == "auto"


def test_explicit_device_is_not_replaced_and_unavailable_cuda_errors() -> None:
    cpu = resolve_target(
        "faster-whisper",
        "cpu",
        "large-v3-turbo",
        report=_device_report(cuda_usable=True),
    )
    assert cpu.device == "cpu"
    assert cpu.device_resolution is not None
    assert cpu.device_resolution.requested == "cpu"
    assert cpu.device_resolution.source == "explicit"
    with pytest.raises(BackendUnavailableError, match="自動フォールバックは行いません"):
        resolve_target(
            "faster-whisper",
            "cuda:0",
            "large-v3-turbo",
            report=_device_report(cuda_usable=False),
        )


def test_result_device_matches_backend_load_device_without_a_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded: list[tuple[str, str, str]] = []

    class FakeBackend:
        def load(self, model: str, device: str, compute_type: str) -> None:
            loaded.append((model, device, compute_type))

        def transcribe(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(segments=(SimpleNamespace(text="test"),))

        def unload(self) -> None:
            pass

    target = resolve_target(
        "faster-whisper",
        "auto",
        "large-v3-turbo",
        report=_device_report(cuda_usable=True),
    )
    monkeypatch.setattr("utteran.benchmark.wav_duration", lambda _path: 1.0)
    monkeypatch.setattr(
        "utteran.benchmark._installed_model", lambda _model, _backend: "installed-model"
    )
    monkeypatch.setitem(
        BACKEND_REGISTRY,
        "faster-whisper",
        BackendAdapter(
            ("cpu", "cuda"),
            lambda _config, _target: cast(ASRBackend, FakeBackend()),
        ),
    )
    clock = iter((0.0, 0.25, 1.0))
    results = run_benchmark(
        Config(),
        tmp_path / "unused.wav",
        targets=(target,),
        word_timestamps=False,
        repeat=1,
        warmup=0,
        clock=lambda: next(clock),
    )
    assert loaded == [("installed-model", "cuda:0", "int8")]
    result_target = cast(dict[str, object], results[0].as_dict()["target"])
    assert result_target["device"] == loaded[0][1]
    assert cast(dict[str, object], result_target["device_resolution"])["source"] == "auto"


def test_backend_unavailability_stops_instead_of_falling_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FailingBackend:
        def load(self, *_args: object) -> None:
            raise BackendUnavailableError("explicit device failed")

        def unload(self) -> None:
            pass

    target = resolve_target("whisper-cpp", "vulkan", "large-v3-turbo-q5_0")
    monkeypatch.setattr("utteran.benchmark.wav_duration", lambda _path: 1.0)
    monkeypatch.setattr(
        "utteran.benchmark._installed_model", lambda _model, _backend: "installed-model"
    )
    monkeypatch.setitem(
        BACKEND_REGISTRY,
        "whisper-cpp",
        BackendAdapter(
            ("cpu", "openvino", "vulkan", "openvino_vulkan"),
            lambda _config, _target: cast(ASRBackend, FailingBackend()),
        ),
    )
    with pytest.raises(BackendUnavailableError, match="explicit device failed"):
        run_benchmark(
            Config(),
            tmp_path / "unused.wav",
            targets=(target,),
            word_timestamps=False,
            repeat=1,
            warmup=0,
        )


def test_cuda_without_hardware_is_hidden_even_when_model_is_missing() -> None:
    target = resolve_target("faster-whisper", "cuda", "large-v3-turbo")
    report = SimpleNamespace(
        backends={"faster-whisper": True},
        ctranslate2=SimpleNamespace(
            cpu_status="completed", cuda_status="completed", cuda_devices=()
        ),
    )
    availability = target_availability(target, cast(DeviceReport, report))
    assert availability.state == "hidden"


def test_apply_variant_preserves_unrelated_config(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[general]\nlog_level = "debug"\n\n[asr.whisper_cpp]\nvariant = "cpu"\nthreads = 2\n',
        encoding="utf-8",
    )
    apply_variant(path, "vulkan", 900.0)
    text = path.read_text(encoding="utf-8")
    assert 'variant = "vulkan"' in text
    assert "benchmark_duration_seconds = 900.000" in text
    assert 'log_level = "debug"' in text
    assert "threads = 2" in text


def test_apply_target_records_provenance_and_remains_loadable(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    target = resolve_target("faster-whisper", "cpu", "large-v3-turbo")
    apply_target(path, target, 180.0, "2026-08-31T00:00:00+00:00")
    text = path.read_text(encoding="utf-8")
    assert 'backend = "faster-whisper"' in text
    assert 'target = "faster-whisper/cpu/large-v3-turbo"' in text
    assert Config.load(config_path=path).asr.device == "cpu"


def test_parse_durations_supports_multiple_lengths_and_full() -> None:
    assert parse_durations("180,900,full,180", 1200.0) == (180.0, 900.0, 1200.0)
    with pytest.raises(ValueError, match="超えています"):
        parse_durations("1201", 1200.0)


def test_short_benchmark_warning_records_phase3d_reversal() -> None:
    assert benchmark_warning(180.0) == SHORT_BENCHMARK_WARNING
    assert "順位逆転" in SHORT_BENCHMARK_WARNING
    assert benchmark_warning(900.0) is None


def test_prepared_audio_lengths_creates_prefix_and_reuses_full_audio(tmp_path: Path) -> None:
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(100)
        audio.writeframes(b"\0\0" * 1000)

    with prepared_audio_lengths(source, (3.0, 10.0)) as prepared:
        assert len(prepared) == 2
        assert prepared[0][0] == 3.0
        assert wav_duration(prepared[0][1]) == 3.0
        assert prepared[1] == (10.0, source)
        temporary = prepared[0][1]
        assert temporary.is_file()
    assert not temporary.exists()


def test_result_save_load_and_markdown_are_transcript_free(tmp_path: Path) -> None:
    target = resolve_target("faster-whisper", "cpu", "large-v3-turbo")
    result = BenchmarkResult(
        "faster-whisper",
        (BenchmarkSample(1.0, 9.0),),
        10.0,
        1.0,
        6.0,
        None,
        target,
        600,
        94,
        0.06,
        False,
    )
    measurement = BenchmarkMeasurement(60.0, benchmark_warning(60.0), (result,))
    payload = new_run_payload("standard", 60.0, {"utteran_version": "test"}, ())
    payload["status"] = "interrupted"
    payload["measurements"] = [measurement.as_dict()]
    path = tmp_path / "benchmark.json"
    save_run(path, payload)
    restored = load_run(path)
    assert restored["status"] == "interrupted"
    measurements = cast(list[dict[str, object]], restored["measurements"])
    results = cast(list[dict[str, object]], measurements[0]["results"])
    assert results[0]["speed_score"] == 600
    assert results[0]["speed_score_excluding_load"] == 667
    markdown = markdown_report(restored)
    assert "約10分" in markdown
    assert "600 / 667" in markdown
    assert SCORE_DISCLAIMER in markdown
    assert "transcript" not in path.read_text(encoding="utf-8")
