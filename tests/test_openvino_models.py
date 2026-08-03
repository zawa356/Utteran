from __future__ import annotations

from pathlib import Path

from utteran.models.catalog import get_model
from utteran.models.manager import ModelManager
from utteran.models.openvino import OpenVINOManager, _alias_paths, _link_or_copy


def test_ir_alias_name_follows_whisper_cpp_source_convention(tmp_path: Path) -> None:
    xml, binary = _alias_paths(tmp_path / "ggml-large-v3-turbo-q5_0.bin")

    assert xml.name == "ggml-large-v3-turbo-q5_0-encoder-openvino.xml"
    assert binary.name == "ggml-large-v3-turbo-q5_0-encoder-openvino.bin"


def test_refresh_aliases_exposes_one_ir_to_installed_quantization(tmp_path: Path) -> None:
    models = ModelManager(model_dir=tmp_path)
    manager = OpenVINOManager(models)
    entry = get_model("whisper-cpp:large-v3-turbo-q5_0")
    ggml_dir = models.managed_path(entry)
    ggml_dir.mkdir(parents=True)
    assert entry.artifact_filename is not None
    (ggml_dir / entry.artifact_filename).write_bytes(b"ggml")
    status = manager._status("large-v3-turbo")
    status.xml_path.parent.mkdir(parents=True)
    status.xml_path.write_text("xml", encoding="utf-8")
    status.bin_path.write_bytes(b"bin")

    manager.refresh_aliases("large-v3-turbo")

    xml, binary = _alias_paths(ggml_dir / entry.artifact_filename)
    assert xml.read_text(encoding="utf-8") == "xml"
    assert binary.read_bytes() == b"bin"


def test_link_falls_back_semantically_to_same_content(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "nested" / "destination.bin"
    source.write_bytes(b"content")

    _link_or_copy(source, destination)

    assert destination.read_bytes() == b"content"
