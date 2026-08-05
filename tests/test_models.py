from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

from utteran.config import TokenProvider
from utteran.errors import (
    ConfigurationError,
    HuggingFaceAuthenticationError,
    HuggingFaceTokenMissingError,
    ModelAgreementError,
)
from utteran.models.catalog import get_model, list_models
from utteran.models.manager import ModelManager, _extend_windows_path, resolve_model_dir
from utteran.types import ProgressEvent


def test_whisper_cpp_catalog_uses_verified_artifact_names() -> None:
    entries = list_models(backend="whisper-cpp")

    assert len(entries) == 33
    turbo = get_model("whisper-cpp:large-v3-turbo-q5_0")
    assert turbo.artifact_filename == "ggml-large-v3-turbo-q5_0.bin"
    assert turbo.quantization == "q5_0"
    assert turbo.dtw_preset == "large.v3.turbo"
    assert turbo.recommended
    assert all(entry.license == "MIT" for entry in entries)


def test_whisper_cpp_vad_catalog_entry_is_downloadable() -> None:
    entry = get_model("whisper-cpp-vad:silero-v6.2.0")

    assert entry.repository_id == "ggml-org/whisper-vad"
    assert entry.artifact_filename == "ggml-silero-v6.2.0.bin"
    assert entry.format == "GGML VAD"


def test_recommended_catalog_hides_nonrecommended_and_english_models() -> None:
    entries = list_models(backend="whisper-cpp", recommended_only=True)

    assert {entry.model_id for entry in entries} == {
        "large-v3-turbo",
        "large-v3-turbo-q5_0",
        "large-v3",
        "large-v3-q5_0",
        "medium-q5_0",
        "base",
    }
    assert not any(entry.english_only for entry in entries)


class StaticTokenProvider(TokenProvider):
    def __init__(self, token: str | None) -> None:
        self.token = token

    def get_token(self) -> str | None:
        return self.token


