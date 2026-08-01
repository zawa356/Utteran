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
    OptionalRuntimeReport,
    TorchReport,
    detect_devices,
    select_faster_whisper_device,
)
from utteran.errors import BackendUnavailableError


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
    )

    report = detect_devices(probes=probes)
    encoded = json.dumps(report.to_dict(), ensure_ascii=False)

    assert report.auto_selection.asr_device == "cpu"
    assert report.auto_selection.asr_compute_type == "int8"
    assert not report.ctranslate2.cuda_devices[0].usable
    assert any("Intel GPU / NPU" in warning for warning in report.warnings)
    assert any("CUDA デバイス" in warning for warning in report.warnings)
    assert '"logical_cores": 8' in encoded
