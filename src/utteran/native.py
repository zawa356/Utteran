"""whisper.cpp native build orchestration (Phase 3a infrastructure only).

This module fetches whisper.cpp at a pinned tag/commit and builds one or more
CMake configurations (`cpu`, `openvino`, `vulkan`, `openvino_vulkan`) beside
it. Phase 3a stops at "the build succeeds and a manifest is recorded" -
actually invoking the resulting `whisper-cli` for transcription is Phase 3b.

Design notes (see `AISTATE.md` Step 0 / Step 5 for the investigation behind
these choices):

- Build artifacts are shared across all profile venvs (the whisper.cpp binary
  itself does not depend on torch), so they live under one OS-keyed
  `native_dir`, independent of `profiles.py`'s per-profile venv layout.
- The manifest never stores the OpenVINO runtime DLL directory. That
  directory lives inside whichever venv's `site-packages/openvino/libs` is
  currently active, and differs per profile; baking one profile's path in
  would break every other profile. `resolve_runtime_library_dirs()` looks
  this up fresh from the *current* environment at call time instead.
- CMake is invoked with the multi-config "Visual Studio 17 2022" generator
  on Windows rather than Ninja, specifically so a build can run from a plain
  PowerShell/cmd session: this generator locates the MSVC toolchain itself
  (via the same mechanism as `vswhere`), unlike Ninja, which requires the
  compiler environment (`vcvars64.bat`) to already be sourced in the calling
  process. Verified against this whisper.cpp checkout (v1.9.1) for the
  `cpu` configuration.
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utteran.errors import DependencyError

MANIFEST_SCHEMA_VERSION = 1
_OPENVINO_DIR_FLAG = "-DOpenVINO_DIR="


def _portable_cmake_flags(flags: Sequence[str]) -> tuple[str, ...]:
    """Remove profile-specific paths before comparing or persisting flags."""
    return tuple(
        f"{_OPENVINO_DIR_FLAG}<resolved-at-build-time>"
        if flag.startswith(_OPENVINO_DIR_FLAG)
        else flag
        for flag in flags
    )


WHISPER_CPP_TAG = "v1.9.1"
WHISPER_CPP_COMMIT = "f049fff95a089aa9969deb009cdd4892b3e74916"
WHISPER_CPP_REPOSITORY = "https://github.com/ggml-org/whisper.cpp.git"

#: Public backend name -> short on-disk build-directory slug. Kept short
#: because ggml's Vulkan shader-gen sub-build nests very deep
#: (vulkan-shaders-gen-prefix/.../CMakeFiles/CMakeScratch/TryCompile-.../),
#: and MSVC's FileTracker can exceed MAX_PATH even under a long build root.
_BUILD_DIR_SLUGS: dict[str, str] = {
    "cpu": "cpu",
    "openvino": "ov",
    "vulkan": "vk",
    "openvino_vulkan": "ovvk",
}
VARIANT_NAMES: tuple[str, ...] = ("cpu", "openvino", "vulkan", "openvino_vulkan")

_ENV_NATIVE_DIR = "UTTERAN_NATIVE_DIR"


class NativeBuildError(DependencyError):
    """A native build prerequisite or step failed."""


@dataclass(frozen=True)
class PrerequisiteCheck:
    """Whether one build/runtime prerequisite is satisfiable, and why not."""

    available: bool
    detail: str | None = None


@dataclass(frozen=True)
class BuildResult:
    """One successfully built (or reused) whisper.cpp variant."""

    name: str
    executable: Path
    cmake_flags: tuple[str, ...]
    requires: tuple[str, ...] = ()
    build_seconds: float | None = None


def default_native_dir() -> Path:
    """Short home-relative default; `platformdirs.user_data_dir` gets long on Windows."""
    return Path.home() / ".utteran" / "native"


def resolve_native_dir(configured: Path | None = None) -> Path:
    """Resolve explicit config > UTTERAN_NATIVE_DIR > the short home default."""
    if configured is not None:
        return configured.expanduser()
    if environment := os.environ.get(_ENV_NATIVE_DIR):
        return Path(environment).expanduser()
    return default_native_dir()


def platform_key() -> str:
    """Return an `<os>-<arch>` key used to keep native builds OS-specific."""
    system = "win" if platform.system() == "Windows" else platform.system().lower()
    machine = platform.machine().lower()
    arch = "amd64" if machine in {"amd64", "x86_64"} else machine
    return f"{system}-{arch}"


class ProcessRunner:
    """Thin subprocess wrapper so tests can substitute a fake runner."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one command, always capturing text output and never raising on exit code.

        cmake/git/vswhere output is decoded as UTF-8 with lossy replacement
        rather than `text=True`'s locale-dependent default: on a Japanese
        (cp932) Windows console, that default fails with `UnicodeDecodeError`
        deep inside a subprocess reader thread the moment the child writes
        any byte sequence cp932 can't parse (observed with a real MSVC/CMake
        build - see AISTATE.md Step 5), silently losing the process's output
        without raising in the calling thread.
        """
        return subprocess.run(
            list(command),
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )


def probe_glslc() -> PrerequisiteCheck:
    """Check the Vulkan shader compiler required to *build* the vulkan variant.

    This is a build-time prerequisite distinct from `probe_vulkan_runtime()`
    below. A machine can have a usable Vulkan driver (`vulkaninfo` succeeds)
    without the Vulkan SDK's `glslc` installed, and vice versa - confirmed
    on the Phase 3a investigation machine (see AISTATE.md I-3).
    """
    executable = shutil.which("glslc") or shutil.which("glslc.exe")
    if executable is None:
        return PrerequisiteCheck(
            False, "glslc (Vulkan SDK シェーダーコンパイラ) が見つかりません。"
        )
    return PrerequisiteCheck(True)


def probe_vulkan_runtime(
    runner: ProcessRunner | None = None,
) -> tuple[PrerequisiteCheck, str | None]:
    """Check whether a Vulkan device is usable at runtime, and its name."""
    executable = shutil.which("vulkaninfo") or shutil.which("vulkaninfo.exe")
    if executable is None:
        return PrerequisiteCheck(False, "vulkaninfo が見つかりません。"), None
    result = (runner or ProcessRunner()).run([executable, "--summary"], timeout=15)
    if result.returncode != 0:
        return (
            PrerequisiteCheck(False, f"vulkaninfo が失敗しました (exit={result.returncode})"),
            None,
        )
    device = None
    for line in result.stdout.splitlines():
        if "deviceName" in line and "=" in line:
            device = line.split("=", 1)[1].strip()
            break
    return PrerequisiteCheck(True), device


def probe_openvino_gpu() -> tuple[PrerequisiteCheck, str | None]:
    """Check whether the openvino package can see a GPU-class device."""
    if importlib.util.find_spec("openvino") is None:
        return PrerequisiteCheck(False, "openvino パッケージがありません。"), None
    try:
        import openvino as ov

        core = ov.Core()
        gpu_devices = [name for name in core.available_devices if name.upper().startswith("GPU")]
        if not gpu_devices:
            return PrerequisiteCheck(False, "OpenVINOからGPUが認識されていません。"), None
        try:
            full_name = str(core.get_property(gpu_devices[0], "FULL_DEVICE_NAME"))
        except Exception:  # device plugins expose different property sets
            full_name = gpu_devices[0]
        return PrerequisiteCheck(True), full_name
    except Exception as exc:  # report any OpenVINO init failure as unavailable
        return PrerequisiteCheck(False, f"OpenVINO初期化に失敗しました: {exc}"), None


def resolve_openvino_cmake_dir() -> Path | None:
    """Resolve the openvino pip package's CMake config directory.

    `openvino.get_cmake_path()` does not exist on every openvino release
    (confirmed absent in 2026.2.1 - see AISTATE.md Step 0); fall back to the
    package-relative `cmake/` directory the reference implementation uses,
    and verify `OpenVINOConfig.cmake` is actually there before trusting it.
    """
    try:
        import openvino
    except ImportError:
        return None
    package_dir = Path(openvino.__file__).resolve().parent
    get_cmake_path = getattr(openvino, "get_cmake_path", None)
    cmake_dir = (
        Path(get_cmake_path()).resolve() if callable(get_cmake_path) else package_dir / "cmake"
    )
    if not (cmake_dir / "OpenVINOConfig.cmake").is_file():
        return None
    return cmake_dir


def resolve_openvino_runtime_dirs() -> tuple[Path, ...]:
    """Resolve the openvino runtime DLL/so directories from the *current* venv.

    Never persisted to the manifest: see the module docstring.
    """
    try:
        import openvino
    except ImportError:
        return ()
    package_dir = Path(openvino.__file__).resolve().parent
    candidates = (package_dir / "libs", package_dir.parent / "openvino" / "libs")
    return tuple(dict.fromkeys(path for path in candidates if path.is_dir()))


def resolve_runtime_library_dirs(name: str) -> tuple[Path, ...]:
    """Resolve one variant's runtime library directories from this environment."""
    if name in {"openvino", "openvino_vulkan"}:
        return resolve_openvino_runtime_dirs()
    return ()


