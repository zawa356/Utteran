"""whisper.cpp ASR backend using the Phase 3a native manifest."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, ClassVar

from utteran.asr.base import ASRBackend
from utteran.asr.whisper_cpp_words import has_dtw_timestamps, tokens_to_words
from utteran.config import WhisperCppConfig
from utteran.errors import BackendUnavailableError, CancelledError, ModelNotFoundError
from utteran.logging import mask_secrets
from utteran.models.catalog import ModelEntry, get_model
from utteran.models.manager import ModelManager
from utteran.native import NativeBuilder, resolve_native_dir, resolve_runtime_library_dirs
from utteran.types import (
    ASROptions,
    CancelToken,
    DeviceInfo,
    ProgressCallback,
    ProgressEvent,
    Segment,
    TranscriptionResult,
)

_PROGRESS = re.compile(r"progress\s*=\s*(\d{1,3})%")


class WhisperCppBackend(ASRBackend):
    """Run one whisper-cli process per normalized input file."""

    name: ClassVar[str] = "whisper-cpp"

    def __init__(
        self, settings: WhisperCppConfig | None = None, *, allow_fallback: bool | None = None
    ) -> None:
        self.settings = settings or WhisperCppConfig()
        self._allow_fallback = (
            self.settings.variant == "auto" if allow_fallback is None else allow_fallback
        )
        self._entry: ModelEntry | None = None
        self._model_path: Path | None = None
        self._executable: Path | None = None
        self._variant = ""
        self._device = ""
        self._backends: dict[str, Any] = {}

    @classmethod
    def is_available(cls) -> bool:
        status = NativeBuilder(resolve_native_dir()).status()
        runnable = status.get("runnable")
        return isinstance(runnable, dict) and any(bool(value) for value in runnable.values())

    @classmethod
    def available_devices(cls) -> list[DeviceInfo]:
        status = NativeBuilder(resolve_native_dir()).status()
        runnable = status.get("runnable")
        if not isinstance(runnable, dict):
            return []
        return [
            DeviceInfo(name, "cpu" if name == "cpu" else "other", name)
            for name in ("cpu", "openvino", "vulkan", "openvino_vulkan")
            if runnable.get(name)
        ]

    def load(self, model_id: str, device: str, compute_type: str) -> None:
        del compute_type
        try:
            entry = get_model(model_id, backend=self.name)
        except Exception:
            raise ModelNotFoundError(
                f"whisper.cppモデルがカタログにありません: {model_id}"
            ) from None
        manager = ModelManager()
        directory, _managed = manager.find_installed(entry)
        if directory is None or not entry.artifact_filename:
            raise ModelNotFoundError(
                f"モデル未導入: {entry.key}。"
                f"`utteran models download {entry.key}`を実行してください。"
            )
        model_path = directory / entry.artifact_filename
        builder = NativeBuilder(resolve_native_dir())
        manifest = builder.load_manifest()
        backends = manifest.get("backends")
        if not isinstance(backends, dict):
            raise BackendUnavailableError(
                "ネイティブビルドがありません。`utteran native build`を実行してください。"
            )
        requested = self.settings.variant if self.settings.variant != "auto" else device
        if requested == "auto":
            requested = _choose_variant(backends)
        backend = backends.get(requested)
        executable = backend.get("executable") if isinstance(backend, dict) else None
        if not executable or not Path(str(executable)).is_file():
            raise BackendUnavailableError(
                f"whisper.cpp構成を利用できません: {requested}。"
                f"`utteran native build --variant {requested}`を実行してください。"
            )
        self._entry = entry
        self._model_path = model_path
        self._executable = Path(str(executable))
        self._variant = requested
        self._device = requested
        self._backends = backends

    def transcribe(
        self,
        audio_path: Path,
        options: ASROptions,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
    ) -> TranscriptionResult:
        if self._entry is None or self._model_path is None or self._executable is None:
            raise BackendUnavailableError("whisper.cppバックエンドがloadされていません。")
        with tempfile.TemporaryDirectory(prefix="utteran-whisper-cpp-") as temporary:
            temporary_path = Path(temporary)
            output_prefix = temporary_path / "result"
            staged_model = _stage_model(self._model_path, temporary_path / "model")
            command = build_command(
                self._executable,
                staged_model,
                audio_path,
                output_prefix,
                self._entry,
                self.settings,
                self._variant,
                options,
            )
            try:
                stderr = _run_process(command, self._variant, progress, cancel)
            except BackendUnavailableError as error:
                fallback = self._fallback_variant(str(error))
                if fallback is None:
                    raise
                logging.getLogger(__name__).warning(
                    "%s初期化失敗のため%sへ1回だけフォールバックします。理由: %s",
                    self._variant,
                    fallback,
                    mask_secrets(str(error)),
                )
                self._variant = fallback
                self._device = fallback
                backend = self._backends[fallback]
                self._executable = Path(str(backend["executable"]))
                command = build_command(
                    self._executable,
                    staged_model,
                    audio_path,
                    output_prefix,
                    self._entry,
                    self.settings,
                    fallback,
                    options,
                )
                stderr = _run_process(command, fallback, progress, cancel)
            output_path = output_prefix.with_suffix(".json")
            if not output_path.is_file():
                raise BackendUnavailableError(
                    "whisper-cliがJSONを生成しませんでした: " + mask_secrets(stderr[-500:])
                )
            data = json.loads(output_path.read_text(encoding="utf-8"))
        return _convert_result(
            data,
            self._entry,
            self._device,
            options.word_timestamps,
            repetition_limit=self.settings.repetition_limit,
        )

    def unload(self) -> None:
        self._entry = None
        self._model_path = None
        self._executable = None
        self._backends = {}

    def _fallback_variant(self, detail: str) -> str | None:
        if not self._allow_fallback or not is_gpu_initialization_failure(detail):
            return None
        order = ("openvino_vulkan", "vulkan", "openvino")
        try:
            start = order.index(self._variant) + 1
        except ValueError:
            return None
        for name in order[start:]:
            entry = self._backends.get(name)
            if isinstance(entry, dict) and Path(str(entry.get("executable", ""))).is_file():
                return name
        return None


def build_command(
    executable: Path,
    model: Path,
    audio: Path,
    output_prefix: Path,
    entry: ModelEntry,
    settings: WhisperCppConfig,
    variant: str,
    options: ASROptions,
) -> list[str]:
    """Build only arguments verified against whisper.cpp v1.9.1 cli.cpp."""
    command = [
        str(executable),
        "-m",
        str(model),
        "-f",
        str(audio),
        "-l",
        options.language or "auto",
        "-bs",
        str(options.beam_size),
        "-ojf",
        "-of",
        str(output_prefix),
        "-pp",
    ]
    if settings.threads:
        command.extend(["-t", str(settings.threads)])
    if options.initial_prompt:
        command.extend(["--prompt", options.initial_prompt])
    if settings.no_context:
        command.extend(["--max-context", "0"])
    command.extend(
        [
            "--entropy-thold",
            str(settings.entropy_threshold),
            "--logprob-thold",
            str(settings.logprob_threshold),
            "--no-speech-thold",
            str(settings.no_speech_threshold),
            "--temperature",
            str(settings.temperature),
            "--temperature-inc",
            str(settings.temperature_increment),
        ]
    )
    if settings.vad:
        if settings.vad_model is None:
            raise ModelNotFoundError(
                "whisper.cpp VADが有効ですがvad_modelが未設定です。"
                "Silero GGML VADモデルを取得し[asr.whisper_cpp].vad_modelへ指定してください。"
            )
        vad_model = settings.vad_model.expanduser().resolve()
        if not vad_model.is_file():
            raise ModelNotFoundError(f"whisper.cpp VADモデルが見つかりません: {vad_model}")
        command.extend(
            [
                "--vad",
                "--vad-model",
                str(vad_model),
                "--vad-threshold",
                str(settings.vad_threshold),
            ]
        )
    if variant in {"openvino", "openvino_vulkan"}:
        command.extend(["-oved", "GPU"])
    if variant == "cpu":
        command.append("--no-gpu")
    elif variant in {"vulkan", "openvino_vulkan"}:
        command.extend(["--device", "0"])
    if options.word_timestamps:
        preset = entry.dtw_preset if settings.dtw == "auto" else settings.dtw
        if preset:
            command.extend(["--dtw", preset, "--no-flash-attn"])
    return command


def parse_progress(line: str) -> int | None:
    match = _PROGRESS.search(line)
    return None if match is None else min(int(match.group(1)), 100)


def is_gpu_initialization_failure(detail: str) -> bool:
    """Recognize bounded v1.9.1/OpenVINO/Vulkan initialization diagnostics."""
    folded = detail.casefold()
    return any(
        marker in folded
        for marker in (
            "failed to initialize",
            "failed to create",
            "vulkan device",
            "openvino encoder",
            "could not open the file",
            "in openvino encoder compile routine",
        )
    )


def _run_process(
    command: list[str],
    variant: str,
    progress: ProgressCallback | None,
    cancel: CancelToken | None,
) -> str:
    environment = os.environ.copy()
    runtime_dirs = resolve_runtime_library_dirs(variant)
    if runtime_dirs:
        environment["PATH"] = (
            os.pathsep.join(map(str, runtime_dirs)) + os.pathsep + environment.get("PATH", "")
        )
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def read_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=read_stderr, daemon=True).start()
    captured: list[str] = []
    finished_reader = False
    while process.poll() is None or not finished_reader:
        if cancel is not None and cancel.is_cancelled:
            _terminate_tree(process)
            raise CancelledError
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            continue
        if line is None:
            finished_reader = True
            continue
        captured.append(line)
        percent = parse_progress(line)
        if percent is not None and progress is not None:
            progress(ProgressEvent("asr", percent, 100, "whisper.cpp文字起こし中"))
    stderr = "".join(captured)
    if process.returncode:
        raise BackendUnavailableError(
            f"whisper-cliが失敗しました(exit={process.returncode}): " + mask_secrets(stderr[-500:])
        )
    return stderr


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def _stage_model(source: Path, directory: Path) -> Path:
    """Expose GGML and adjacent IR through an ASCII-safe temporary path on Windows."""
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / source.name
    _link_or_copy(source, destination)
    stem = source.stem + "-encoder-openvino"
    for suffix in (".xml", ".bin"):
        companion = source.with_name(stem + suffix)
        if companion.is_file():
            _link_or_copy(companion, directory / companion.name)
    return destination


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _convert_result(
    data: dict[str, Any],
    entry: ModelEntry,
    device: str,
    requested_words: bool,
    *,
    repetition_limit: int = 4,
) -> TranscriptionResult:
    segments: list[Segment] = []
    dtw_found = False
    discarded_segments = 0
    discarded_words = 0
    discarded_repetitions = 0
    previous_text = ""
    consecutive_repetitions = 0
    for raw in data.get("transcription", []):
        offsets = raw.get("offsets", {})
        start = float(offsets.get("from", 0)) / 1000.0
        end = float(offsets.get("to", 0)) / 1000.0
        if end <= start:
            discarded_segments += 1
            continue
        tokens = raw.get("tokens", [])
        dtw_found = dtw_found or has_dtw_timestamps(tokens)
        converted_words = (
            tokens_to_words(tokens, segment_start=start, segment_end=end) if requested_words else []
        )
        words = [word for word in converted_words if word.end > word.start]
        discarded_words += len(converted_words) - len(words)
        text = str(raw.get("text", ""))
        normalized_text = text.strip()
        if normalized_text and normalized_text == previous_text:
            consecutive_repetitions += 1
        else:
            previous_text = normalized_text
            consecutive_repetitions = 1
        if repetition_limit and normalized_text and consecutive_repetitions > repetition_limit:
            discarded_repetitions += 1
            continue
        segments.append(Segment(start, end, text, words))
    if discarded_segments or discarded_words or discarded_repetitions:
        logging.getLogger(__name__).warning(
            "whisper.cppの無効出力を除外しました: zero_segments=%d, zero_words=%d, "
            "repeated_segments=%d (repetition_limit=%d; 正当な反復も除外される可能性があります)",
            discarded_segments,
            discarded_words,
            discarded_repetitions,
            repetition_limit,
        )
    if requested_words and not dtw_found:
        logging.getLogger(__name__).warning(
            "DTWが有効にならずt_dtwが全て-1のため、単語時刻を破棄してsegment単位へ退避します。"
        )
        for segment in segments:
            segment.words = []
    result = data.get("result", {})
    duration = max((segment.end for segment in segments), default=0.0)
    return TranscriptionResult(
        segments,
        str(result.get("language", "unknown")),
        duration,
        "whisper-cpp",
        entry.model_id,
        device,
    )


def _choose_variant(backends: dict[str, Any]) -> str:
    for name in ("vulkan", "openvino_vulkan", "openvino", "cpu"):
        entry = backends.get(name)
        if isinstance(entry, dict) and Path(str(entry.get("executable", ""))).is_file():
            return name
    raise BackendUnavailableError("実行可能なwhisper.cpp構成がありません。")
