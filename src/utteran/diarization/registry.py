"""Diarization backend selection isolated from the pipeline."""

from pathlib import Path

from utteran.config import TokenProvider
from utteran.diarization.base import DiarizationBackend
from utteran.errors import BackendUnavailableError, HuggingFaceTokenMissingError
from utteran.models.manager import find_runtime_model


def create_diarization_backend(
    name: str, token_provider: TokenProvider | None = None
) -> DiarizationBackend:
    """Instantiate a Phase 1 diarization backend by stable registry name."""
    if name == "pyannote":
        from utteran.diarization.pyannote import PyannoteBackend

        return PyannoteBackend(token_provider)
    raise BackendUnavailableError(f"未対応の話者分離バックエンドです: {name}")


def preflight_diarization_backend(
    name: str,
    model_id: str,
    token_provider: TokenProvider,
) -> None:
    """Validate cheap backend prerequisites before running expensive stages."""
    if (
        name == "pyannote"
        and not Path(model_id).expanduser().exists()
        and find_runtime_model(name, model_id, token_provider=token_provider) is None
        and not token_provider.get_token()
    ):
        raise HuggingFaceTokenMissingError
