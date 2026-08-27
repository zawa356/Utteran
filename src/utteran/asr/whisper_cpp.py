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
from utteran.logging import mask_secrets, structured_event, write_raw_subprocess_log
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
_OPENVINO_FAILURE_MARKERS = (
    "in openvino encoder compile routine",
    "openvino encoder init failed",
    "failed to initialize openvino",
)
_OPENVINO_SUCCESS_MARKERS = (
    "loading openvino model from",
    "openvino model loaded",
    "openvino encoder initialized",
    "openvino encoder init succeeded",
)


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
        self._fallback_attempted = False

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
        self._fallback_attempted = False
        structured_event(
            "asr_backend_resolved",
            backend=self.name,
            variant=requested,
            executable=str(self._executable),
            model=str(self._model_path),
            fallback_allowed=self._allow_fallback,
        )

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
            run_settings = self.settings
            if run_settings.vad:
                resolved_vad = _resolve_vad_model(self.settings)
                if resolved_vad is None:
                    logging.getLogger(__name__).warning(
                        "Silero VADモデルが未取得のため、この実行だけVADを無効にします。"
                        "モデル管理またはセットアップウィザードから取得できます。"
                    )
                    run_settings = self.settings.model_copy(update={"vad": False})
                else:
                    staged_vad = _stage_model(resolved_vad, temporary_path / "vad-model")
                    run_settings = self.settings.model_copy(update={"vad_model": staged_vad})
            command = build_command(
                self._executable,
                staged_model,
                audio_path,
                output_prefix,
                self._entry,
                run_settings,
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
                structured_event(
                    "variant_fallback",
                    from_variant=self._variant,
                    to_variant=fallback,
                    reason=type(error).__name__,
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
                    run_settings,
                    fallback,
                    options,
                )
                stderr = _run_process(command, fallback, progress, cancel)
            output_path = output_prefix.with_suffix(".json")
            if not output_path.is_file():
                raise BackendUnavailableError(
                    "whisper-cliがJSONを生成しませんでした: " + summarize_subprocess_error(stderr)
                )
            raw_output = output_path.read_bytes()
            try:
                output_text = raw_output.decode("utf-8")
            except UnicodeDecodeError as error:
                logging.getLogger(__name__).warning(
                    "whisper.cpp JSONの不正UTF-8を置換しました: byte_offset=%d", error.start
                )
                output_text = raw_output.decode("utf-8", errors="replace")
            data = json.loads(output_text)
        return _convert_result(
            data,
            self._entry,
            self._device,
            options.word_timestamps,
            repetition_limit=self.settings.repetition_limit,
            max_word_duration_seconds=self.settings.max_word_duration_seconds,
        )

    def unload(self) -> None:
        self._entry = None
        self._model_path = None
        self._executable = None
        self._backends = {}
        self._fallback_attempted = False

    def _fallback_variant(self, detail: str) -> str | None:
        if (
            not self._allow_fallback
            or self._fallback_attempted
            or not is_gpu_initialization_failure(detail)
        ):
            return None
        order = ("openvino_vulkan", "vulkan", "openvino")
        try:
            start = order.index(self._variant) + 1
        except ValueError:
            return None
        for name in order[start:]:
            entry = self._backends.get(name)
            if isinstance(entry, dict) and Path(str(entry.get("executable", ""))).is_file():
                self._fallback_attempted = True
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
    """Build only arguments verified against patched whisper.cpp v1.9.2 cli.cpp."""
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
        vad_model = _resolve_vad_model(settings)
        if vad_model is not None:
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
    elif os.environ.get("UTTERAN_DEBUG_NO_FLASH_ATTN") == "1":
        # Phase 3dの原因切り分け専用。公開設定にはせず、通常経路へ影響させない。
        command.append("--no-flash-attn")
    return command


def _resolve_vad_model(settings: WhisperCppConfig) -> Path | None:
    if settings.vad_model is not None:
        vad_model = settings.vad_model.expanduser().resolve()
    else:
        vad_entry = get_model("whisper-cpp-vad:silero-v6.2.0")
        installed, _managed = ModelManager().find_installed(vad_entry)
        if installed is None:
            return None
        vad_model = installed / (vad_entry.artifact_filename or "")
    if not vad_model.is_file():
        raise ModelNotFoundError(
            f"whisper.cpp VADモデルが見つかりません: {vad_model}。"
            "`utteran models download whisper-cpp-vad:silero-v6.2.0`で取得できます。"
        )
    return vad_model


def parse_progress(line: str) -> int | None:
    match = _PROGRESS.search(line)
    return None if match is None else min(int(match.group(1)), 100)


def is_gpu_initialization_failure(detail: str) -> bool:
    """Recognize bounded v1.9.2/OpenVINO/Vulkan initialization diagnostics."""
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


def parse_openvino_ir_status(stderr: str) -> bool | None:
    """Classify the centralized whisper.cpp OpenVINO initialization diagnostics."""
    folded = stderr.casefold()
    if any(marker in folded for marker in _OPENVINO_FAILURE_MARKERS):
        return False
    if any(marker in folded for marker in _OPENVINO_SUCCESS_MARKERS):
        return True
    return None


def summarize_subprocess_error(stderr: str) -> str:
    """Keep diagnostic lines while excluding possible recognition output."""
    markers = (
        "error",
        "failed",
        "exception",
        "openvino",
        "vulkan",
        "could not open",
        "not found",
    )
    diagnostics = [
        line.strip()
        for line in stderr.splitlines()
        if any(marker in line.casefold() for marker in markers)
    ]
    return mask_secrets(" | ".join(diagnostics[-5:]) or "詳細はrawログの明示有効化後に確認")


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
    creationflags = (
        int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
    )
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        creationflags=creationflags,
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
    write_raw_subprocess_log("whisper-cpp", stderr)
    if variant in {"openvino", "openvino_vulkan"}:
        status = parse_openvino_ir_status(stderr)
        model_path = Path(command[command.index("-m") + 1])
        ir_path = model_path.with_name(model_path.stem + "-encoder-openvino.xml")
        structured_event(
            "openvino_ir_loaded",
            path=str(ir_path),
            success=status is True and process.returncode == 0,
            detected=status is not None,
            variant=variant,
        )
    if process.returncode:
        raise BackendUnavailableError(
            f"whisper-cliが失敗しました(exit={process.returncode}): "
            + summarize_subprocess_error(stderr)
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
    repetition_limit: int = 10,
    max_word_duration_seconds: float = 3.0,
) -> TranscriptionResult:
    segments: list[Segment] = []
    dtw_found = False
    discarded_segments = 0
    discarded_words = 0
    discarded_long_words = 0
    discarded_long_word_segments = 0
    discarded_repetitions = 0
    previous_text = ""
    consecutive_repetitions = 0
    timestamp_statistics = _token_timestamp_statistics(data)
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
        if requested_words and any(
            word.end - word.start > max_word_duration_seconds for word in converted_words
        ):
            discarded_long_words += len(converted_words)
            discarded_long_word_segments += 1
            continue
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
    if (
        discarded_segments
        or discarded_words
        or discarded_long_words
        or discarded_long_word_segments
        or discarded_repetitions
    ):
        logging.getLogger(__name__).warning(
            "whisper.cppの無効出力を除外しました: zero_segments=%d, zero_words=%d, "
            "long_words=%d, long_word_segments=%d (max_word_duration=%.3fs), "
            "repeated_segments=%d "
            "(repetition_limit=%d; 正当な反復も除外される可能性があります)",
            discarded_segments,
            discarded_words,
            discarded_long_words,
            discarded_long_word_segments,
            max_word_duration_seconds,
            discarded_repetitions,
            repetition_limit,
        )
    if requested_words and not dtw_found:
        logging.getLogger(__name__).warning(
            "DTWが有効にならずt_dtwが全て-1のため、単語時刻を破棄してsegment単位へ退避します。"
        )
        for segment in segments:
            segment.words = []
    structured_event(
        "asr_word_timestamp_statistics",
        requested=requested_words,
        retained_word_count=sum(len(segment.words) for segment in segments),
        discarded_zero_word_count=discarded_words,
        discarded_long_word_count=discarded_long_words,
        discarded_long_word_segment_count=discarded_long_word_segments,
        **timestamp_statistics,
    )
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


def _token_timestamp_statistics(data: dict[str, Any]) -> dict[str, int]:
    """Count timestamp coordinate patterns without retaining or logging token text."""
    statistics = {
        "raw_segment_count": 0,
        "token_count": 0,
        "dtw_nonnegative_count": 0,
        "dtw_absolute_count": 0,
        "dtw_relative_count": 0,
        "offset_count": 0,
        "offset_positive_count": 0,
        "offset_absolute_count": 0,
        "offset_relative_count": 0,
    }
    for raw in data.get("transcription", []):
        offsets = raw.get("offsets", {})
        segment_start = float(offsets.get("from", 0.0)) / 1000.0
        segment_end = float(offsets.get("to", 0.0)) / 1000.0
        segment_duration = max(0.0, segment_end - segment_start)
        statistics["raw_segment_count"] += 1
        for token in raw.get("tokens", []):
            statistics["token_count"] += 1
            try:
                dtw = int(token.get("t_dtw", -1))
            except (TypeError, ValueError):
                dtw = -1
            if dtw >= 0:
                dtw_seconds = dtw * 0.01
                statistics["dtw_nonnegative_count"] += 1
                statistics["dtw_absolute_count"] += int(segment_start <= dtw_seconds <= segment_end)
                statistics["dtw_relative_count"] += int(0.0 <= dtw_seconds <= segment_duration)
            token_offsets = token.get("offsets")
            if not isinstance(token_offsets, dict):
                continue
            try:
                start = float(token_offsets["from"]) / 1000.0
                end = float(token_offsets["to"]) / 1000.0
            except (KeyError, TypeError, ValueError):
                continue
            statistics["offset_count"] += 1
            statistics["offset_positive_count"] += int(end > start)
            statistics["offset_absolute_count"] += int(segment_start <= start < end <= segment_end)
            statistics["offset_relative_count"] += int(0.0 <= start < end <= segment_duration)
    return statistics


def _choose_variant(backends: dict[str, Any]) -> str:
    for name in ("vulkan", "openvino_vulkan", "openvino", "cpu"):
        entry = backends.get(name)
        if isinstance(entry, dict) and Path(str(entry.get("executable", ""))).is_file():
            return name
    raise BackendUnavailableError("実行可能なwhisper.cpp構成がありません。")
