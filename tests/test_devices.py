from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import utteran.devices as device_module
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
    run_isolated_probe,
    select_faster_whisper_device,
    suppress_torch_import,
)
from utteran.errors import BackendUnavailableError
from utteran.profiles import venv_dir_name


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


def test_auto_prefers_vulkan_when_cuda_is_unavailable(tmp_path: Path) -> None:
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
        "vulkan",
    )
    assert selected.diarization_device == "xpu:0"
    assert any("XPU" in note for note in selected.notes)


def test_detect_profile_report_reflects_current_and_created_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UTTERAN_PROFILE", "cpu")
    (tmp_path / venv_dir_name("cpu")).mkdir()

    report = detect_profile_report(tmp_path)

    assert report.current == "cpu"
    by_name = {item.name: item for item in report.profiles}
    assert set(by_name) == {"cpu", "cuda", "intel", "vulkan"}
    assert by_name["cpu"].exists is True
    assert by_name["cuda"].exists is False


def test_detect_vulkan_reports_build_and_runtime_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = device_module.ProbeOutcome("vulkan", "Vulkan", "completed", 0.1)
    monkeypatch.setattr(
        device_module,
        "run_isolated_probe",
        lambda *args, **kwargs: device_module._ProbeRun(
            outcome,
            {
                "build_available": False,
                "build_error": "no glslc",
                "runtime_available": True,
                "runtime_device": "Fake GPU",
                "runtime_error": None,
            },
        ),
    )

    report = detect_vulkan()

    assert report.build_available is False
    assert report.build_error == "no glslc"
    assert report.runtime_available is True
    assert report.runtime_device == "Fake GPU"


def test_timed_out_probe_is_unknown_and_never_available() -> None:
    timeout = device_module._ProbeRun(
        device_module.ProbeOutcome(
            "torch_xpu", "PyTorch XPU", "timeout", 0.1, "判定不能"
        ),
        None,
    )
    completed = device_module._ProbeRun(
        device_module.ProbeOutcome("torch_cuda", "PyTorch CUDA", "completed", 0.1),
        {"version": "test", "devices": []},
    )

    report = device_module._combine_torch(True, completed, timeout)

    assert report.xpu_status == "timeout"
    assert report.xpu_available is False
    assert report.xpu_devices == ()


def test_suppress_torch_import_installs_and_removes_a_stand_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "torch", raising=False)

    with suppress_torch_import() as installed:
        assert installed is True
        assert "torch" in sys.modules
        import torch

        assert torch.__name__ == "torch"

    assert "torch" not in sys.modules


def test_suppress_torch_import_leaves_a_real_import_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_module = type(sys)("torch")
    monkeypatch.setitem(sys.modules, "torch", real_module)

    with suppress_torch_import() as installed:
        assert installed is False
        assert sys.modules["torch"] is real_module

    assert sys.modules["torch"] is real_module


def test_probe_cache_round_trip_and_hardware_invalidation(tmp_path: Path) -> None:
    reports = device_module._IsolatedReports(
        CTranslate2Report(True, "test", ("int8",), 0, ()),
        TorchReport(True, "test", False, (), xpu_status="timeout"),
        OptionalRuntimeReport(True, ("CPU", "GPU")),
        OptionalRuntimeReport(True, ("CPUExecutionProvider",)),
        VulkanReport(False, "missing", False, None, "missing"),
        (
            device_module.ProbeOutcome(
                "torch_xpu", "PyTorch XPU", "timeout", 20.0, "判定不能"
            ),
        ),
    )
    path = tmp_path / "devices.json"

    device_module._save_probe_cache(path, "hardware-a", reports)
    loaded = device_module._load_probe_cache(path, "hardware-a")

    assert loaded is not None
    assert loaded.torch.xpu_status == "timeout"
    assert loaded.outcomes[0].status == "timeout"
    assert device_module._load_probe_cache(path, "hardware-b") is None


def test_timeout_kills_probe_process_tree(tmp_path: Path) -> None:
    child_pid = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid)); "
        "time.sleep(60)"
    )

    run = run_isolated_probe(
        "hang",
        "hanging process tree",
        0.5,
        command=[sys.executable, "-c", script],
    )

    assert run.outcome.status == "timeout"
    assert child_pid.is_file()
    pid = int(child_pid.read_text())
    deadline = time.monotonic() + 5.0
    while _pid_is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _pid_is_alive(pid)


def _pid_is_alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_detect_native_report_reflects_manifest_state(tmp_path: Path) -> None:
    never_built = detect_native_report(tmp_path / "empty-native")
    assert never_built.built is False
    assert never_built.whisper_cpp_tag is None
    assert all(value is False for value in never_built.variants.values())