def _write_partial_pyannote(path: Path) -> None:
    """Create the small files left by an interrupted community-1 download."""
    (path / "plda").mkdir(parents=True)
    (path / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
    (path / "plda" / "plda.npz").write_bytes(b"plda")
    (path / "plda" / "xvec_transform.npz").write_bytes(b"transform")


def test_catalog_keeps_same_model_separate_by_backend() -> None:
    turbo = [entry for entry in list_models() if entry.model_id == "large-v3-turbo"]

    assert {entry.backend for entry in turbo} == {
        "faster-whisper",
        "openvino",
        "whisper-cpp",
    }
    assert get_model("faster-whisper:large-v3-turbo").format == "CTranslate2"
    assert get_model("large-v3-turbo", backend="openvino").format == "OpenVINO IR"
    with pytest.raises(ConfigurationError, match="複数"):
        get_model("large-v3-turbo")


def test_catalog_has_human_oriented_names_and_japanese_model() -> None:
    entries = list_models()
    japanese = get_model("faster-whisper:kotoba-whisper-v2.0")

    assert all(entry.display_name and entry.description for entry in entries)
    assert "日本語" in japanese.description


def test_model_dir_environment_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UTTERAN_MODEL_DIR", str(tmp_path / "custom"))

    assert resolve_model_dir() == tmp_path / "custom"


def test_windows_extended_path_supports_drive_and_unc_paths() -> None:
    drive = r"C:\Users\person\AppData\Local\utteran\models"
    unc = r"\\server\share\utteran\models"

    assert _extend_windows_path(drive) == rf"\\?\{drive}"
    assert _extend_windows_path(unc) == r"\\?\UNC\server\share\utteran\models"
    assert _extend_windows_path(rf"\\?\{drive}") == rf"\\?\{drive}"


def test_managed_model_status_verify_and_remove(tmp_path: Path) -> None:
    manager = ModelManager(tmp_path / "models", StaticTokenProvider(None))
    entry = get_model("faster-whisper:large-v3")
    path = manager.managed_path(entry)
    path.mkdir(parents=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    (path / "model.bin").write_bytes(b"weights")

    status = manager.status(entry)
    verification = manager.verify(entry)

    assert status.installed and status.managed and status.path == path
    assert verification.ok and verification.size_bytes > 0
    assert manager.remove(entry)
    assert not path.exists()


def test_partial_pyannote_is_reported_and_download_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ModelManager(tmp_path / "models", StaticTokenProvider("hf_test"))
    entry = get_model("pyannote/speaker-diarization-community-1", backend="pyannote")
    path = manager.managed_path(entry)
    _write_partial_pyannote(path)

    def no_standard_cache(**_kwargs: object) -> str:
        raise FileNotFoundError

    monkeypatch.setattr("huggingface_hub.snapshot_download", no_standard_cache)

    status = manager.status(entry)
    verification = manager.verify(entry)

    assert not status.installed
    assert status.path == path
    assert any(item.entry == entry for item in manager.list_status())
    assert not verification.ok
    assert "embedding/pytorch_model.bin" in verification.message
    assert "segmentation/pytorch_model.bin" in verification.message

    def resume_download(*, repo_id: str, token: str | None, local_dir: Path) -> str:
        assert repo_id == entry.repository_id
        assert token == "hf_test"
        assert (local_dir / "config.yaml").is_file()
        (local_dir / "embedding").mkdir()
        (local_dir / "segmentation").mkdir()
        (local_dir / "embedding" / "pytorch_model.bin").write_bytes(b"embedding")
        (local_dir / "segmentation" / "pytorch_model.bin").write_bytes(b"segmentation")
        return str(local_dir)

    monkeypatch.setattr("huggingface_hub.snapshot_download", resume_download)

    assert manager.download(entry) == path
    assert manager.verify(entry).ok


def test_download_is_explicit_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ModelManager(tmp_path / "models", StaticTokenProvider(None))
    entry = get_model("faster-whisper:large-v3")
    events: list[ProgressEvent] = []

    def fake_download(*, repo_id: str, token: str | None, local_dir: Path) -> str:
        assert repo_id == entry.repository_id
        assert token is None
        local_dir.mkdir(parents=True)
        (local_dir / "config.json").write_text("{}", encoding="utf-8")
        (local_dir / "model.bin").write_bytes(b"weights")
        return str(local_dir)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_download)

    path = manager.download(entry, progress=events.append)

    assert path == manager.managed_path(entry)
    assert (path / ".utteran-model.json").is_file()
    assert events[0].completed == 0.0
    assert events[-1].completed == 1.0


def test_gated_download_classifies_missing_token_and_agreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = get_model("pyannote/speaker-diarization-community-1", backend="pyannote")
    with pytest.raises(HuggingFaceTokenMissingError):
        ModelManager(tmp_path / "models", StaticTokenProvider(None)).download(entry)

    response = httpx.Response(
        403,
        request=httpx.Request("GET", entry.agreement_url),
    )

    def gated_download(**_kwargs: object) -> str:
        raise GatedRepoError("gated", response=response)

    monkeypatch.setattr("huggingface_hub.snapshot_download", gated_download)
    with pytest.raises(ModelAgreementError, match="利用条件"):
        ModelManager(tmp_path / "models", StaticTokenProvider("hf_test")).download(entry)


def test_download_corrects_alignment_heads_that_crash_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config.json referencing a nonexistent decoder layer (the
    kotoba-whisper-v2.0-faster defect) is rewritten to a safe fallback when
    the isolated alignment probe reports an abnormal (crashed) exit."""
    manager = ModelManager(tmp_path / "models", StaticTokenProvider(None))
    entry = get_model("faster-whisper:kotoba-whisper-v2.0")

    def fake_download(*, repo_id: str, token: str | None, local_dir: Path) -> str:
        local_dir.mkdir(parents=True)
        (local_dir / "config.json").write_text(
            json.dumps({"alignment_heads": [[25, 6]]}), encoding="utf-8"
        )
        (local_dir / "model.bin").write_bytes(b"weights")
        return str(local_dir)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_download)
    monkeypatch.setattr(
        "utteran.models.manager.subprocess.run",
        lambda *_a, **_k: subprocess.CompletedProcess([], returncode=-1073741819),
    )

    path = manager.download(entry)

    fixed = json.loads((path / "config.json").read_text(encoding="utf-8"))
    assert fixed["alignment_heads"] == [[0, 0]]


def test_download_keeps_alignment_heads_when_probe_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ModelManager(tmp_path / "models", StaticTokenProvider(None))
    entry = get_model("faster-whisper:large-v3")
    original_heads = [[3, 14]]

    def fake_download(*, repo_id: str, token: str | None, local_dir: Path) -> str:
        local_dir.mkdir(parents=True)
        (local_dir / "config.json").write_text(
            json.dumps({"alignment_heads": original_heads}), encoding="utf-8"
        )
        (local_dir / "model.bin").write_bytes(b"weights")
        return str(local_dir)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_download)
    monkeypatch.setattr(
        "utteran.models.manager.subprocess.run",
        lambda *_a, **_k: subprocess.CompletedProcess([], returncode=0),
    )

    path = manager.download(entry)

    kept = json.loads((path / "config.json").read_text(encoding="utf-8"))
    assert kept["alignment_heads"] == original_heads


def test_download_keeps_alignment_heads_when_probe_is_inconclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ordinary Python-level failure inside the probe (exit code 2, e.g. a
    missing optional dependency) must not be treated as proof of the crash."""
    manager = ModelManager(tmp_path / "models", StaticTokenProvider(None))
    entry = get_model("faster-whisper:large-v3")
    original_heads = [[3, 14]]

    def fake_download(*, repo_id: str, token: str | None, local_dir: Path) -> str:
        local_dir.mkdir(parents=True)
        (local_dir / "config.json").write_text(
            json.dumps({"alignment_heads": original_heads}), encoding="utf-8"
        )
        (local_dir / "model.bin").write_bytes(b"weights")
        return str(local_dir)

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_download)
    monkeypatch.setattr(
        "utteran.models.manager.subprocess.run",
        lambda *_a, **_k: subprocess.CompletedProcess([], returncode=2),
    )

    path = manager.download(entry)

    kept = json.loads((path / "config.json").read_text(encoding="utf-8"))
    assert kept["alignment_heads"] == original_heads