class NativeBuilder:
    """Fetch whisper.cpp once and build the requested variants beside it."""

    def __init__(self, native_dir: Path, *, runner: ProcessRunner | None = None) -> None:
        self.native_dir = native_dir
        self.runner = runner or ProcessRunner()
        self.platform = platform_key()
        self.platform_dir = native_dir / self.platform
        self.source_dir = self.platform_dir / "src"
        self.manifest_path = self.platform_dir / "manifest.json"

    def build_dir(self, variant: str) -> Path:
        """Return the short on-disk build directory for one variant."""
        return self.platform_dir / _BUILD_DIR_SLUGS[variant]

    def load_manifest(self) -> dict[str, Any]:
        """Read a manifest matching the pinned whisper.cpp commit, else {}."""
        if not self.manifest_path.is_file():
            return {}
        try:
            with self.manifest_path.open(encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        whisper_cpp = data.get("whisper_cpp")
        if not isinstance(whisper_cpp, dict) or whisper_cpp.get("commit") != WHISPER_CPP_COMMIT:
            return {}
        return data

    def build_all(
        self,
        *,
        variants: Sequence[str] = VARIANT_NAMES,
        force: bool = False,
    ) -> dict[str, Any]:
        """Build every requested, prerequisite-satisfying variant; skip the rest."""
        self._ensure_source(force=force)
        cmake = self._find_cmake()
        existing = self.load_manifest()
        results: dict[str, BuildResult] = {}
        errors: dict[str, str] = {}
        requested = set(variants)

        if "cpu" in requested:
            try:
                results["cpu"] = self._build_variant(
                    "cpu",
                    cmake,
                    ("-DWHISPER_OPENVINO=OFF", "-DGGML_VULKAN=OFF"),
                    force=force,
                    existing=existing,
                )
            except NativeBuildError as exc:
                errors["cpu"] = str(exc)

        openvino_ok = False
        if "openvino" in requested or "openvino_vulkan" in requested:
            check, _device = probe_openvino_gpu()
            openvino_dir = resolve_openvino_cmake_dir() if check.available else None
            if not check.available:
                errors["openvino"] = check.detail or "OpenVINOが利用できません。"
            elif openvino_dir is None:
                errors["openvino"] = "OpenVINOのCMake構成が見つかりません。"
            elif "openvino" in requested:
                try:
                    results["openvino"] = self._build_variant(
                        "openvino",
                        cmake,
                        (
                            "-DWHISPER_OPENVINO=ON",
                            "-DGGML_VULKAN=OFF",
                            f"-DOpenVINO_DIR={openvino_dir}",
                        ),
                        force=force,
                        existing=existing,
                        requires=("openvino",),
                    )
                    openvino_ok = True
                except NativeBuildError as exc:
                    errors["openvino"] = str(exc)
            else:
                openvino_ok = True
        else:
            openvino_dir = None

        vulkan_ok = False
        if "vulkan" in requested or "openvino_vulkan" in requested:
            glslc_check = probe_glslc()
            if not glslc_check.available:
                errors["vulkan"] = glslc_check.detail or "glslc が見つかりません。"
            elif "vulkan" in requested:
                try:
                    results["vulkan"] = self._build_variant(
                        "vulkan",
                        cmake,
                        ("-DWHISPER_OPENVINO=OFF", "-DGGML_VULKAN=ON"),
                        force=force,
                        existing=existing,
                        requires=("vulkan",),
                    )
                    vulkan_ok = True
                except NativeBuildError as exc:
                    errors["vulkan"] = str(exc)
            else:
                vulkan_ok = True

        if "openvino_vulkan" in requested:
            if openvino_dir is not None and openvino_ok and vulkan_ok:
                try:
                    results["openvino_vulkan"] = self._build_variant(
                        "openvino_vulkan",
                        cmake,
                        (
                            "-DWHISPER_OPENVINO=ON",
                            "-DGGML_VULKAN=ON",
                            f"-DOpenVINO_DIR={openvino_dir}",
                        ),
                        force=force,
                        existing=existing,
                        requires=("openvino", "vulkan"),
                    )
                except NativeBuildError as exc:
                    errors["openvino_vulkan"] = str(exc)
            elif "openvino_vulkan" not in errors:
                errors["openvino_vulkan"] = "OpenVINOまたはVulkanの前提条件が揃っていません。"

        existing_backends = existing.get("backends", {})
        preserved_backends = (
            {
                name: {
                    **entry,
                    "cmake_flags": list(
                        _portable_cmake_flags(
                            tuple(str(flag) for flag in entry.get("cmake_flags", []))
                        )
                    ),
                }
                for name, entry in existing_backends.items()
                if name not in requested and isinstance(entry, dict)
            }
            if isinstance(existing_backends, dict)
            else {}
        )
        preserved_backends.update(
            {
                name: {
                    "executable": str(result.executable),
                    "cmake_flags": list(_portable_cmake_flags(result.cmake_flags)),
                    "requires": list(result.requires),
                }
                for name, result in results.items()
            }
        )
        existing_errors = existing.get("errors", {})
        preserved_errors = (
            {name: detail for name, detail in existing_errors.items() if name not in requested}
            if isinstance(existing_errors, dict)
            else {}
        )
        preserved_errors.update(errors)

        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "platform": self.platform,
            "whisper_cpp": {"tag": WHISPER_CPP_TAG, "commit": WHISPER_CPP_COMMIT},
            "built_at": _now(),
            "backends": preserved_backends,
            "errors": preserved_errors,
        }
        self._write_manifest(manifest)
        return manifest

    def status(self) -> dict[str, Any]:
        """Report the manifest plus whether each backend is runnable right now."""
        manifest = self.load_manifest()
        backends = manifest.get("backends", {})
        runnable: dict[str, bool] = {}
        if isinstance(backends, dict):
            for name, entry in backends.items():
                if not isinstance(entry, dict):
                    continue
                executable = entry.get("executable")
                runnable[name] = bool(executable) and Path(str(executable)).is_file()
        return {"manifest": manifest, "runnable": runnable}

    def clean(self, *, variant: str | None = None) -> None:
        """Remove one build directory, or the whole platform tree when None."""
        if variant is None:
            if self.platform_dir.is_dir():
                shutil.rmtree(self.platform_dir)
            return
        build_dir = self.build_dir(variant)
        if build_dir.is_dir():
            shutil.rmtree(build_dir)
        manifest = self.load_manifest()
        backends = manifest.get("backends", {})
        if isinstance(backends, dict) and variant in backends:
            del backends[variant]
            self._write_manifest(manifest)

    def _find_cmake(self) -> str:
        """Locate cmake on PATH, falling back to the active venv's console script."""
        executable = shutil.which("cmake")
        if executable:
            return executable
        import sys

        python_dir = Path(sys.executable).parent
        for candidate in (python_dir / "cmake", python_dir / "cmake.exe"):
            if candidate.is_file():
                return str(candidate)
        raise NativeBuildError(
            "cmake が見つかりません。`uv sync --extra whisper-cpp` を実行してください。"
        )

    def _ensure_source(self, *, force: bool) -> None:
        """Clone or update whisper.cpp to the pinned tag/commit and verify HEAD."""
        git = shutil.which("git")
        if not git:
            raise NativeBuildError("git が見つからないため whisper.cpp を取得できません。")
        if (self.source_dir / ".git").is_dir():
            result = self.runner.run([git, "-C", str(self.source_dir), "rev-parse", "HEAD"])
            if result.returncode == 0 and result.stdout.strip() == WHISPER_CPP_COMMIT and not force:
                return
            fetch = self.runner.run(
                [
                    git,
                    "-C",
                    str(self.source_dir),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    "tag",
                    WHISPER_CPP_TAG,
                ]
            )
            if fetch.returncode != 0:
                raise NativeBuildError(f"whisper.cppの取得に失敗しました: {_tail(fetch.stderr)}")
            checkout = self.runner.run(
                [git, "-C", str(self.source_dir), "checkout", "--detach", WHISPER_CPP_COMMIT]
            )
            if checkout.returncode != 0:
                raise NativeBuildError(
                    f"whisper.cppのcheckoutに失敗しました: {_tail(checkout.stderr)}"
                )
        else:
            self.source_dir.parent.mkdir(parents=True, exist_ok=True)
            result = self.runner.run(
                [
                    git,
                    "clone",
                    "--branch",
                    WHISPER_CPP_TAG,
                    "--depth",
                    "1",
                    WHISPER_CPP_REPOSITORY,
                    str(self.source_dir),
                ]
            )
            if result.returncode != 0:
                raise NativeBuildError(f"whisper.cppのcloneに失敗しました: {_tail(result.stderr)}")
        verify = self.runner.run([git, "-C", str(self.source_dir), "rev-parse", "HEAD"])
        if verify.returncode != 0 or verify.stdout.strip() != WHISPER_CPP_COMMIT:
            raise NativeBuildError("取得したwhisper.cppのコミットが期待値と一致しません。")

    def _build_variant(
        self,
        name: str,
        cmake: str,
        flags: tuple[str, ...],
        *,
        force: bool,
        existing: dict[str, Any],
        requires: tuple[str, ...] = (),
    ) -> BuildResult:
        """Reuse an identical prior build, or configure+build fresh."""
        existing_backends = existing.get("backends", {})
        if isinstance(existing_backends, dict) and not force:
            current = existing_backends.get(name)
            if isinstance(current, dict):
                executable = Path(str(current.get("executable", "")))
                current_flags = tuple(str(flag) for flag in current.get("cmake_flags", []))
                if executable.is_file() and _portable_cmake_flags(
                    current_flags
                ) == _portable_cmake_flags(flags):
                    return BuildResult(name, executable, flags, requires)

        build_dir = self.build_dir(name)
        build_dir.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        configure = [
            cmake,
            "-S",
            str(self.source_dir),
            "-B",
            str(build_dir),
            *self._generator_args(),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DWHISPER_BUILD_TESTS=OFF",
            "-DWHISPER_BUILD_EXAMPLES=ON",
            "-DWHISPER_BUILD_SERVER=OFF",
            *flags,
        ]
        result = self.runner.run(configure, timeout=600)
        if result.returncode != 0:
            raise NativeBuildError(
                f"whisper.cpp {name}版のCMake構成に失敗しました: {_tail(result.stderr)}"
            )
        build = self.runner.run(
            [cmake, "--build", str(build_dir), "--config", "Release", "--target", "whisper-cli"],
            timeout=3600,
        )
        if build.returncode != 0:
            raise NativeBuildError(
                f"whisper.cpp {name}版のビルドに失敗しました: {_tail(build.stderr)}"
            )
        executable = self._find_whisper_cli(build_dir)
        elapsed = time.monotonic() - started
        return BuildResult(name, executable, flags, requires, build_seconds=elapsed)

    def _generator_args(self) -> tuple[str, ...]:
        """Prefer a generator that needs no pre-sourced compiler environment."""
        if platform.system() == "Windows":
            return ("-G", "Visual Studio 17 2022", "-A", "x64")
        return ()

    @staticmethod
    def _find_whisper_cli(build_dir: Path) -> Path:
        """Locate the built CLI under either a flat or per-config bin layout."""
        candidates = [
            path
            for path in build_dir.glob("bin/**/whisper-cli*")
            if path.is_file() and path.suffix.lower() not in {".pdb", ".lib", ".exp"}
        ]
        if not candidates:
            raise NativeBuildError(f"構築後のwhisper-cliが見つかりません: {build_dir}")
        preferred = [path for path in candidates if path.name in {"whisper-cli", "whisper-cli.exe"}]
        return (preferred or candidates)[0].resolve()

    def _write_manifest(self, manifest: Mapping[str, Any]) -> None:
        """Atomically persist the manifest via a same-directory temp file."""
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(dir=self.manifest_path.parent, suffix=".tmp")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temporary.replace(self.manifest_path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _now() -> str:
    """Return an unambiguous local timestamp."""
    from datetime import datetime

    return datetime.now().astimezone().isoformat()


def _tail(text: str, lines: int = 20) -> str:
    """Return the last few lines of subprocess output for a compact error."""
    return "\n".join(text.strip().splitlines()[-lines:])
