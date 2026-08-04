"""Explicit model detection, download, verification, and removal."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir

from utteran.config import TokenProvider, default_token_provider
from utteran.errors import (
    HuggingFaceAuthenticationError,
    HuggingFaceTokenMissingError,
    ModelAgreementError,
    ModelNotFoundError,
)
from utteran.models.catalog import ModelEntry, get_model, list_models
from utteran.types import CancelToken, ProgressCallback, ProgressEvent

_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class ModelStatus:
    """Installed state for one catalog entry."""

    entry: ModelEntry
    installed: bool
    path: Path | None
    size_bytes: int
    managed: bool


@dataclass(frozen=True)
class VerificationResult:
    """Existence/size validation result for one installed model."""

    entry: ModelEntry
    ok: bool
    path: Path | None
    size_bytes: int
    message: str


class ModelManager:
    """Manage models below an explicit or platform-default cache directory."""

    def __init__(
        self,
        model_dir: Path | None = None,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self.root = resolve_model_dir(model_dir)
        self._token_provider = token_provider or default_token_provider()

    def managed_path(self, entry: ModelEntry) -> Path:
        """Return the backend-specific local directory for an entry."""
        component = _SAFE_COMPONENT.sub("--", entry.model_id).strip("-")
        return self.root / entry.backend / component

    def find_installed(self, entry: ModelEntry) -> tuple[Path | None, bool]:
        """Find a complete managed copy first, then the default Hugging Face cache."""
        for path, managed in self._candidate_paths(entry):
            if _looks_installed(entry, path):
                return path, managed
        return None, False

    def _candidate_paths(self, entry: ModelEntry) -> list[tuple[Path, bool]]:
        """Return existing managed and standard-cache candidates, including partial copies."""
        candidates: list[tuple[Path, bool]] = []
        managed = self.managed_path(entry)
        if managed.is_dir():
            candidates.append((managed, True))
        try:
            from huggingface_hub import snapshot_download

            cached = Path(
                snapshot_download(
                    repo_id=entry.repository_id,
                    token=self._token_provider.get_token(),
                    local_files_only=True,
                )
            )
            if cached.is_dir() and cached != managed:
                candidates.append((cached, False))
        except Exception:
            pass
        return candidates

    def status(self, entry: ModelEntry) -> ModelStatus:
        """Return complete or partial location and actual byte size."""
        candidates = self._candidate_paths(entry)
        complete = next(
            ((path, managed) for path, managed in candidates if _looks_installed(entry, path)),
            None,
        )
        selected = complete or (candidates[0] if candidates else (None, False))
        path, managed = selected
        return ModelStatus(
            entry=entry,
            installed=complete is not None,
            path=path,
            size_bytes=0 if path is None else _directory_size(path),
            managed=managed,
        )

    def list_status(
        self, *, available: bool = False, all_models: bool = False
    ) -> list[ModelStatus]:
        """List local complete/partial entries, or the complete catalog when requested."""
        entries = list_models(recommended_only=available and not all_models)
        statuses = [self.status(entry) for entry in entries]
        return statuses if available else [status for status in statuses if status.path is not None]

    def download(
        self,
        entry: ModelEntry,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> Path:
        """Explicitly download one snapshot into the utteran model directory."""
        _check_cancel(cancel)
        token = self._token_provider.get_token()
        if entry.gated and not token:
            raise HuggingFaceTokenMissingError
        destination = self.managed_path(entry)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if progress is not None:
            progress(
                ProgressEvent(
                    "model-download",
                    0.0,
                    1.0,
                    f"{entry.key} を取得しています",
                )
            )
        try:
            if entry.format in {"GGML", "GGML VAD"} and entry.artifact_filename:
                from huggingface_hub import hf_hub_download

                hf_hub_download(
                    repo_id=entry.repository_id,
                    filename=entry.artifact_filename,
                    token=token,
                    local_dir=_snapshot_download_path(destination),
                )
            else:
                from huggingface_hub import snapshot_download

                snapshot_download(
                    repo_id=entry.repository_id,
                    token=token,
                    local_dir=_snapshot_download_path(destination),
                )
            downloaded = destination
        except Exception as exc:
            _raise_download_error(entry, exc)
        _check_cancel(cancel)
        if not _looks_installed(entry, downloaded):
            raise ModelNotFoundError(
                f"取得したモデル '{entry.key}' に必要なファイルがありません。"
                "`utteran models remove` 後に再取得してください。"
            )
        _write_metadata(downloaded, entry)
        if entry.format == "CTranslate2":
            _verify_alignment_heads(downloaded)
        elif entry.format == "GGML" and entry.model_size:
            from utteran.models.openvino import OpenVINOManager

            OpenVINOManager(self).refresh_aliases(entry.model_size)
        if progress is not None:
            progress(
                ProgressEvent(
                    "model-download",
                    1.0,
                    1.0,
                    f"{entry.key} の取得が完了しました",
                )
            )
        return downloaded

    def remove(self, entry: ModelEntry) -> bool:
        """Remove a complete/partial managed copy or all revisions in the default HF cache."""
        status = self.status(entry)
        if status.path is None:
            return False
        path = status.path
        if status.managed:
            shutil.rmtree(path)
            _remove_empty_parent(path.parent, self.root)
            return True
        try:
            from huggingface_hub import scan_cache_dir

            cache = scan_cache_dir()
            repository = next(
                (repo for repo in cache.repos if repo.repo_id == entry.repository_id),
                None,
            )
            if repository is None:
                return False
            revisions = [revision.commit_hash for revision in repository.revisions]
            if not revisions:
                return False
            cache.delete_revisions(*revisions).execute()
            return True
        except Exception as exc:
            raise ModelNotFoundError(
                f"モデルキャッシュを削除できません: {entry.key}: {type(exc).__name__}"
            ) from None

    def verify(self, entry: ModelEntry) -> VerificationResult:
        """Validate every format-specific required file and nonzero total size."""
        status = self.status(entry)
        if status.path is None:
            return VerificationResult(entry, False, None, 0, "未導入")
        path = status.path
        size = status.size_bytes
        missing = _missing_required_files(entry, path)
        if missing:
            return VerificationResult(
                entry,
                False,
                path,
                size,
                f"必須ファイル不足: {', '.join(missing)}",
            )
        if size <= 0:
            return VerificationResult(entry, False, path, size, "モデルディレクトリが空です")
        if entry.format == "CTranslate2":
            _verify_alignment_heads(path)
        if entry.format == "GGML" and entry.model_size and entry.artifact_filename:
            ir_root = self.root / "openvino-encoder" / entry.model_size
            canonical_xml = ir_root / f"ggml-{entry.model_size}-encoder-openvino.xml"
            canonical_bin = canonical_xml.with_suffix(".bin")
            if canonical_xml.exists() != canonical_bin.exists():
                return VerificationResult(entry, False, path, size, "OpenVINO IRのXML/BINが不整合")
            if canonical_xml.is_file():
                alias_xml = path / (Path(entry.artifact_filename).stem + "-encoder-openvino.xml")
                alias_bin = alias_xml.with_suffix(".bin")
                if not alias_xml.is_file() or not alias_bin.is_file():
                    return VerificationResult(
                        entry, False, path, size, "OpenVINO IR aliasがありません"
                    )
        return VerificationResult(entry, True, path, size, "正常")


def resolve_model_dir(configured: Path | None = None) -> Path:
    """Resolve explicit path > UTTERAN_MODEL_DIR > platform cache default."""
    if configured is not None:
        return configured.expanduser()
    if environment := os.environ.get("UTTERAN_MODEL_DIR"):
        return Path(environment).expanduser()
    return Path(user_cache_dir("utteran")) / "models"


def find_runtime_model(
    backend: str,
    model_id: str,
    *,
    token_provider: TokenProvider | None = None,
) -> Path | None:
    """Resolve a catalog model to a managed or standard-cache snapshot."""
    if Path(model_id).expanduser().exists():
        return Path(model_id).expanduser()
    try:
        entry = get_model(model_id, backend=backend)
    except Exception:
        return None
    path, _managed = ModelManager(token_provider=token_provider).find_installed(entry)
    return path


def _looks_installed(entry: ModelEntry, path: Path) -> bool:
    """Check format-specific required files without loading a model."""
    return not _missing_required_files(entry, path)


def _missing_required_files(entry: ModelEntry, path: Path) -> tuple[str, ...]:
    """Return missing format-specific files or patterns for one model directory."""
    if not path.is_dir():
        return ("<model-directory>",)
    if entry.format == "CTranslate2":
        return tuple(name for name in ("config.json", "model.bin") if not (path / name).is_file())
    if entry.format in {"GGML", "GGML VAD"}:
        filename = entry.artifact_filename or "<ggml-file>"
        candidate = path / filename
        return () if candidate.is_file() and candidate.stat().st_size > 0 else (filename,)
    if entry.format == "OpenVINO IR":
        missing = []
        if not any(path.glob("*.xml")):
            missing.append("*.xml")
        if not any(path.glob("*.bin")):
            missing.append("*.bin")
        return tuple(missing)
    if entry.format == "pyannote pipeline":
        required = (
            "embedding/pytorch_model.bin",
            "segmentation/pytorch_model.bin",
            "plda/plda.npz",
            "plda/xvec_transform.npz",
        )
        missing = list(name for name in required if not (path / name).is_file())
        if not (path / "config.yaml").is_file() and not (path / "config.yml").is_file():
            missing.insert(0, "config.yaml|config.yml")
        return tuple(missing)
    if entry.format == "ONNX":
        return () if any(path.rglob("*.onnx")) else ("*.onnx",)
    return () if any(candidate.is_file() for candidate in path.rglob("*")) else ("<model-file>",)


_SAFE_ALIGNMENT_HEADS = [[0, 0]]


def _verify_alignment_heads(path: Path) -> None:
    """Detect and safely replace an alignment_heads config that indexes a
    decoder layer the model does not have.

    Some CTranslate2 conversions of distilled Whisper models (for example
    kotoba-whisper-v2.0-faster) ship this config unmodified from the
    original, deeper decoder. Requesting word timestamps then crashes the
    process natively; Python cannot catch it. Running the same alignment
    path in a disposable subprocess lets a crash be observed safely from the
    outside via the exit code, without risking the caller's process.
    """
    config_path = path / "config.json"
    if not config_path.is_file():
        return
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if "alignment_heads" not in config or config["alignment_heads"] == _SAFE_ALIGNMENT_HEADS:
        return
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "utteran.models._alignment_probe", str(path)],
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if probe.returncode in (0, 2):
        return
    logging.getLogger(__name__).warning(
        "モデル '%s' の alignment_heads が実際の decoder に存在しない層を参照しており、"
        "単語タイムスタンプ計算時にクラッシュするため、安全な既定値へ自動修正しました。"
        "単語レベルのタイムスタンプ精度が低下する可能性があります。",
        path.name,
    )
    config["alignment_heads"] = _SAFE_ALIGNMENT_HEADS
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _raise_download_error(entry: ModelEntry, error: Exception) -> None:
    """Classify Hub failures without leaking a token or raw request details."""
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError

    if isinstance(error, GatedRepoError):
        raise ModelAgreementError(
            f"モデル '{entry.key}' の利用条件に同意されていません。"
            f"{entry.agreement_url} を開き、利用条件に同意してください。"
        ) from None
    if isinstance(error, HfHubHTTPError):
        status = error.response.status_code if error.response is not None else None
        if status == 401:
            raise HuggingFaceAuthenticationError(
                "Hugging Face トークンが無効です。読み取り権限と値を確認してください。"
            ) from None
        if status == 403 and entry.gated:
            raise ModelAgreementError(
                f"モデル '{entry.key}' の利用条件に未同意か、アクセス権がありません。"
                f"{entry.agreement_url} を確認してください。"
            ) from None
    raise ModelNotFoundError(
        f"モデル '{entry.key}' を取得できません。ネットワーク接続、モデル ID、"
        "保存先の空き容量を確認してください。"
    ) from None


def _write_metadata(path: Path, entry: ModelEntry) -> None:
    """Atomically record the catalog identity of a managed snapshot."""
    payload = {
        "catalog_key": entry.key,
        "repository_id": entry.repository_id,
        "format": entry.format,
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".utteran-model.",
            suffix=".tmp",
            dir=path,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
        temporary_path.replace(path / ".utteran-model.json")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _snapshot_download_path(path: Path) -> Path:
    """Use an extended Windows path so Hub temporary names can exceed MAX_PATH."""
    if os.name != "nt":
        return path
    return Path(_extend_windows_path(str(path.resolve())))


def _extend_windows_path(path: str) -> str:
    """Add the Windows extended-length prefix without duplicating an existing prefix."""
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return f"\\\\?\\UNC\\{path[2:]}"
    return f"\\\\?\\{path}"


def _directory_size(path: Path) -> int:
    """Sum readable regular-file sizes for display and validation."""
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total


def _remove_empty_parent(path: Path, stop: Path) -> None:
    """Remove an empty backend directory without crossing the model root."""
    if path != stop and path.parent == stop:
        with suppress(OSError):
            path.rmdir()


def _check_cancel(cancel: CancelToken | None) -> None:
    """Honor cancellation at safe download boundaries."""
    if cancel is not None:
        cancel.raise_if_cancelled()
