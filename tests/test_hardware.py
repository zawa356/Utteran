from __future__ import annotations

from pathlib import Path

from utteran_gui.hardware import (
    DiskReport,
    GpuAdapter,
    GpuReport,
    HardwareProbeSet,
    MemoryReport,
    RuntimeCapabilities,
    detect_hardware,
    recommend_profile,
)


def _gpu(vendor: str, error: str | None = None) -> GpuReport:
    adapters = () if vendor == "none" else (GpuAdapter(name=f"{vendor} card", vendor=vendor),)  # type: ignore[arg-type]
    return GpuReport(adapters, vendor, error=error)  # type: ignore[arg-type]


def test_nvidia_gpu_recommends_cuda_with_both_backends_accelerated() -> None:
    recommendation = recommend_profile(_gpu("nvidia"))
    assert recommendation.recommended == "cuda"
    assert recommendation.detection_confident is False
    cuda = next(item for item in recommendation.alternatives if item.profile == "cuda")
    assert cuda.asr_accelerated is True
    assert cuda.diarization_accelerated is True
    assert {item.profile for item in recommendation.alternatives} == {"cuda", "cpu"}


def test_intel_gpu_recommends_intel_with_diarization_on_xpu() -> None:
    recommendation = recommend_profile(_gpu("intel"))
    assert recommendation.recommended == "intel"
    intel = next(item for item in recommendation.alternatives if item.profile == "intel")
    assert intel.asr_accelerated is True
    assert intel.diarization_accelerated is True


def test_intel_openvino_available_xpu_unavailable_is_explained() -> None:
    runtime = RuntimeCapabilities("intel", False, False, True, False, False)

    recommendation = recommend_profile(_gpu("intel"), runtime)

    assert recommendation.recommended == "intel"
    intel = next(item for item in recommendation.alternatives if item.profile == "intel")
    assert intel.asr_accelerated is True
    assert intel.diarization_accelerated is False
    assert any(
        "ASRはGPU" in reason and "話者分離はCPU" in reason for reason in recommendation.reasons
    )


def test_intel_xpu_timeout_is_reported_as_unknown_not_unavailable() -> None:
    runtime = RuntimeCapabilities("intel", False, False, True, None, False)

    recommendation = recommend_profile(_gpu("intel"), runtime)

    assert recommendation.detection_confident is False
    assert any(
        "PyTorch XPU" in reason and "判定" in reason and "できません" in reason
        for reason in recommendation.reasons
    )


def test_amd_gpu_recommends_vulkan_with_diarization_on_cpu_only() -> None:
    recommendation = recommend_profile(_gpu("amd"))
    assert recommendation.recommended == "vulkan"
    vulkan = next(item for item in recommendation.alternatives if item.profile == "vulkan")
    assert vulkan.asr_accelerated is True
    assert vulkan.diarization_accelerated is False
    assert vulkan.caveat is not None


def test_no_gpu_recommends_cpu_only() -> None:
    recommendation = recommend_profile(_gpu("none"))
    assert recommendation.recommended == "cpu"
    assert [item.profile for item in recommendation.alternatives] == ["cpu"]


def test_failed_detection_offers_only_cpu_and_is_not_confident() -> None:
    recommendation = recommend_profile(_gpu("none", error="unsupported_os"))
    assert recommendation.detection_confident is False
    assert recommendation.recommended == "cpu"
    assert [item.profile for item in recommendation.alternatives] == ["cpu"]


def test_cuda_never_offered_without_an_nvidia_gpu() -> None:
    for vendor in ("intel", "amd", "none"):
        recommendation = recommend_profile(_gpu(vendor))
        assert "cuda" not in {item.profile for item in recommendation.alternatives}


def test_detect_hardware_uses_injected_probes_and_is_json_serializable(tmp_path: Path) -> None:
    probes = HardwareProbeSet(
        gpu=lambda: _gpu("nvidia"),
        memory=lambda: MemoryReport(total_bytes=16 * 1024**3, available_bytes=8 * 1024**3),
        disk=lambda _path: DiskReport(free_bytes=100 * 1024**3),
    )
    snapshot = detect_hardware(tmp_path, probes=probes)
    payload = snapshot.to_dict()
    assert payload["gpu"]["dominant_vendor"] == "nvidia"
    assert payload["memory"]["total_bytes"] == 16 * 1024**3
    assert payload["disk"]["free_bytes"] == 100 * 1024**3
    assert payload["recommendation"]["recommended"] == "cuda"
