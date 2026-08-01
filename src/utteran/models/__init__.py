"""Explicit model catalog and local model management."""

from utteran.models.catalog import ModelEntry, get_model, list_models
from utteran.models.manager import ModelManager, ModelStatus, VerificationResult

__all__ = [
    "ModelEntry",
    "ModelManager",
    "ModelStatus",
    "VerificationResult",
    "get_model",
    "list_models",
]