def test_verify_self_heals_installed_model_with_crashing_alignment_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ModelManager(tmp_path / "models", StaticTokenProvider(None))
    entry = get_model("faster-whisper:kotoba-whisper-v2.0")
    path = manager.managed_path(entry)
    path.mkdir(parents=True)
    (path / "config.json").write_text(json.dumps({"alignment_heads": [[25, 6]]}), encoding="utf-8")
    (path / "model.bin").write_bytes(b"weights")
    monkeypatch.setattr(
        "utteran.models.manager.subprocess.run",
        lambda *_a, **_k: subprocess.CompletedProcess([], returncode=-1073741819),
    )

    verification = manager.verify(entry)

    assert verification.ok
    fixed = json.loads((path / "config.json").read_text(encoding="utf-8"))
    assert fixed["alignment_heads"] == [[0, 0]]


def test_verify_skips_probe_for_an_already_fixed_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = ModelManager(tmp_path / "models", StaticTokenProvider(None))
    entry = get_model("faster-whisper:kotoba-whisper-v2.0")
    path = manager.managed_path(entry)
    path.mkdir(parents=True)
    (path / "config.json").write_text(json.dumps({"alignment_heads": [[0, 0]]}), encoding="utf-8")
    (path / "model.bin").write_bytes(b"weights")
    calls: list[object] = []
    monkeypatch.setattr(
        "utteran.models.manager.subprocess.run",
        lambda *args, **kwargs: calls.append(args) or subprocess.CompletedProcess([], returncode=0),
    )

    assert manager.verify(entry).ok
    assert calls == []


def test_download_classifies_invalid_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = get_model("faster-whisper:large-v3")
    response = httpx.Response(
        401,
        request=httpx.Request("GET", entry.agreement_url),
    )

    def unauthorized_download(**_kwargs: object) -> str:
        raise HfHubHTTPError("unauthorized", response=response)

    monkeypatch.setattr("huggingface_hub.snapshot_download", unauthorized_download)
    with pytest.raises(HuggingFaceAuthenticationError, match="無効"):
        ModelManager(tmp_path / "models", StaticTokenProvider("hf_invalid")).download(entry)
