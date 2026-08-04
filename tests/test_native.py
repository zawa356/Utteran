from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from utteran.native import (
    NativeBuilder,
    NativeBuildError,
    PrerequisiteCheck,
    default_native_dir,
    platform_key,
    probe_glslc,
    probe_openvino_gpu,
    probe_vulkan_runtime,
    resolve_native_dir,
    resolve_openvino_cmake_dir,
    resolve_openvino_runtime_dirs,
    resolve_runtime_library_dirs,
)


class FakeRunner:
    """Dispatch fake subprocess results to a per-test handler function."""

    def __init__(self, handler: Callable[[list[str]], subprocess.CompletedProcess[str]]) -> None:
        self._handler = handler
        self.commands: list[list[str]] = []

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: object = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        return self._handler(list(command))


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def _fail(stderr: str = "boom") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 1, stdout="", stderr=stderr)


def _touch_fake_cli(build_dir: Path) -> None:
    """Create a stand-in built executable at the layout _find_whisper_cli expects."""
    bin_dir = build_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "whisper-cli.exe").write_bytes(b"fake")


@pytest.fixture(autouse=True)
def _fake_cmake_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend cmake is installed without disturbing real lookups (e.g. git)."""
    import shutil as shutil_module

    real_which = shutil_module.which

    def fake_which(name: str, *args: object, **kwargs: object) -> str | None:
        if name in {"cmake", "cmake.exe"}:
            return "/fake/cmake"
        return real_which(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(shutil_module, "which", fake_which)


def test_resolve_native_dir_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UTTERAN_NATIVE_DIR", raising=False)
    assert resolve_native_dir() == default_native_dir()

    env_dir = tmp_path / "from-env"
    monkeypatch.setenv("UTTERAN_NATIVE_DIR", str(env_dir))
    assert resolve_native_dir() == env_dir

    explicit = tmp_path / "from-config"
    assert resolve_native_dir(explicit) == explicit


def test_default_native_dir_is_short_and_home_relative() -> None:
    path = default_native_dir()
    assert path == Path.home() / ".utteran" / "native"


def test_platform_key_is_stable_and_nonempty() -> None:
    key = platform_key()
    assert key
    assert "-" in key


def test_probe_glslc_reports_missing_shader_compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    check = probe_glslc()
    assert check.available is False
    assert check.detail is not None and "glslc" in check.detail


def test_probe_glslc_reports_available_when_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/glslc" if name == "glslc" else None)
    assert probe_glslc().available is True


def test_probe_vulkan_runtime_reports_missing_vulkaninfo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    check, device = probe_vulkan_runtime()
    assert check.available is False
    assert device is None


def test_probe_vulkan_runtime_parses_device_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/vulkaninfo" if "vulkaninfo" in name else None
    )
    runner = FakeRunner(lambda _cmd: _ok("deviceName        = Fake GPU\nother = 1\n"))

    check, device = probe_vulkan_runtime(runner)  # type: ignore[arg-type]

    assert check.available is True
    assert device == "Fake GPU"


def test_probe_openvino_gpu_unavailable_without_the_package() -> None:
    # The dev test environment does not install the optional `openvino` extra.
    check, device = probe_openvino_gpu()
    assert check.available is False
    assert device is None


def test_resolve_openvino_cmake_dir_none_without_the_package() -> None:
    assert resolve_openvino_cmake_dir() is None


def test_resolve_openvino_runtime_dirs_empty_without_the_package() -> None:
    assert resolve_openvino_runtime_dirs() == ()


def test_resolve_runtime_library_dirs_is_empty_for_cpu_and_vulkan() -> None:
    assert resolve_runtime_library_dirs("cpu") == ()
    assert resolve_runtime_library_dirs("vulkan") == ()


def _make_builder(tmp_path: Path, runner: FakeRunner) -> NativeBuilder:
    builder = NativeBuilder(tmp_path, runner=runner)  # type: ignore[arg-type]
    (builder.source_dir / ".git").mkdir(parents=True)
    return builder


def test_build_all_cpu_only_writes_a_manifest_without_baked_library_paths(
    tmp_path: Path,
) -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "--build" in command:
            build_dir = Path(command[command.index("--build") + 1])
            _touch_fake_cli(build_dir)
            return _ok()
        return _ok()

    runner = FakeRunner(handler)
    builder = _make_builder(tmp_path, runner)

    manifest = builder.build_all(variants=("cpu",))

    assert manifest["schema_version"] == 1
    assert manifest["whisper_cpp"]["commit"] == "f049fff95a089aa9969deb009cdd4892b3e74916"
    assert "cpu" in manifest["backends"]
    assert manifest["backends"]["cpu"]["cmake_flags"] == [
        "-DWHISPER_OPENVINO=OFF",
        "-DGGML_VULKAN=OFF",
    ]
    # No absolute OpenVINO/library directory is ever recorded in the manifest.
    assert "runtime_library_dirs" not in manifest["backends"]["cpu"]
    # Unrequested variants are neither built nor reported as failed.
    assert "openvino" not in manifest["errors"]
    assert "vulkan" not in manifest["errors"]


def test_openvino_manifest_replaces_profile_specific_cmake_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "--build" in command:
            _touch_fake_cli(Path(command[command.index("--build") + 1]))
        return _ok()

    profile_path = tmp_path / "win-intel" / "Lib" / "site-packages" / "openvino" / "cmake"
    monkeypatch.setattr(
        "utteran.native.probe_openvino_gpu",
        lambda: (PrerequisiteCheck(True, "GPU"), "GPU"),
    )
    monkeypatch.setattr("utteran.native.resolve_openvino_cmake_dir", lambda: profile_path)
    builder = _make_builder(tmp_path / "native", FakeRunner(handler))

    first = builder.build_all(variants=("openvino",))
    second = builder.build_all(variants=("openvino",))

    expected = [
        "-DWHISPER_OPENVINO=ON",
        "-DGGML_VULKAN=OFF",
        "-DOpenVINO_DIR=<resolved-at-build-time>",
    ]
    assert first["backends"]["openvino"]["cmake_flags"] == expected
    assert second["backends"]["openvino"]["cmake_flags"] == expected
    assert str(profile_path) not in json.dumps(second)


def test_partial_force_build_preserves_unrequested_manifest_entries(tmp_path: Path) -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "--build" in command:
            _touch_fake_cli(Path(command[command.index("--build") + 1]))
        return _ok()

    builder = _make_builder(tmp_path, FakeRunner(handler))
    builder.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    builder.manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "platform": builder.platform,
                "whisper_cpp": {
                    "tag": "v1.9.1",
                    "commit": "f049fff95a089aa9969deb009cdd4892b3e74916",
                },
                "backends": {
                    "vulkan": {
                        "executable": "shared/vulkan/whisper-cli.exe",
                        "cmake_flags": ["-DGGML_VULKAN=ON"],
                        "requires": ["vulkan"],
                    }
                },
                "errors": {"openvino": "previous prerequisite failure"},
            }
        ),
        encoding="utf-8",
    )

    manifest = builder.build_all(variants=("cpu",), force=True)

    assert set(manifest["backends"]) == {"cpu", "vulkan"}
    assert manifest["errors"] == {"openvino": "previous prerequisite failure"}


def test_build_all_records_prerequisite_failures_for_requested_gpu_variants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "--build" in command:
            build_dir = Path(command[command.index("--build") + 1])
            _touch_fake_cli(build_dir)
            return _ok()
        return _ok()

    # Force both GPU prerequisites unavailable regardless of this host's real
    # toolchain, so the test is deterministic on machines that do have a
    # Vulkan SDK / OpenVINO installed (as the Phase 3a investigation machine
    # does - see AISTATE.md I-3).
    monkeypatch.setattr("utteran.native.probe_glslc", lambda: PrerequisiteCheck(False, "no glslc"))
    monkeypatch.setattr(
        "utteran.native.probe_openvino_gpu", lambda: (PrerequisiteCheck(False, "no openvino"), None)
    )
    runner = FakeRunner(handler)
    builder = _make_builder(tmp_path, runner)

    manifest = builder.build_all(variants=("cpu", "openvino", "vulkan", "openvino_vulkan"))

    assert "openvino" in manifest["errors"]
    assert "vulkan" in manifest["errors"]
    assert "openvino_vulkan" in manifest["errors"]
    assert "cpu" in manifest["backends"]
    assert "openvino" not in manifest["backends"]
    assert "vulkan" not in manifest["backends"]
    assert "openvino_vulkan" not in manifest["backends"]


def test_build_variant_reuses_identical_prior_build_without_reinvoking_cmake(
    tmp_path: Path,
) -> None:
    calls: list[list[str]] = []

    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "--build" in command:
            build_dir = Path(command[command.index("--build") + 1])
            _touch_fake_cli(build_dir)
            return _ok()
        return _ok()

    runner = FakeRunner(handler)
    builder = _make_builder(tmp_path, runner)
    builder.build_all(variants=("cpu",))
    configure_and_build_calls_before = sum(1 for c in calls if "--build" in c or "-S" in c)

    builder.build_all(variants=("cpu",))
    configure_and_build_calls_after = sum(1 for c in calls if "--build" in c or "-S" in c)

    assert configure_and_build_calls_after == configure_and_build_calls_before


def test_build_variant_force_rebuilds_even_when_unchanged(tmp_path: Path) -> None:
    build_invocations = 0

    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal build_invocations
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "--build" in command:
            build_invocations += 1
            build_dir = Path(command[command.index("--build") + 1])
            _touch_fake_cli(build_dir)
            return _ok()
        return _ok()

    runner = FakeRunner(handler)
    builder = _make_builder(tmp_path, runner)
    builder.build_all(variants=("cpu",))
    builder.build_all(variants=("cpu",), force=True)

    assert build_invocations == 2


def test_build_variant_raises_native_build_error_on_configure_failure(tmp_path: Path) -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "-S" in command:
            return _fail("cmake configure exploded")
        return _ok()

    runner = FakeRunner(handler)
    builder = _make_builder(tmp_path, runner)

    manifest = builder.build_all(variants=("cpu",))

    assert "cpu" not in manifest["backends"]
    assert "cmake" in manifest["errors"]["cpu"] or "構成に失敗" in manifest["errors"]["cpu"]


def test_ensure_source_raises_when_git_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None if name == "git" else "/fake/cmake")
    runner = FakeRunner(lambda _cmd: _ok())
    builder = NativeBuilder(tmp_path, runner=runner)  # type: ignore[arg-type]

    with pytest.raises(NativeBuildError, match="git"):
        builder.build_all(variants=("cpu",))


def test_status_reports_runnable_backends_from_the_manifest(tmp_path: Path) -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "--build" in command:
            build_dir = Path(command[command.index("--build") + 1])
            _touch_fake_cli(build_dir)
            return _ok()
        return _ok()

    runner = FakeRunner(handler)
    builder = _make_builder(tmp_path, runner)
    builder.build_all(variants=("cpu",))

    status = builder.status()

    assert status["runnable"]["cpu"] is True


def test_clean_removes_one_variant_without_touching_others(tmp_path: Path) -> None:
    def handler(command: list[str]) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in command:
            return _ok("f049fff95a089aa9969deb009cdd4892b3e74916")
        if "--build" in command:
            build_dir = Path(command[command.index("--build") + 1])
            _touch_fake_cli(build_dir)
            return _ok()
        return _ok()

    runner = FakeRunner(handler)
    builder = _make_builder(tmp_path, runner)
    builder.build_all(variants=("cpu",))
    assert builder.build_dir("cpu").is_dir()

    builder.clean(variant="cpu")

    assert not builder.build_dir("cpu").is_dir()
    assert "cpu" not in builder.load_manifest().get("backends", {})
