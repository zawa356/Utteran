"""Pinned community-1 assets for the v1 offline adapter."""

from __future__ import annotations

import hashlib
import importlib.metadata
import tempfile
from pathlib import Path
from typing import Any

from utteran.errors import BackendUnavailableError, ModelNotFoundError

MODEL = "pyannote/speaker-diarization-community-1"
REVISION = "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee"
SDK_VERSION = "4.0.7"
_CONFIG_BLOB = "4022db43960736338378fdb6b5a85cfdae198910"
ASSETS = {
    "embedding/pytorch_model.bin": (
        26646242,
        "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929",
    ),
    "segmentation/pytorch_model.bin": (
        5906507,
        "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e",
    ),
    "plda/plda.npz": (
        133852,
        "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255",
    ),
    "plda/xvec_transform.npz": (
        134376,
        "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f",
    ),
}


def _asset(root: Path, name: str, size: int) -> bytes:
    path = root / name
    try:
        if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
            raise OSError
        with path.open("rb") as source:
            data = source.read(size + 1)
        if len(data) != size:
            raise OSError
    except OSError:
        raise ModelNotFoundError("managed community-1 asset missing or invalid") from None
    return data


def _configuration(root: Path) -> dict[str, Any]:
    # HF publishes the git blob ID for this small, non-LFS configuration file.
    data = _asset(root, "config.yaml", 444)
    if hashlib.sha1(b"blob 444\0" + data, usedforsecurity=False).hexdigest() != _CONFIG_BLOB:
        raise ModelNotFoundError("managed community-1 configuration mismatch")
    import yaml  # type: ignore[import-untyped]

    data_config = yaml.safe_load(data)
    if (
        not isinstance(data_config, dict)
        or data_config.get("pipeline", {}).get("name")
        != "pyannote.audio.pipelines.SpeakerDiarization"
        or set(data_config) - {"pipeline", "params", "version", "dependencies"}
    ):
        raise ModelNotFoundError("managed community-1 configuration unsupported")
    return data_config


def _verified_asset(root: Path, name: str) -> bytes:
    size, expected = ASSETS[name]
    data = _asset(root, name, size)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ModelNotFoundError("managed community-1 component mismatch")
    return data


def verify_assets(root: Path) -> None:
    root = root.resolve()
    _configuration(root)
    for name in ASSETS:
        _verified_asset(root, name)


def load_pipeline(root: Path) -> Any:
    """Load only verified bytes copied into a private directory for the SDK.

    The original YAML cannot introduce preprocessors, imports, or remote components.
    Model.from_pretrained and PLDA.from_pretrained select local branches for these
    explicit paths. Passing Model and PLDA instances avoids their transitive Hub resolvers.
    """
    if importlib.metadata.version("pyannote.audio") != SDK_VERSION:
        raise BackendUnavailableError("community-1 SDK version mismatch")
    root = root.resolve()
    config = _configuration(root)
    with tempfile.TemporaryDirectory(prefix="utteran-community-") as temporary:
        local = Path(temporary)
        for name in ASSETS:
            data = _verified_asset(root, name)
            target = local / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(data)
        del data

        from pyannote.audio import Model
        from pyannote.audio.core.plda import PLDA
        from pyannote.audio.pipelines import SpeakerDiarization

        segmentation = Model.from_pretrained(local / "segmentation" / "pytorch_model.bin")
        embedding = Model.from_pretrained(local / "embedding" / "pytorch_model.bin")
        plda = PLDA.from_pretrained(local / "plda")
        if segmentation is None or embedding is None or plda is None:
            raise ModelNotFoundError("managed community-1 component could not load")
        original = config["pipeline"]["params"]
        pipeline = SpeakerDiarization(
            segmentation=segmentation,
            embedding=embedding,
            plda=plda,
            clustering="VBxClustering",
            embedding_exclude_overlap=original.get("embedding_exclude_overlap", False),
            segmentation_step=original.get("segmentation_step", 0.1),
            embedding_batch_size=original.get("embedding_batch_size", 1),
            segmentation_batch_size=original.get("segmentation_batch_size", 1),
        )
        pipeline.instantiate(config["params"])
        return pipeline


def verify_pipeline_device(pipeline: Any, device: Any) -> None:
    """Check both learned components after the SDK moves them."""
    if device.type in {"cuda", "xpu"}:
        import torch

        if device.index != 0:
            raise BackendUnavailableError("community-1 GPU ordinal mismatch")
        if device.type == "cuda":
            from utteran.capabilities.fingerprints import single_cuda_gpu
            from utteran.capabilities.nvml import cuda_ordinal_uuid

            gpu = single_cuda_gpu()
            if gpu is None or torch.cuda.device_count() != 1 or cuda_ordinal_uuid(0) != gpu.uuid:
                raise BackendUnavailableError("community-1 CUDA device unverified")
        else:
            from utteran.capabilities.fingerprints import (
                native_fingerprints,
                windows_display_adapter,
            )

            adapter = windows_display_adapter()
            if (
                None in native_fingerprints("vulkan", "intel")
                or adapter is None
                or torch.xpu.device_count() != 1
                or torch.xpu.get_device_name(0).strip().casefold() != adapter[0].strip().casefold()
            ):
                raise BackendUnavailableError("community-1 XPU device unverified")
    for model in (pipeline._segmentation.model, pipeline._embedding.model_):
        tensors = (*model.parameters(), *model.buffers())
        if not tensors or any(tensor.device != device for tensor in tensors):
            raise BackendUnavailableError("community-1 component device mismatch")
