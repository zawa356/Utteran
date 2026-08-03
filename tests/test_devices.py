from __future__ import annotations

import json
from pathlib import Path

import pytest

from utteran.devices import (
    AcceleratorDevice,
    CPUReport,
    CTranslate2Report,
    DeviceProbeSet,
    FfmpegReport,
    LibraryReport,
    NativeReport,
    OptionalRuntimeReport,
    ProfileReport,
    TorchReport,
    VulkanReport,
    detect_devices,
    detect_native_report,
    detect_profile_report,
    detect_vulkan,
    select_faster_whisper_device,
)
from utteran.errors import BackendUnavailableError
from utteran.native import PrerequisiteCheck as NativePrerequisiteCheck


def test_auto_compute_type_uses_supported_cuda_fallback() -> None:
    report = CTranslate2Report(
        True,
        "test",
        ("int8", "float32"),
        1,
        (
            AcceleratorDevice(
                0,
                "GPU",
                8 * 1024**3,
                ("int8", "int8_float16"),
                True,
            ),
        ),
    )

    selection = select_faster_whisper_device("auto", "auto", report=report)

    assert selection.device == "cuda"
    assert selection.compute_type == "int8_float16"
    assert selection.note is not None and "float16" in selection.note


def test_explicit_device_or_compute_type_never_falls_back() -> None:
    report = CTranslate2Report(
        True,
        "test",
        ("int8",),
        1,
        (AcceleratorDevice(0, "GPU", None, error="missing cuDNN"),),
    )

    with pytest.raises(BackendUnavailableError, match="フォールバックは行いません"):
        select_faster_whisper_device("cuda:0", "auto", report=report)
    with pytest.raises(BackendUnavailableError, match="対応していません"):
        select_faster_whisper_device("cpu", "float16", report=report)


def test_device_detection_is_injectable_and_json_serializable(tmp_path: Path) -> None:
    probes = DeviceProbeSet(
        cpu=lambda: CPUReport(8, 4, True, False),
        ctranslate2=lambda: CTranslate2Report(
            True,
            "test",
            ("int8",),
            1,
            (AcceleratorDevice(0, "GPU", None, ("int8",), True),),
        ),
        libraries=lambda: LibraryReport(None, None),
        torch=lambda: TorchReport(True, "test", False, ()),
        openvino=lambda: OptionalRuntimeReport(True, ("CPU", "GPU.0", "NPU")),
        onnxruntime=lambda: OptionalRuntimeReport(
            True,
            ("CPUExecutionProvider",),
        ),
        ffmpeg=lambda _path: FfmpegReport(True, str(tmp_path / "ffmpeg"), "ffmpeg test"),
        backends=lambda: {
            "faster-whisper": True,
            "pyannote": True,
            "openvino": True,
            "sherpa-onnx": False,
        },
        profile=lambda: ProfileReport(current="cpu", profiles=()),
        vulkan=lambda: VulkanReport(True, None, True, "Fake GPU", None),
        native=lambda: NativeReport(built=True, whisper_cpp_tag="v1.9.1", variants={"cpu": True}),
    )

    report = detect_devices(probes=probes)
    encoded = json.dumps(report.to_dict(), ensure_ascii=False)

    assert report.auto_selection.asr_device == "cpu"
    assert report.auto_selection.asr_compute_type == "int8"
    assert not report.ctranslate2.cuda_devices[0].usable
    assert any("Intel GPU / NPU" in warning for warning in report.warnings)
    assert any("CUDA デバイス" in warning for warning in report.warnings)
    assert '"logical_cores": 8' in encoded


def test_auto_prefers_openvino_vulkan_when_cuda_is_unavailable(tmp_path: Path) -> None:
    probes = DeviceProbeSet(
        cpu=lambda: CPUReport(8, 4, True, False),
        ctranslate2=lambda: CTranslate2Report(True, "test", ("int8",), 0, ()),
        libraries=lambda: LibraryReport(None, None),
        torch=lambda: TorchReport(
            True,
            "test+xpu",
            False,
            (),
            xpu_available=True,
            xpu_devices=(AcceleratorDevice(0, "Arc", 32 * 1024**3, usable=True),),
        ),
        openvino=lambda: OptionalRuntimeReport(True, ("CPU", "GPU.0")),
        onnxruntime=lambda: OptionalRuntimeReport(False, ()),
        ffmpeg=lambda _path: FfmpegReport(True, str(tmp_path / "ffmpeg"), "test"),
        backends=lambda: {"faster-whisper": True, "openvino": True},
        profile=lambda: ProfileReport(current="intel", profiles=()),
        vulkan=lambda: VulkanReport(True, None, True, "Arc", None),
        native=lambda: NativeReport(
            True,
            "v1.9.1",
            {"cpu": True, "openvino": True, "vulkan": True, "openvino_vulkan": True},
        ),
    )

    selected = detect_devices(probes=probes).auto_selection

    assert (selected.asr_backend, selected.asr_device) == (
        "whisper-cpp",
        "openvino_vulkan",
    )
    assert selected.diarization_device == "xpu:0"
    assert any("XPU" in note for note in selected.notes)


def test_detect_profile_report_reflects_current_and_created_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UTTERAN_PROFILE", "cpu")
    (tmp_path / "win-cpu").mkdir()

    report = detect_profile_report(tmp_path)

    assert report.current == "cpu"
    by_name = {item.name: item for item in report.profiles}
    assert set(by_name) == {"cpu", "cuda", "intel", "vulkan"}
    assert by_name["cpu"].exists is True
    assert by_name["cuda"].exists is False


def test_detect_vulkan_reports_build_and_runtime_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "utteran.native.probe_glslc", lambda: NativePrerequisiteCheck(False, "no glslc")
    )
    monkeypatch.setattr(
        "utteran.native.probe_vulkan_runtime",
        lambda *args, **kwargs: (NativePrerequisiteCheck(True, None), "Fake GPU"),
    )

    report = detect_vulkan()

    assert report.build_available is False
    assert report.build_error == "no glslc"
    assert report.runtime_available is True
    assert report.runtime_device == "Fake GPU"


def test_detect_native_report_reflects_manifest_state(tmp_path: Path) -> None:
    never_built = detect_native_report(tmp_path / "empty-native")
    assert never_built.built is False
    assert never_built.whisper_cpp_tag is None
    assert all(value is False for value in never_built.variants.values())
