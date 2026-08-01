"""Audio extraction and normalization through an external ffmpeg executable."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from platformdirs import user_data_dir

from utteran.errors import AudioDecodeError, FfmpegNotFoundError, InputFileNotFoundError
from utteran.types import CancelToken, ProgressCallback, ProgressEvent


def find_ffmpeg(configured_path: Path | None = None) -> Path:
    """Find ffmpeg in config, PATH, then the application data bundle directory."""
    if configured_path is not None and configured_path.is_file():
        return configured_path

    from_path = shutil.which("ffmpeg")
    if from_path:
        return Path(from_path)

    bundled_dir = Path(user_data_dir("utteran")) / "bin"
    for executable_name in ("ffmpeg.exe", "ffmpeg"):
        candidate = bundled_dir / executable_name
        if candidate.is_file():
            return candidate

    raise FfmpegNotFoundError


def build_ffmpeg_command(ffmpeg_path: Path, input_path: Path, output_path: Path) -> list[str]:
    """Build the deterministic 16 kHz mono PCM16 normalization command."""
    return [
        str(ffmpeg_path),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(output_path),
    ]


def normalize_audio(
    input_path: Path,
    output_path: Path,
    *,
    ffmpeg_path: Path | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelToken | None = None,
) -> Path:
    """Extract and normalize one media file to 16 kHz mono PCM16 WAV."""
    if not input_path.is_file():
        raise InputFileNotFoundError(f"入力ファイルが見つかりません: {input_path}")
    executable = find_ffmpeg(ffmpeg_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if cancel is not None:
        cancel.raise_if_cancelled()
    if progress is not None:
        progress(ProgressEvent("audio", 0.0, 1.0, "音声を抽出しています"))

    with tempfile.NamedTemporaryFile(
        prefix=".utteran-",
        suffix=".wav",
        dir=output_path.parent,
        delete=False,
    ) as temporary_file:
        temporary_path = Path(temporary_file.name)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            build_ffmpeg_command(executable, input_path, temporary_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        while True:
            if cancel is not None and cancel.is_cancelled:
                _stop_process(process)
                cancel.raise_if_cancelled()
            try:
                _, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue

        if process.returncode != 0:
            detail = _last_error_line(stderr.decode("utf-8", errors="replace"))
            suffix = f" ffmpeg: {detail}" if detail else ""
            raise AudioDecodeError(f"音声をデコードできません: {input_path.name}.{suffix}")
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise AudioDecodeError(f"ffmpeg が空の音声を生成しました: {input_path.name}")
        temporary_path.replace(output_path)
    except KeyboardInterrupt:
        if process is not None and process.poll() is None:
            _stop_process(process)
        raise
    except OSError as exc:
        if process is not None and process.poll() is None:
            _stop_process(process)
        raise AudioDecodeError(f"ffmpeg を実行できません: {exc}") from None
    finally:
        temporary_path.unlink(missing_ok=True)

    if progress is not None:
        progress(ProgressEvent("audio", 1.0, 1.0, "音声抽出が完了しました"))
    return output_path


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Terminate ffmpeg, escalating only when it does not stop promptly."""
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _last_error_line(stderr: str) -> str:
    """Return a bounded, useful ffmpeg diagnostic line."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1][:500] if lines else ""
