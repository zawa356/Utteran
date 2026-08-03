"""Prepare and manage whisper.cpp OpenVINO encoder IR artifacts."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from utteran.errors import DependencyError, ModelNotFoundError
from utteran.models.catalog import get_model, list_models
from utteran.models.manager import ModelManager


@dataclass(frozen=True)
class OpenVINOStatus:
    """Prepared encoder state for one Whisper model size."""

    model_size: str
    xml_path: Path
    bin_path: Path
    installed: bool


class OpenVINOManager:
    """Convert one encoder per model size and expose aliases to GGML variants."""

    def __init__(self, manager: ModelManager | None = None) -> None:
        self.models = manager or ModelManager()
        self.root = self.models.root / "openvino-encoder"

    def prepare(self, identifier: str, *, purge_cache: bool = False) -> OpenVINOStatus:
        """Run the pinned compatible converter and install its XML/BIN pair."""
        _require_dependencies()
        entry = get_model(identifier, backend="whisper-cpp")
        if not entry.model_size:
            raise ModelNotFoundError(f"OpenVINO変換対象を解決できません: {identifier}")
        model_size = entry.model_size
        target = self._status(model_size)
        target.xml_path.parent.mkdir(parents=True, exist_ok=True)
        vendor = Path(__file__).with_name("vendor") / "convert_whisper_to_openvino.py"
        with tempfile.TemporaryDirectory(prefix="utteran-openvino-") as temporary:
            work = Path(temporary)
            script = work / vendor.name
            shutil.copy2(vendor, script)
            result = subprocess.run(
                [sys.executable, str(script), "--model", model_size],
                cwd=work,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
            generated_xml = work / f"ggml-{model_size}-encoder-openvino.xml"
            generated_bin = generated_xml.with_suffix(".bin")
            if result.returncode != 0 or not generated_xml.is_file() or not generated_bin.is_file():
                raise DependencyError(
                    "OpenVINO encoder IRの変換に失敗しました。"
                    "空き容量とopenvino extraを確認してください。"
                )
            shutil.move(generated_xml, target.xml_path)
            shutil.move(generated_bin, target.bin_path)
        self.refresh_aliases(model_size)
        if purge_cache:
            _purge_whisper_cache(model_size)
        return self._status(model_size)

    def list(self) -> list[OpenVINOStatus]:
        """Return a stable status list for every distinct catalog model size."""
        sizes = sorted(
            {entry.model_size for entry in list_models(backend="whisper-cpp") if entry.model_size}
        )
        return [self._status(size) for size in sizes]

    def remove(self, identifier: str) -> bool:
        """Remove one canonical IR pair and all managed aliases."""
        entry = get_model(identifier, backend="whisper-cpp")
        if not entry.model_size:
            return False
        status = self._status(entry.model_size)
        existed = status.xml_path.exists() or status.bin_path.exists()
        status.xml_path.unlink(missing_ok=True)
        status.bin_path.unlink(missing_ok=True)
        for variant in list_models(backend="whisper-cpp"):
            if variant.model_size == entry.model_size and variant.artifact_filename:
                directory = self.models.managed_path(variant)
                _alias_paths(directory / variant.artifact_filename)[0].unlink(missing_ok=True)
                _alias_paths(directory / variant.artifact_filename)[1].unlink(missing_ok=True)
        return existed

    def refresh_aliases(self, model_size: str) -> None:
        """Link the canonical pair beside every installed quantization variant."""
        status = self._status(model_size)
        if not status.installed:
            return
        for entry in list_models(backend="whisper-cpp"):
            if entry.model_size != model_size or not entry.artifact_filename:
                continue
            ggml = self.models.managed_path(entry) / entry.artifact_filename
            if ggml.is_file():
                xml_alias, bin_alias = _alias_paths(ggml)
                _link_or_copy(status.xml_path, xml_alias)
                _link_or_copy(status.bin_path, bin_alias)

    def _status(self, model_size: str) -> OpenVINOStatus:
        directory = self.root / model_size
        xml = directory / f"ggml-{model_size}-encoder-openvino.xml"
        binary = xml.with_suffix(".bin")
        return OpenVINOStatus(model_size, xml, binary, xml.is_file() and binary.is_file())


def _require_dependencies() -> None:
    missing = [
        name
        for name in ("whisper", "onnxscript", "openvino")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        raise DependencyError(
            "OpenVINO変換依存がありません: "
            + ", ".join(missing)
            + "。`setup.ps1 -Profile intel`または`uv sync --extra openvino`を実行してください。"
        )


def _alias_paths(ggml_path: Path) -> tuple[Path, Path]:
    xml = ggml_path.with_name(ggml_path.stem + "-encoder-openvino.xml")
    return xml, xml.with_suffix(".bin")


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _purge_whisper_cache(model_size: str) -> None:
    cache = Path.home() / ".cache" / "whisper"
    (cache / f"{model_size}.pt").unlink(missing_ok=True)
