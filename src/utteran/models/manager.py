"""Explicit model detection, download, verification, and removal."""

from __future__ import annotations

import json
import os
import re
import shutil
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
        """Find a managed copy first, then the default Hugging Face cache."""
        managed = self.managed_path(entry)
        if _looks_installed(entry, managed):
            return managed, True
        try:
            from huggingface_hub import snapshot_download

            cached = Path(
                snapshot_download(
                    repo_id=entry.repository_id,
                    token=self._token_provider.get_token(),
                    local_files_only=True,
                )
            )
            if _looks_installed(entry, cached):
                return cached, False
        except Exception:
            pass
        return None, False

    def status(self, entry: ModelEntry) -> ModelStatus:
        """Return installed location and actual byte size."""
        path, managed = self.find_installed(entry)
        return ModelStatus(
            entry=entry,
            installed=path is not None,
            path=path,
            size_bytes=0 if path is None else _directory_size(path),
            managed=managed,
        )

    def list_status(self, *, available: bool = False) -> list[ModelStatus]:
        """List installed entries, or the complete catalog when requested."""
        statuses = [self.status(entry) for entry in list_models()]
        return statuses if available else [status for status in statuses if status.installed]

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
            from huggingface_hub import snapshot_download

            downloaded = Path(
                snapshot_download(
                    repo_id=entry.repository_id,
                    token=token,
                    local_dir=destination,
                )
            )
        except Exception as exc:
            _raise_download_error(entry, exc)
        _check_cancel(cancel)
        if not _looks_installed(entry, downloaded):
            raise ModelNotFoundError(
                f"取得したモデル '{entry.key}' に必要なファイルがありません。"
                "`utteran models remove` 後に再取得してください。"
            )
        _write_metadata(downloaded, entry)
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
        """Remove an utteran-managed copy or all revisions in the default HF cache."""
        path, managed = self.find_installed(entry)
        if path is None:
            return False
        if managed:
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
        """Validate required files and nonzero total size."""
        path, _managed = self.find_installed(entry)
        if path is None:
            return VerificationResult(entry, False, None, 0, "未導入")
        size = _directory_size(path)
        if not _looks_installed(entry, path):
            return VerificationResult(entry, False, path, size, "必要なモデルファイルがありません")
        if size <= 0:
            return VerificationResult(entry, False, path, size, "モデルディレクトリが空です")
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
    if not path.is_dir():
        return False
    if entry.format == "CTranslate2":
        return (path / "config.json").is_file() and (path / "model.bin").is_file()
    if entry.format == "OpenVINO IR":
        return any(path.glob("*.xml")) and any(path.glob("*.bin"))
    if entry.format == "pyannote pipeline":
        return (path / "config.yaml").is_file() or (path / "config.yml").is_file()
    if entry.format == "ONNX":
        return any(path.rglob("*.onnx"))
    return any(candidate.is_file() for candidate in path.rglob("*"))


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
