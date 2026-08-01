"""pyannote.audio 4.x speaker diarization backend."""

from __future__ import annotations

import gc
import importlib.util
import logging
import wave
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from utteran.config import TokenProvider, default_token_provider
from utteran.diarization.base import DiarizationBackend
from utteran.errors import (
    AudioDecodeError,
    BackendUnavailableError,
    CancelledError,
    ConfigurationError,
    HuggingFaceAuthenticationError,
    HuggingFaceTokenMissingError,
    ModelAgreementError,
    ModelNotFoundError,
    VramExhaustedError,
)
from utteran.models.manager import find_runtime_model
from utteran.types import (
    CancelToken,
    DeviceInfo,
    DiarizationOptions,
    DiarizationResult,
    ProgressCallback,
    ProgressEvent,
    SpeakerTurn,
)


class PyannoteBackend(DiarizationBackend):
    """Convert pyannote 4.x annotations at the backend boundary."""

    name: ClassVar[str] = "pyannote"

    def __init__(self, token_provider: TokenProvider | None = None) -> None:
        self._token_provider = token_provider or default_token_provider()
        self._pipeline: Any | None = None
        self._model_id = ""
        self._device = ""

    @classmethod
    def is_available(cls) -> bool:
        """Return false instead of leaking an optional-import exception."""
        try:
            return importlib.util.find_spec("pyannote.audio") is not None
        except (ImportError, ModuleNotFoundError):
            return False

    @classmethod
    def available_devices(cls) -> list[DeviceInfo]:
        """Report CPU and CUDA devices visible to PyTorch."""
        if not cls.is_available():
            return []
        devices = [DeviceInfo(id="cpu", kind="cpu", name="CPU")]
        try:
            import torch

            for index in range(torch.cuda.device_count()):
                name = torch.cuda.get_device_name(index)
                devices.append(DeviceInfo(id=f"cuda:{index}", kind="cuda", name=name))
        except Exception:
            pass
        return devices

    def load(self, model_id: str, device: str) -> None:
        """Load a local pipeline or an already cached gated Hub model."""
        if not self.is_available():
            raise BackendUnavailableError(
                "pyannote.audio が導入されていません。"
                "`uv sync --extra pyannote` を実行してください。"
            )

        token: str | None = None
        model_path = Path(model_id).expanduser()
        if not model_path.exists():
            managed_model = find_runtime_model(
                self.name,
                model_id,
                token_provider=self._token_provider,
            )
            if managed_model is not None:
                model_path = managed_model
            else:
                token = self._token_provider.get_token()
                if not token:
                    raise HuggingFaceTokenMissingError
                model_path = _resolve_cached_model(model_id, token)

        try:
            import torch
            from pyannote.audio import Pipeline

            selected_device = _select_device(device, torch)
            pipeline = Pipeline.from_pretrained(model_path, token=token)
            if pipeline is None:
                raise ModelNotFoundError(f"話者分離モデル '{model_id}' の設定を読み込めません。")
            try:
                pipeline.to(torch.device(selected_device))
            except Exception:
                if device == "auto" and selected_device.startswith("cuda"):
                    logging.getLogger(__name__).warning(
                        "CUDA で pyannote を初期化できないため CPU へフォールバックします。"
                    )
                    selected_device = "cpu"
                    pipeline.to(torch.device(selected_device))
                else:
                    raise
        except (ConfigurationError, ModelNotFoundError):
            raise
        except Exception as exc:
            _raise_backend_error("モデル読み込み", exc)

        self._pipeline = pipeline
        self._model_id = model_id
        self._device = selected_device

    def diarize(
        self,
        audio_path: Path,
        options: DiarizationOptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> DiarizationResult:
        """Run pyannote and return regular plus exclusive speaker turns."""
        if self._pipeline is None:
            raise BackendUnavailableError("pyannote モデルが読み込まれていません。")
        if cancel is not None:
            cancel.raise_if_cancelled()

        audio = _load_pcm_waveform(audio_path)
        kwargs: dict[str, object] = {}
        if options.num_speakers is not None:
            kwargs["num_speakers"] = options.num_speakers
        else:
            if options.min_speakers is not None:
                kwargs["min_speakers"] = options.min_speakers
            if options.max_speakers is not None:
                kwargs["max_speakers"] = options.max_speakers

        def hook(
            step_name: str,
            _artifact: object,
            _file: Mapping[str, object] | None = None,
            total: int | None = None,
            completed: int | None = None,
        ) -> None:
            if cancel is not None:
                cancel.raise_if_cancelled()
            if progress is not None:
                progress(
                    ProgressEvent(
                        "diarization",
                        float(completed if completed is not None else 1),
                        None if total is None else float(total),
                        str(step_name),
                    )
                )

        if progress is not None:
            progress(ProgressEvent("diarization", 0.0, None, "話者分離を開始します"))
        try:
            output = self._pipeline(audio, hook=hook, **kwargs)
            turns = _annotation_to_turns(output.speaker_diarization)
            exclusive = _annotation_to_turns(output.exclusive_speaker_diarization)
        except CancelledError:
            raise
        except Exception as exc:
            _raise_backend_error("話者分離", exc)

        speakers = {turn.speaker for turn in turns}
        if progress is not None:
            progress(ProgressEvent("diarization", 1.0, 1.0, "話者分離が完了しました"))
        return DiarizationResult(
            turns=turns,
            exclusive_turns=exclusive,
            num_speakers=len(speakers),
            backend=self.name,
            model_id=self._model_id,
            device=self._device,
        )

    def unload(self) -> None:
        """Release pipeline memory and clear the CUDA allocator when used."""
        self._pipeline = None
        gc.collect()
        if self._device.startswith("cuda"):
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:
                pass


def _resolve_cached_model(model_id: str, token: str) -> Path:
    """Resolve a Hub model from cache only and diagnose gated access when absent."""
    from huggingface_hub import HfApi, snapshot_download
    from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError

    try:
        snapshot = snapshot_download(repo_id=model_id, token=token, local_files_only=True)
        return Path(snapshot)
    except LocalEntryNotFoundError:
        pass

    try:
        HfApi().model_info(model_id, token=token)
    except GatedRepoError:
        raise ModelAgreementError(
            f"話者分離モデルの利用条件に同意されていません。"
            f"https://huggingface.co/{model_id} を開き、利用条件に同意してください。"
        ) from None
    except HfHubHTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 401:
            raise HuggingFaceAuthenticationError(
                "Hugging Face トークンが無効です。読み取り権限と値を確認してください。"
            ) from None
        if status == 403:
            raise ModelAgreementError(
                f"話者分離モデルの利用条件に同意されていないか、アクセス権がありません。"
                f"https://huggingface.co/{model_id} を確認してください。"
            ) from None
    except Exception:
        pass

    raise ModelNotFoundError(
        f"話者分離モデル '{model_id}' がローカルにありません。"
        f"ネットワーク接続時に `hf download {model_id}` で事前取得してください。"
    )


def _select_device(requested: str, torch_module: Any) -> str:
    """Resolve CPU/CUDA after a minimal real CUDA allocation probe."""
    if requested == "auto":
        if _torch_cuda_usable(torch_module, 0):
            return "cuda:0"
        if torch_module.cuda.is_available():
            logging.getLogger(__name__).info("PyTorch CUDA を初期化できないため CPU を使用します。")
        return "cpu"
    if requested == "cuda":
        requested = "cuda:0"
    if requested == "cpu":
        return requested
    if requested.startswith("cuda:"):
        try:
            index = int(requested.partition(":")[2])
        except ValueError:
            raise BackendUnavailableError(f"不正な CUDA デバイス指定です: {requested}") from None
        if not _torch_cuda_usable(torch_module, index):
            raise BackendUnavailableError(
                f"明示指定された cuda:{index} を PyTorch で初期化できません。"
                "自動フォールバックは行いません。"
            )
        return f"cuda:{index}"
    raise BackendUnavailableError(f"pyannote が対応していないデバイスです: {requested}")


def _torch_cuda_usable(torch_module: Any, index: int) -> bool:
    """Verify PyTorch CUDA initialization with a one-element allocation."""
    try:
        if (
            index < 0
            or not torch_module.cuda.is_available()
            or index >= torch_module.cuda.device_count()
        ):
            return False
        probe = torch_module.empty(1, device=f"cuda:{index}")
        del probe
        return True
    except Exception:
        return False


def _load_pcm_waveform(audio_path: Path) -> dict[str, Any]:
    """Load normalized PCM16 WAV without relying on TorchCodec's ffmpeg linkage."""
    try:
        with wave.open(str(audio_path), "rb") as audio_file:
            channels = audio_file.getnchannels()
            sample_width = audio_file.getsampwidth()
            sample_rate = audio_file.getframerate()
            frames = audio_file.readframes(audio_file.getnframes())
    except (OSError, wave.Error) as exc:
        raise AudioDecodeError(f"正規化済み WAV を読み込めません: {exc}") from None
    if (channels, sample_width, sample_rate) != (1, 2, 16_000):
        raise AudioDecodeError(
            "話者分離への入力は 16 kHz / mono / PCM 16bit WAV である必要があります。"
        )

    import torch

    samples = torch.frombuffer(bytearray(frames), dtype=torch.int16).to(torch.float32)
    return {"waveform": (samples / 32768.0).unsqueeze(0), "sample_rate": sample_rate}


def _annotation_to_turns(annotation: Any) -> list[SpeakerTurn]:
    """Convert a pyannote Annotation without exposing it to callers."""
    return [
        SpeakerTurn(float(turn.start), float(turn.end), str(speaker))
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]


def _raise_backend_error(operation: str, error: Exception) -> None:
    """Translate pyannote/Torch errors into stable public exceptions."""
    detail = str(error).casefold()
    if "out of memory" in detail or "cuda_error_out_of_memory" in detail:
        raise VramExhaustedError(
            f"{operation}中に VRAM が不足しました。"
            "CPU を指定するか、他の GPU 使用量を減らしてください。"
        ) from None
    if any(name in detail for name in ("cuda", "cudnn", "cublas")):
        raise BackendUnavailableError(
            f"{operation}で CUDA を初期化できません。"
            "PyTorch、CUDA、NVIDIA ドライバーを確認してください。"
        ) from None
    raise BackendUnavailableError(
        f"pyannote の{operation}に失敗しました。モデル、入力音声、実行デバイスを確認してください。"
    ) from None
