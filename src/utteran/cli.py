"""Typer command-line interface for Phase 1."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import Progress, TaskID

from utteran.config import Config, TokenProvider, default_token_provider
from utteran.errors import CancelledError, ConfigurationError, UtteranError
from utteran.logging import configure_logging, mask_secrets, register_secret
from utteran.pipeline import run_pipeline
from utteran.types import PipelineOutcome, ProgressEvent

app = typer.Typer(
    name="utteran",
    help="音声・動画から話者付き文字起こしを生成します。",
    no_args_is_help=True,
)
console = Console()
error_console = Console(stderr=True)


@app.callback()
def main() -> None:
    """Run the utteran command group."""


class RichProgressReporter:
    """Adapt backend-neutral progress events to Rich tasks."""

    def __init__(self, progress: Progress) -> None:
        self._progress = progress
        self._tasks: dict[str, TaskID] = {}

    def __call__(self, event: ProgressEvent) -> None:
        """Create or update one task per pipeline stage."""
        task_id = self._tasks.get(event.stage)
        description = event.message or event.stage
        if task_id is None:
            task_id = self._progress.add_task(description, total=event.total)
            self._tasks[event.stage] = task_id
        self._progress.update(
            task_id,
            description=description,
            completed=event.completed,
            total=event.total,
        )


@app.command()
def transcribe(
    input_path: Annotated[Path, typer.Argument(help="入力する音声または動画ファイル")],
    format_names: Annotated[
        str | None,
        typer.Option("--format", help="出力形式 (srt,vtt,json,txt,md のカンマ区切り)"),
    ] = None,
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", help="出力先ディレクトリ")
    ] = None,
    asr_backend: Annotated[
        str | None, typer.Option("--asr-backend", help="ASR バックエンド")
    ] = None,
    asr_model: Annotated[str | None, typer.Option("--asr-model", help="ASR モデル ID")] = None,
    diarization_backend: Annotated[
        str | None, typer.Option("--diarization-backend", help="話者分離バックエンド")
    ] = None,
    device: Annotated[str | None, typer.Option("--device", help="cpu、cuda、cuda:N、auto")] = None,
    language: Annotated[str | None, typer.Option("--language", help="言語コード")] = None,
    num_speakers: Annotated[
        int | None, typer.Option("--num-speakers", min=1, help="既知の話者数")
    ] = None,
    min_speakers: Annotated[
        int | None, typer.Option("--min-speakers", min=1, help="最小話者数")
    ] = None,
    max_speakers: Annotated[
        int | None, typer.Option("--max-speakers", min=1, help="最大話者数")
    ] = None,
    no_diarization: Annotated[
        bool, typer.Option("--no-diarization", help="話者分離を無効にする")
    ] = False,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="config.toml のパス")
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", help="詳細ログを表示")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="進捗表示を抑制")] = False,
) -> None:
    """Transcribe one audio or video file."""
    try:
        if verbose and quiet:
            raise ConfigurationError("--verbose と --quiet は同時に指定できません。")
        overrides = _cli_overrides(
            format_names=format_names,
            output_dir=output_dir,
            asr_backend=asr_backend,
            asr_model=asr_model,
            diarization_backend=diarization_backend,
            device=device,
            language=language,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
            no_diarization=no_diarization,
            verbose=verbose,
            quiet=quiet,
        )
        config = Config.load(config_path=config_path, cli_overrides=overrides)
        configure_logging(config.general.log_level)
        token_provider = default_token_provider()
        register_secret(token_provider.get_token())
        outcome = _run_with_progress(input_path, config, token_provider, quiet)
    except KeyboardInterrupt:
        _exit_expected(CancelledError())
    except UtteranError as exc:
        _exit_expected(exc)
    except Exception as exc:
        if verbose:
            logging.getLogger(__name__).exception("予期しないエラー")
        error_console.print(f"[red]予期しないエラー:[/red] {mask_secrets(str(exc))}")
        raise typer.Exit(code=1) from None

    for path in outcome.output_paths:
        console.print(f"出力: {path}")


def _run_with_progress(
    input_path: Path,
    config: Config,
    token_provider: TokenProvider,
    quiet: bool,
) -> PipelineOutcome:
    """Run the pipeline with optional Rich progress rendering."""
    if quiet:
        return run_pipeline(input_path, config, token_provider=token_provider)
    with Progress(console=console) as progress:
        reporter = RichProgressReporter(progress)
        return run_pipeline(
            input_path,
            config,
            progress=reporter,
            token_provider=token_provider,
        )


def _cli_overrides(
    *,
    format_names: str | None,
    output_dir: Path | None,
    asr_backend: str | None,
    asr_model: str | None,
    diarization_backend: str | None,
    device: str | None,
    language: str | None,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
    no_diarization: bool,
    verbose: bool,
    quiet: bool,
) -> dict[str, object]:
    """Translate only explicitly supplied CLI options to highest-priority settings."""
    overrides: dict[str, object] = {}
    general: dict[str, object] = {}
    asr: dict[str, object] = {}
    diarization: dict[str, object] = {}
    output: dict[str, object] = {}

    if output_dir is not None:
        general["output_dir"] = output_dir
    if verbose:
        general["log_level"] = "debug"
    elif quiet:
        general["log_level"] = "error"
    if asr_backend is not None:
        asr["backend"] = asr_backend
    if asr_model is not None:
        asr["model"] = asr_model
    if device is not None:
        asr["device"] = device
        diarization["device"] = device
    if language is not None:
        asr["language"] = language
    if diarization_backend is not None:
        diarization["backend"] = diarization_backend
    if num_speakers is not None:
        diarization["num_speakers"] = num_speakers
    if min_speakers is not None:
        diarization["min_speakers"] = min_speakers
    if max_speakers is not None:
        diarization["max_speakers"] = max_speakers
    if no_diarization:
        diarization["enabled"] = False
    if format_names is not None:
        formats = [item.strip().lower() for item in format_names.split(",") if item.strip()]
        if not formats:
            raise ConfigurationError("--format には1つ以上の出力形式を指定してください。")
        output["formats"] = formats

    for name, values in (
        ("general", general),
        ("asr", asr),
        ("diarization", diarization),
        ("output", output),
    ):
        if values:
            overrides[name] = values
    return overrides


def _exit_expected(error: UtteranError) -> None:
    """Print an actionable expected error without a traceback."""
    error_console.print(f"[red]エラー:[/red] {mask_secrets(str(error))}")
    raise typer.Exit(code=error.exit_code) from None


if __name__ == "__main__":
    app()
