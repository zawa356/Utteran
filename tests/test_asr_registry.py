from __future__ import annotations

from types import SimpleNamespace

import pytest

from utteran.asr.registry import create_asr_backend
from utteran.asr.whisper_cpp import WhisperCppBackend
from utteran.config import Config


@pytest.mark.parametrize(
    ("variant", "fallback_allowed"),
    [("openvino_vulkan", False), ("auto", True)],
)
def test_registry_preserves_variant_fallback_policy(
    monkeypatch: pytest.MonkeyPatch, variant: str, fallback_allowed: bool
) -> None:
    report = SimpleNamespace(
        auto_selection=SimpleNamespace(asr_backend="whisper-cpp", asr_device="openvino_vulkan")
    )
    monkeypatch.setattr("utteran.devices.detect_devices", lambda *args, **kwargs: report)
    config = Config.model_validate({"asr": {"whisper_cpp": {"variant": variant}}})

    backend = create_asr_backend("auto", config)

    assert isinstance(backend, WhisperCppBackend)
    assert backend.settings.variant == variant
    assert backend._allow_fallback is fallback_allowed
