"""Thin Typer command-line interface over utteran's reusable core APIs."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Never, TypeVar, cast

import typer
from rich.console import Console
from rich.progress import Progress, TaskID
from rich.table import Table

from utteran.audio import find_ffmpeg
from utteran.batch import BatchSummary, discover_inputs, run_batch
from utteran.benchmark import (
    BenchmarkMeasurement,
    apply_variant,
    benchmark_warning,
    parse_durations,
    prepared_audio_lengths,
    run_benchmark,
    wav_duration,
)
from utteran.config import (
    Config,
    TokenProvider,
    default_config_path,
    default_token_provider,
    initialize_config,
)
from utteran.devices import DeviceReport, detect_devices
from utteran.diarization.registry import preflight_diarization_backend
from utteran.errors import (
    CancelledError,
    ConfigurationError,
    ModelNotFoundError,
    UtteranError,
)
from utteran.jobs import JobStore
from utteran.logging import configure_logging, mask_secrets, register_secret
from utteran.memory import (
    CALIBRATION_MIN_POINTS,
    CALIBRATION_MIN_SPAN_MINUTES,
    DEFAULT_MODELS,
    CalibrationStore,
)
from utteran.models.catalog import ModelEntry, get_model
from utteran.models.manager import ModelManager, ModelStatus
from utteran.native import VARIANT_NAMES, NativeBuilder, resolve_native_dir
from utteran.pipeline import run_pipeline
from utteran.profiles import (
    current_profile_name,
    list_profile_statuses,
    resolve_venv_root,
)
from utteran.types import CancelToken, PipelineOutcome, ProgressEvent

T = TypeVar("T")

app = typer.Typer(
    name="utteran",
    help="音声・動画から話者付き文字起こしを生成します。",
    no_args_is_help=True,
)
models_app = typer.Typer(help="推論モデルを明示的に管理します。", no_args_is_help=True)
jobs_app = typer.Typer(help="保存済みジョブを確認・削除します。", no_args_is_help=True)
config_app = typer.Typer(help="utteran の設定ファイルを管理します。", no_args_is_help=True)
profiles_app = typer.Typer(help="実行環境プロファイル (venv) を確認します。", no_args_is_help=True)
native_app = typer.Typer(help="whisper.cpp ネイティブビルドを管理します。", no_args_is_help=True)
memory_app = typer.Typer(help="メモリ推定のキャリブレーションを管理します。", no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(jobs_app, name="jobs")
app.add_typer(config_app, name="config")
app.add_typer(profiles_app, name="profiles")
app.add_typer(native_app, name="native")
app.add_typer(memory_app, name="memory")


@memory_app.command("show")
def memory_show_command() -> None:
    """Show bundled/local peak models without exposing media identifiers."""
    store = CalibrationStore()
    points = store.load()
    table = Table("stage", "backend/device", "base", "slope", "source", "saved/model points")
    keys = set(DEFAULT_MODELS) | {(p.stage, p.backend, p.device_kind) for p in points}
    for stage, backend, kind in sorted(keys):
        model = store.model(stage, backend, kind)
        if model is None:
            continue
        saved = sum(
            (point.stage, point.backend, point.device_kind) == (stage, backend, kind)
            for point in points
        )
        table.add_row(
            stage,
            f"{backend}/{kind}",
            f"{model.base_gib:.3f} GiB",
            f"{model.gib_per_minute:.6f} GiB/min",
            model.source,
            f"{saved}/{model.sample_count}",
        )
    console.print(table)
    console.print(
        f"保存点: {len(points)} / ローカル式への切替: 同一構成{CALIBRATION_MIN_POINTS}点以上・"
        f"長さspan {CALIBRATION_MIN_SPAN_MINUTES:g}分以上 / "
        f"保存先: {store.path}"
    )


@memory_app.command("reset")
def memory_reset_command() -> None:
    """Delete all profile-independent memory calibration points."""
    removed = CalibrationStore().reset()
    console.print("キャリブレーションを削除しました。" if removed else "保存データはありません。")


console = Console()
error_console = Console(stderr=True)


@app.callback()
def main() -> None:
    """Run the utteran command group."""
    # On Windows, sys.stdout/stderr default to the system locale codepage (e.g. cp932)
    # whenever they are not attached to a real console (piped, redirected to a file, or
    # captured by a subprocess). typer.echo(json.dumps(..., ensure_ascii=False)) then
    # writes real Japanese characters (e.g. auto-selection notes) through that codepage,
    # silently corrupting them or emitting bytes that a UTF-8 reader parses as invalid
    # JSON. Force both streams to UTF-8 so `--json` output stays machine-readable
    # regardless of console state, matching the UTF-8 discipline already enforced for
    # subprocess I/O elsewhere in this codebase (native.py, setup.ps1 child processes).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")


@app.command()
def benchmark(
    audio: Annotated[Path, typer.Option("--audio", help="測定用WAV (実データは明示指定)")],
    variants: Annotated[
        str, typer.Option(help="構成名のカンマ区切り")
    ] = "cpu,openvino,vulkan,openvino_vulkan,faster-whisper",
    word_timestamps: Annotated[str, typer.Option(help="auto|always|never")] = "auto",
    repeat: Annotated[int, typer.Option(min=1)] = 3,
    warmup: Annotated[int, typer.Option(min=0)] = 1,
    durations: Annotated[
        str,
        typer.Option(help="測定する秒数のカンマ区切り。fullは入力全体 (例: 180,900,full)"),
    ] = "full",
    json_path: Annotated[Path | None, typer.Option("--json", help="JSON出力先")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="最速whisper.cpp構成を設定へ保存")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Measure ASR variants without creating pipeline jobs or retaining recognized text."""
    if word_timestamps not in {"auto", "always", "never"}:
        raise typer.BadParameter(
            "auto|always|neverを指定してください", param_hint="--word-timestamps"
        )
    selected = tuple(item.strip() for item in variants.split(",") if item.strip())
    config = Config.load(config_path=config_path)
    console.print("他の高負荷処理を停止してください。結果に文字起こし内容は保存しません。")
    try:
        source_duration = wav_duration(audio)
        requested_durations = parse_durations(durations, source_duration)
        measurements: list[BenchmarkMeasurement] = []
        with prepared_audio_lengths(audio, requested_durations) as prepared:
            for measured_duration, measured_audio in prepared:
                warning = benchmark_warning(measured_duration)
                if warning:
                    console.print(f"[yellow]警告 ({measured_duration:.3f}秒): {warning}[/yellow]")
                results = run_benchmark(
                    config,
                    measured_audio,
                    selected,
                    word_timestamps=word_timestamps == "always",
                    repeat=repeat,
                    warmup=warmup,
                )
                measurements.append(
                    BenchmarkMeasurement(measured_duration, warning, tuple(results))
                )
    except (OSError, ValueError, UtteranError) as exc:
        error_console.print(f"エラー: {mask_secrets(str(exc))}")
        raise typer.Exit(3) from None
    if not any(measurement.results for measurement in measurements):
        error_console.print("エラー: 指定した構成に利用可能なバックエンド/モデルがありません。")
        raise typer.Exit(3)
    table = Table("音声長", "構成", "中央値", "load中央値", "実時間比", "peak RAM")
    for measurement in measurements:
        for result in measurement.results:
            peak = (
                "取得不可"
                if result.peak_ram_bytes is None
                else f"{result.peak_ram_bytes / 1024**3:.2f} GiB"
            )
            table.add_row(
                f"{measurement.audio_duration_seconds:.3f}s",
                result.variant,
                f"{result.median_total_seconds:.3f}s",
                f"{result.median_load_seconds:.3f}s",
                f"{result.realtime_factor:.3f}x",
                peak,
            )
    console.print(table)
    payload = {
        "schema_version": 2,
        "source_audio_duration_seconds": source_duration,
        "measurements": [measurement.as_dict() for measurement in measurements],
    }
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if apply:
        longest = max(measurements, key=lambda item: item.audio_duration_seconds)
        candidates = [item for item in longest.results if item.variant != "faster-whisper"]
        if not candidates:
            raise typer.BadParameter("--applyにはwhisper.cpp構成が必要です")
        fastest = min(candidates, key=lambda item: item.median_total_seconds)
        applied_duration = longest.audio_duration_seconds
        apply_variant(config_path or default_config_path(), fastest.variant, applied_duration)
        console.print(f"設定へ適用しました: {fastest.variant} ({applied_duration:.3f}秒の測定)")


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
    input_path: Annotated[Path, typer.Argument(help="入力する音声・動画ファイルまたはフォルダ")],
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
    diarization_model: Annotated[
        str | None, typer.Option("--diarization-model", help="話者分離モデル ID またはローカルパス")
    ] = None,
    device: Annotated[
        str | None,
        typer.Option("--device", help="ASRと話者分離に共通の実行デバイス (互換用)"),
    ] = None,
    asr_device: Annotated[
        str | None, typer.Option("--asr-device", help="ASRの実行デバイス/構成")
    ] = None,
    diarization_device: Annotated[
        str | None,
        typer.Option("--diarization-device", help="話者分離の実行デバイス"),
    ] = None,
    language: Annotated[
        str | None, typer.Option("--language", help="言語コードまたは auto (自動判定)")
    ] = None,
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
    recursive: Annotated[bool, typer.Option("--recursive", help="フォルダを再帰的に処理")] = False,
    include: Annotated[
        list[str] | None, typer.Option("--include", help="対象に含める glob (複数指定可)")
    ] = None,
    exclude: Annotated[
        list[str] | None, typer.Option("--exclude", help="対象から除く glob (複数指定可)")
    ] = None,
    resume: Annotated[
        bool, typer.Option("--resume/--no-resume", help="完了済みステージを再利用")
    ] = True,
    force: Annotated[bool, typer.Option("--force", help="すべてのステージを再実行")] = False,
    force_unlock: Annotated[
        bool, typer.Option("--force-unlock", help="既存ジョブロックを強制解除")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="処理対象だけを表示")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="モデル取得などの確認を省略")] = False,
    config_path: Annotated[Path | None, typer.Option("--config", help="config.toml のパス")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", help="詳細ログを表示")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="進捗表示を抑制")] = False,
) -> None:
    """Transcribe one file or a stable sequential folder batch."""
    _restore_windows_ctrl_c()
    try:
        if verbose and quiet:
            raise ConfigurationError("--verbose と --quiet は同時に指定できません。")
        overrides = _cli_overrides(
            format_names=format_names,
            output_dir=output_dir,
            asr_backend=asr_backend,
            asr_model=asr_model,
            diarization_backend=diarization_backend,
            diarization_model=diarization_model,
            device=device,
            asr_device=asr_device,
            diarization_device=diarization_device,
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
        _validate_transcribe_path(input_path)
        includes = tuple(include or ())
        excludes = tuple(exclude or ())

        if input_path.is_file() and (recursive or includes or excludes):
            raise ConfigurationError(
                "--recursive / --include / --exclude はフォルダ入力でのみ指定できます。"
            )
        if dry_run:
            _show_dry_run(input_path, config, recursive, includes, excludes)
            return
        if input_path.is_dir() and not discover_inputs(
            input_path,
            recursive=recursive,
            include=includes,
            exclude=excludes,
        ):
            console.print("処理対象ファイルはありません。")
            return

        if config.diarization.enabled:
            preflight_diarization_backend(
                config.diarization.backend,
                config.diarization.model,
                token_provider,
            )
        find_ffmpeg(config.ffmpeg.path)
        _ensure_configured_models(config, token_provider, yes=yes, quiet=quiet)

        if input_path.is_dir():
            summary = _run_batch_with_progress(
                input_path,
                config,
                token_provider,
                quiet,
                recursive=recursive,
                include=includes,
                exclude=excludes,
                resume=resume,
                force=force,
                force_unlock=force_unlock,
            )
            _print_batch_summary(summary)
            _print_batch_stage_timings(summary)
            if summary.exit_code:
                raise typer.Exit(code=summary.exit_code)
            return

        outcome = _run_with_progress(
            input_path,
            config,
            token_provider,
            quiet,
            resume=resume,
            force=force,
            force_unlock=force_unlock,
        )
    except KeyboardInterrupt:
        _exit_expected(CancelledError())
    except UtteranError as exc:
        _exit_expected(exc)
    except typer.Exit:
        raise
    except Exception as exc:
        if verbose:
            logging.getLogger(__name__).exception("予期しないエラー")
        error_console.print(f"[red]予期しないエラー:[/red] {mask_secrets(str(exc))}")
        raise typer.Exit(code=1) from None

    if outcome.executed_stages:
        console.print(f"ジョブ: {outcome.job_id} ({', '.join(outcome.executed_stages)})")
    else:
        console.print(f"ジョブ: {outcome.job_id} (完了済みのためスキップ)")
    for path in outcome.output_paths:
        console.print(f"出力: {path}")
    _print_stage_timings(outcome.stage_durations)


@app.command("devices")
def devices_command(
    json_output: Annotated[bool, typer.Option("--json", help="機械可読な JSON を出力")] = False,
    config_path: Annotated[Path | None, typer.Option("--config", help="config.toml のパス")] = None,
) -> None:
    """Display hardware, runtimes, dependencies, and actual auto choices."""
    try:
        config = Config.load(config_path=config_path)
        report = detect_devices(
            config.ffmpeg.path,
            venv_dir=config.general.venv_dir,
            native_dir=config.general.native_dir,
        )
    except UtteranError as exc:
        _exit_expected(exc)
    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    _print_device_report(report)


@models_app.command("list")
def models_list(
    available: Annotated[
        bool, typer.Option("--available", help="未導入を含むカタログ全体を表示")
    ] = False,
    all_models: Annotated[
        bool, typer.Option("--all", help="英語専用を含む全カタログを表示")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="JSONで表示")] = False,
) -> None:
    """導入済みモデル、または選択可能なカタログ全体を表示します。"""
    try:
        manager = _model_manager()
        statuses = manager.list_status(available=available, all_models=all_models)
    except UtteranError as exc:
        _exit_expected(exc)
    if json_output:
        typer.echo(
            json.dumps(
                [
                    {
                        "key": status.entry.key,
                        "model_id": status.entry.model_id,
                        "display_name": status.entry.display_name,
                        "backend": status.entry.backend,
                        "installed": status.installed,
                        "path": None if status.path is None else str(status.path),
                    }
                    for status in statuses
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    _print_model_catalog(statuses, numbered=available)
    if not statuses:
        console.print(
            "導入済みモデルはありません。`models list --available` で候補を確認できます。"
        )


@models_app.command("download")
def models_download(
    identifier: Annotated[
        str | None,
        typer.Argument(help="省略時は番号付き一覧から選択。モデル ID も指定可能"),
    ] = None,
) -> None:
    """モデルIDを指定するか、対話一覧から選択して取得します。"""
    try:
        manager = _model_manager()
        entries = [get_model(identifier)] if identifier is not None else _prompt_for_models(manager)
        if not entries:
            console.print("モデルは選択されませんでした。")
            return
        for entry in entries:
            existing = manager.status(entry)
            if existing.installed:
                console.print(f"導入済み: {entry.display_name}  {existing.path}")
                continue
            if existing.path is not None:
                console.print(f"不完全なモデルを再取得: {entry.display_name}  {existing.path}")
            console.print(
                f"取得: {entry.display_name} ({entry.key}, "
                f"概算 {_format_size(entry.approximate_size_bytes)})"
            )
            with Progress(console=console) as progress:
                path = manager.download(entry, progress=RichProgressReporter(progress))
            console.print(f"取得完了: {path}")
    except KeyboardInterrupt:
        _exit_expected(CancelledError())
    except UtteranError as exc:
        _exit_expected(exc)


@models_app.command("remove")
def models_remove(
    identifier: Annotated[str, typer.Argument(help="モデル ID または backend:model-id")],
    yes: Annotated[bool, typer.Option("--yes", help="確認を省略")] = False,
) -> None:
    """Remove one explicitly selected model cache."""
    try:
        manager = _model_manager()
        entry = get_model(identifier)
        status = manager.status(entry)
        if status.path is None:
            console.print(f"未導入: {entry.key}")
            return
        state = "導入済み" if status.installed else "不完全"
        console.print(
            f"削除対象: {entry.key}  {state}  {_format_size(status.size_bytes)}  {status.path}"
        )
        if not yes and not typer.confirm("削除しますか?"):
            console.print("キャンセルしました。")
            return
        if manager.remove(entry):
            console.print(f"削除しました: {entry.key}")
    except UtteranError as exc:
        _exit_expected(exc)


@models_app.command("verify")
def models_verify(
    identifier: Annotated[str | None, typer.Argument(help="省略時は導入済みモデルすべて")] = None,
) -> None:
    """Verify required files and sizes for installed models."""
    try:
        manager = _model_manager()
        entries = (
            [get_model(identifier)]
            if identifier is not None
            else [status.entry for status in manager.list_status()]
        )
        results = [manager.verify(entry) for entry in entries]
    except UtteranError as exc:
        _exit_expected(exc)
    table = Table("ID", "結果", "サイズ", "詳細")
    for result in results:
        table.add_row(
            result.entry.key,
            "ok" if result.ok else "failed",
            _format_size(result.size_bytes),
            result.message,
        )
    console.print(table)
    if not results:
        console.print("検証対象の導入済みモデルはありません。")
    if any(not result.ok for result in results):
        raise typer.Exit(code=1)


@models_app.command("path")
def models_path() -> None:
    """Print the effective utteran model directory."""
    typer.echo(_model_manager().root)


@models_app.command("prepare-openvino")
def models_prepare_openvino(
    identifier: Annotated[str, typer.Argument(help="whisper-cppモデルID")],
    device: Annotated[str, typer.Option("--device", help="実行時OpenVINO device")] = "GPU",
    purge_cache: Annotated[bool, typer.Option("--purge-cache")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="大容量重み取得の確認を省略")] = False,
) -> None:
    """OpenAI重みからwhisper.cpp用OpenVINO encoder IRを生成します。"""
    try:
        entry = get_model(identifier, backend="whisper-cpp")
        console.print(
            f"{entry.model_size}のOpenAI PyTorch重み(最大約3GB)を別途取得してIRへ変換します。"
        )
        console.print(f"実行時OpenVINO device: {device}")
        if not yes and not typer.confirm("変換を続行しますか?"):
            console.print("キャンセルしました。")
            return
        from utteran.models.openvino import OpenVINOManager

        status = OpenVINOManager(_model_manager()).prepare(identifier, purge_cache=purge_cache)
        console.print(f"OpenVINO IRを生成しました: {status.xml_path}")
    except UtteranError as exc:
        _exit_expected(exc)


@models_app.command("list-openvino")
def models_list_openvino() -> None:
    """変換済みOpenVINO encoder IRを一覧表示します。"""
    from utteran.models.openvino import OpenVINOManager

    table = Table("モデルサイズ", "状態", "XML")
    for status in OpenVINOManager(_model_manager()).list():
        table.add_row(
            status.model_size,
            "ok" if status.installed else "未生成",
            str(status.xml_path),
        )
    console.print(table)


@models_app.command("remove-openvino")
def models_remove_openvino(
    identifier: Annotated[str, typer.Argument(help="whisper-cppモデルID")],
    yes: Annotated[bool, typer.Option("--yes", help="確認を省略")] = False,
) -> None:
    """モデルサイズに対応するOpenVINO encoder IRを削除します。"""
    if not yes and not typer.confirm("OpenVINO IRを削除しますか?"):
        console.print("キャンセルしました。")
        return
    try:
        from utteran.models.openvino import OpenVINOManager

        removed = OpenVINOManager(_model_manager()).remove(identifier)
        console.print("削除しました。" if removed else "変換済みIRはありません。")
    except UtteranError as exc:
        _exit_expected(exc)


@jobs_app.command("list")
def jobs_list(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """List jobs with input name, status, update time, and disk size."""
    try:
        store = _job_store(config_path)
        summaries = store.list_jobs()
    except UtteranError as exc:
        _exit_expected(exc)
    table = Table("job_id", "入力", "状態", "更新日時", "サイズ")
    for item in summaries:
        table.add_row(
            item.job_id,
            item.input_name,
            item.status,
            item.updated_at,
            _format_size(item.size_bytes),
        )
    console.print(table)
    if not summaries:
        console.print("保存済みジョブはありません。")


@jobs_app.command("show")
def jobs_show(
    job_id: Annotated[str, typer.Argument(help="16文字のジョブ ID")],
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Show one manifest and every stage config hash."""
    try:
        job = _job_store(config_path).get(job_id)
    except UtteranError as exc:
        _exit_expected(exc)
    console.print(f"入力: {job.manifest.input.path}")
    console.print(f"状態: {job.manifest.status}")
    console.print(f"作成: {job.manifest.created_at}")
    console.print(f"更新: {job.manifest.updated_at}")
    table = Table("ステージ", "状態", "config_hash", "完了日時", "エラー")
    for name, stage in job.manifest.stages.items():
        table.add_row(
            name,
            stage.status,
            stage.config_hash or "-",
            stage.finished_at or "-",
            stage.error or "-",
        )
    console.print(table)


@jobs_app.command("clean")
def jobs_clean(
    all_jobs: Annotated[bool, typer.Option("--all", help="すべて削除")] = False,
    failed: Annotated[bool, typer.Option("--failed", help="失敗ジョブだけ削除")] = False,
    older_than: Annotated[
        int | None, typer.Option("--older-than", min=0, help="指定日数より古いジョブ")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="確認を省略")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Display and then remove jobs matching exactly one filter."""
    try:
        if sum((all_jobs, failed, older_than is not None)) != 1:
            raise ConfigurationError(
                "--all / --failed / --older-than のいずれか1つを指定してください。"
            )
        store = _job_store(config_path)
        candidates = store.clean_candidates(
            all_jobs=all_jobs,
            failed=failed,
            older_than_days=older_than,
        )
        if not candidates:
            console.print("削除対象のジョブはありません。")
            return
        for item in candidates:
            console.print(
                f"削除対象: {item.job_id}  {item.input_name}  {item.status}  "
                f"{_format_size(item.size_bytes)}"
            )
        if not yes and not typer.confirm(f"{len(candidates)}件を削除しますか?"):
            console.print("キャンセルしました。")
            return
        store.remove([item.job_id for item in candidates])
        console.print(f"{len(candidates)}件のジョブを削除しました。")
    except UtteranError as exc:
        _exit_expected(exc)


@config_app.command("init")
def config_init(
    path: Annotated[Path | None, typer.Option("--path", help="作成先")] = None,
) -> None:
    """Create a token-free config.toml template without overwriting."""
    try:
        created = initialize_config(path)
        console.print(f"設定ファイルを作成しました: {created}")
    except UtteranError as exc:
        _exit_expected(exc)


@config_app.command("show")
def config_show(
    path: Annotated[Path | None, typer.Option("--path", help="読み込む config.toml")] = None,
) -> None:
    """Print effective validated settings without secret token sources."""
    try:
        config = Config.load(config_path=path)
    except UtteranError as exc:
        _exit_expected(exc)
    typer.echo(json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2))


@config_app.command("path")
def config_path_command() -> None:
    """Print the platform-specific default config.toml path."""
    typer.echo(default_config_path())


@profiles_app.command("list")
def profiles_list_command(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """List every known profile's presence, disk usage, and last update."""
    try:
        config = Config.load(config_path=config_path)
        root = resolve_venv_root(Path.cwd(), configured=config.general.venv_dir)
        statuses = list_profile_statuses(root)
    except UtteranError as exc:
        _exit_expected(exc)
    table = Table("プロファイル", "extras", "状態", "サイズ", "最終更新")
    for status in statuses:
        table.add_row(
            status.name,
            ",".join(status.extras),
            "作成済み" if status.exists else "未作成",
            _format_size(status.size_bytes) if status.exists else "-",
            status.updated_at or "-",
        )
    console.print(table)
    console.print(f"venv ルート: {root}")


@profiles_app.command("current")
def profiles_current_command() -> None:
    """Print the profile run.ps1 recorded via UTTERAN_PROFILE, or 'unknown'."""
    name = current_profile_name()
    console.print(name or "不明 (run.ps1 以外から起動、または UTTERAN_PROFILE 未設定)")


@profiles_app.command("path")
def profiles_path_command(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Print the resolved venv root directory."""
    try:
        config = Config.load(config_path=config_path)
        root = resolve_venv_root(Path.cwd(), configured=config.general.venv_dir)
    except UtteranError as exc:
        _exit_expected(exc)
    typer.echo(root)


@native_app.command("build")
def native_build_command(
    variant: Annotated[
        str | None,
        typer.Option("--variant", help="cpu,vulkan,... のカンマ区切り。省略時は全構成を試行"),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="同一設定でも既存ビルドを再構築する")
    ] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Build whisper.cpp variants whose prerequisites are currently satisfied."""
    try:
        config = Config.load(config_path=config_path)
        native_dir = resolve_native_dir(config.general.native_dir)
        variants = _parse_variant_selection(variant)
        console.print(f"ネイティブビルド先: {native_dir}")
        builder = NativeBuilder(native_dir)
        manifest = builder.build_all(variants=variants, force=force)
    except UtteranError as exc:
        _exit_expected(exc)
    errors = cast(dict[str, str], manifest.get("errors", {}))
    backends = cast(dict[str, object], manifest.get("backends", {}))
    for name in variants:
        if name in backends:
            entry = cast(dict[str, object], backends[name])
            console.print(f"[green]構築成功:[/green] {name} -> {entry['executable']}")
        elif name in errors:
            console.print(f"[yellow]スキップ:[/yellow] {name}: {errors[name]}")
    if not backends:
        error_console.print("[red]エラー:[/red] 構築できた構成がありません。")
        raise typer.Exit(code=3)


@native_app.command("status")
def native_status_command(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Show the native build manifest and whether each variant is runnable now."""
    try:
        config = Config.load(config_path=config_path)
        native_dir = resolve_native_dir(config.general.native_dir)
        status = NativeBuilder(native_dir).status()
    except UtteranError as exc:
        _exit_expected(exc)
    manifest = cast(dict[str, object], status["manifest"])
    if not manifest:
        console.print("ネイティブビルドは未実行です。`utteran native build` を実行してください。")
        return
    whisper_cpp = cast(dict[str, str], manifest.get("whisper_cpp", {}))
    console.print(f"whisper.cpp: {whisper_cpp.get('tag', '-')} ({whisper_cpp.get('commit', '-')})")
    console.print(f"構築日時: {manifest.get('built_at', '-')}")
    backends = cast(dict[str, dict[str, object]], manifest.get("backends", {}))
    errors = cast(dict[str, str], manifest.get("errors", {}))
    runnable = cast(dict[str, bool], status["runnable"])
    table = Table("構成", "実行可能", "詳細")
    for name in VARIANT_NAMES:
        if name in backends:
            detail = str(backends[name].get("executable", "-"))
            table.add_row(name, "yes" if runnable.get(name) else "no", detail)
        else:
            table.add_row(name, "no", errors.get(name, "未試行"))
    console.print(table)


@native_app.command("clean")
def native_clean_command(
    all_variants: Annotated[bool, typer.Option("--all", help="全構成を削除")] = False,
    variant: Annotated[str | None, typer.Option("--variant", help="削除する構成名")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Remove one native build variant, or the entire platform build tree."""
    try:
        if sum((all_variants, variant is not None)) != 1:
            raise ConfigurationError("--all または --variant のいずれか1つを指定してください。")
        config = Config.load(config_path=config_path)
        native_dir = resolve_native_dir(config.general.native_dir)
        builder = NativeBuilder(native_dir)
        builder.clean(variant=None if all_variants else variant)
    except UtteranError as exc:
        _exit_expected(exc)
    console.print("削除しました: " + ("すべての構成" if all_variants else str(variant)))


def _parse_variant_selection(raw: str | None) -> tuple[str, ...]:
    """Validate a comma-separated --variant option against known variant names."""
    if raw is None:
        return VARIANT_NAMES
    requested = tuple(item.strip() for item in raw.split(",") if item.strip())
    unknown = [item for item in requested if item not in VARIANT_NAMES]
    if unknown:
        raise ConfigurationError(
            f"未知の構成です: {', '.join(unknown)} (既知: {', '.join(VARIANT_NAMES)})"
        )
    return requested or VARIANT_NAMES


def _run_with_progress(
    input_path: Path,
    config: Config,
    token_provider: TokenProvider,
    quiet: bool,
    *,
    resume: bool,
    force: bool,
    force_unlock: bool,
) -> PipelineOutcome:
    """Run one pipeline with optional Rich progress rendering."""

    def operation(cancel: CancelToken) -> PipelineOutcome:
        if quiet:
            return run_pipeline(
                input_path,
                config,
                cancel=cancel,
                token_provider=token_provider,
                resume=resume,
                force=force,
                force_unlock=force_unlock,
            )
        with Progress(console=console) as progress:
            return run_pipeline(
                input_path,
                config,
                progress=RichProgressReporter(progress),
                cancel=cancel,
                token_provider=token_provider,
                resume=resume,
                force=force,
                force_unlock=force_unlock,
            )

    return _run_interruptibly(operation, hard_exit_on_interrupt=True)


def _run_batch_with_progress(
    input_path: Path,
    config: Config,
    token_provider: TokenProvider,
    quiet: bool,
    *,
    recursive: bool,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    resume: bool,
    force: bool,
    force_unlock: bool,
) -> BatchSummary:
    """Run a folder batch with one shared Rich progress reporter."""

    def operation(cancel: CancelToken) -> BatchSummary:
        if quiet:
            return run_batch(
                input_path,
                config,
                cancel=cancel,
                token_provider=token_provider,
                recursive=recursive,
                include=include,
                exclude=exclude,
                resume=resume,
                force=force,
                force_unlock=force_unlock,
            )
        with Progress(console=console) as progress:
            return run_batch(
                input_path,
                config,
                token_provider=token_provider,
                progress=RichProgressReporter(progress),
                cancel=cancel,
                recursive=recursive,
                include=include,
                exclude=exclude,
                resume=resume,
                force=force,
                force_unlock=force_unlock,
            )

    return _run_interruptibly(operation, hard_exit_on_interrupt=True)


def _run_interruptibly(
    operation: Callable[[CancelToken], T],
    *,
    hard_exit_on_interrupt: bool = False,
) -> T:
    """Keep the main thread responsive while a native backend is running."""
    cancel = CancelToken()
    finished = threading.Event()
    outcomes: list[T] = []
    failures: list[BaseException] = []

    def worker() -> None:
        try:
            outcomes.append(operation(cancel))
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=worker, name="utteran-operation", daemon=True)
    try:
        thread.start()
        while not finished.wait(0.1):
            pass
    except KeyboardInterrupt:
        cancel.cancel()
        finished.wait(1.0)
        if hard_exit_on_interrupt:
            error_console.print(f"[red]エラー:[/red] {CancelledError()}")
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(130)
        raise CancelledError from None

    if failures:
        raise failures[0]
    if not outcomes:
        raise RuntimeError("処理スレッドが結果を返さず終了しました。")
    return outcomes[0]


def _restore_windows_ctrl_c() -> None:
    """Undo a console launcher's inherited Ctrl+C-ignore flag on Windows."""
    if os.name != "nt":
        return
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        return
    try:
        kernel32 = loader("kernel32", use_last_error=True)
        kernel32.SetConsoleCtrlHandler(None, False)
    except (AttributeError, OSError):
        logging.getLogger(__name__).debug("Windows Ctrl+C handler state could not be restored")


def _ensure_configured_models(
    config: Config,
    token_provider: TokenProvider,
    *,
    yes: bool,
    quiet: bool,
) -> None:
    """Prompt before any missing catalog model download; never download implicitly."""
    manager = ModelManager(token_provider=token_provider)
    entries: list[ModelEntry] = []
    asr_name = "faster-whisper" if config.asr.backend == "auto" else config.asr.backend
    for model_id, backend, enabled in (
        (config.asr.model, asr_name, True),
        (
            config.diarization.model,
            config.diarization.backend,
            config.diarization.enabled,
        ),
    ):
        if not enabled:
            continue
        try:
            entry = get_model(model_id, backend=backend)
        except ConfigurationError:
            continue
        if entry not in entries:
            entries.append(entry)
    for entry in entries:
        if manager.status(entry).installed:
            continue
        command = f"utteran models download {entry.key}"
        if not yes:
            if not sys.stdin.isatty():
                raise ModelNotFoundError(
                    f"モデル '{entry.key}' が未取得です。非対話環境では自動取得しません。"
                    f"`{command}` を先に実行するか、--yes を指定してください。"
                )
            if not typer.confirm(
                f"モデル {entry.key} (概算 {_format_size(entry.approximate_size_bytes)}) "
                "を取得しますか?"
            ):
                raise ModelNotFoundError(f"モデルが必要です。`{command}` を実行してください。")
        if quiet:
            manager.download(entry)
        else:
            with Progress(console=console) as progress:
                manager.download(entry, progress=RichProgressReporter(progress))


def _show_dry_run(
    input_path: Path,
    config: Config,
    recursive: bool,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
) -> None:
    """Display selected inputs without checking dependencies or downloading models."""
    paths = (
        [input_path]
        if input_path.is_file()
        else discover_inputs(
            input_path,
            recursive=recursive,
            include=include,
            exclude=exclude,
        )
    )
    for path in paths:
        console.print(f"処理対象: {path}")
    console.print(f"合計: {len(paths)}件 / 出力先: {config.general.output_dir}")


def _print_batch_summary(summary: BatchSummary) -> None:
    """Print aggregate counts plus all skip/failure reasons."""
    console.print(
        f"集計: 成功 {summary.success_count} / スキップ {summary.skipped_count} / "
        f"失敗 {summary.failed_count}"
    )
    for item in summary.items:
        if item.status in {"skipped", "failed"}:
            style = "yellow" if item.status == "skipped" else "red"
            console.print(f"[{style}]{item.status}:[/{style}] {item.path}: {item.reason}")


def _print_batch_stage_timings(summary: BatchSummary) -> None:
    """Print accumulated stage durations for all successfully completed batch items."""
    totals: dict[str, float] = {}
    for item in summary.items:
        if item.outcome is None:
            continue
        for stage, duration in item.outcome.stage_durations.items():
            totals[stage] = totals.get(stage, 0.0) + duration
    _print_stage_timings(totals, title="フェーズ別処理時間 (一括合計)")


def _print_stage_timings(
    stage_durations: dict[str, float], *, title: str = "フェーズ別処理時間"
) -> None:
    """Print timings in pipeline order after all regular result output."""
    if not stage_durations:
        console.print(f"{title}: 今回実行したフェーズはありません。")
        return
    labels = {
        "audio": "音声抽出・正規化",
        "asr": "文字起こし (ASR)",
        "diarization": "話者分離",
        "merge": "話者割当・結合",
        "export": "出力生成",
    }
    table = Table("フェーズ", "所要時間", title=title)
    total = 0.0
    for stage in ("audio", "asr", "diarization", "merge", "export"):
        duration = stage_durations.get(stage)
        if duration is None:
            continue
        total += duration
        table.add_row(labels[stage], _format_duration(duration))
    table.add_section()
    table.add_row("実行フェーズ合計", _format_duration(total))
    console.print(table)


def _format_duration(seconds: float) -> str:
    """Format elapsed seconds without losing sub-second stage visibility."""
    hours, remainder = divmod(max(seconds, 0.0), 3600)
    minutes, seconds_part = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{seconds_part:06.3f}"


def _print_device_report(report: DeviceReport) -> None:
    """Render a compact human-oriented device table."""
    table = Table("項目", "状態", "詳細")
    cpu = report.cpu
    table.add_row(
        "CPU",
        "available",
        f"logical={cpu.logical_cores}, physical={cpu.physical_cores}, "
        f"AVX2={cpu.avx2}, AVX-512={cpu.avx512}",
    )
    ct2 = report.ctranslate2
    table.add_row(
        "CTranslate2",
        "available" if ct2.available else "unavailable",
        f"version={ct2.version}, CPU={','.join(ct2.cpu_compute_types) or '-'}",
    )
    for device in ct2.cuda_devices:
        table.add_row(
            f"CTranslate2 cuda:{device.index}",
            "usable" if device.usable else "unusable",
            f"{device.name}, VRAM={_format_size(device.memory_bytes)}, "
            f"compute={','.join(device.compute_types) or '-'} {device.error or ''}",
        )
    libraries = report.cuda_libraries
    table.add_row("cuDNN", "found" if libraries.cudnn else "missing", libraries.cudnn or "-")
    table.add_row("cuBLAS", "found" if libraries.cublas else "missing", libraries.cublas or "-")
    torch = report.pytorch
    table.add_row(
        "PyTorch CUDA",
        "usable" if torch.cuda_available else "unavailable",
        f"version={torch.version}, devices={len(torch.cuda_devices)}",
    )
    for device in torch.cuda_devices:
        table.add_row(
            f"PyTorch cuda:{device.index}",
            "usable" if device.usable else "unusable",
            f"{device.name}, VRAM={_format_size(device.memory_bytes)} {device.error or ''}",
        )
    table.add_row(
        "PyTorch XPU",
        "usable" if torch.xpu_available else "unavailable",
        f"version={torch.version}, devices={len(torch.xpu_devices)}",
    )
    for device in torch.xpu_devices:
        table.add_row(
            f"PyTorch xpu:{device.index}",
            "usable" if device.usable else "unusable",
            f"{device.name}, shared memory={_format_size(device.memory_bytes)} "
            f"{device.error or ''}",
        )
    table.add_row(
        "OpenVINO",
        "available" if report.openvino.available else "unavailable",
        ", ".join(report.openvino.values) or report.openvino.error or "-",
    )
    table.add_row(
        "ONNX Runtime",
        "available" if report.onnxruntime.available else "unavailable",
        ", ".join(report.onnxruntime.values) or report.onnxruntime.error or "-",
    )
    table.add_row(
        "ffmpeg",
        "available" if report.ffmpeg.available else "unavailable",
        f"{report.ffmpeg.path or '-'} / {report.ffmpeg.version or report.ffmpeg.error or '-'}",
    )
    for backend, available in report.backends.items():
        table.add_row(
            f"backend: {backend}",
            "available" if available else "unavailable",
            "-",
        )
    table.add_row(
        "Vulkan (build)",
        "available" if report.vulkan.build_available else "unavailable",
        report.vulkan.build_error or "glslc found",
    )
    table.add_row(
        "Vulkan (runtime)",
        "available" if report.vulkan.runtime_available else "unavailable",
        report.vulkan.runtime_device or report.vulkan.runtime_error or "-",
    )
    for name, runnable in report.native.variants.items():
        table.add_row(
            f"native: {name}",
            "runnable" if runnable else "not built",
            report.native.whisper_cpp_tag or "-",
        )
    console.print(table)
    console.print(
        f"プロファイル: 現在={report.profile.current or '不明'} / "
        + ", ".join(
            f"{item.name}={'作成済み' if item.exists else '未作成'}"
            for item in report.profile.profiles
        )
    )
    selected = report.auto_selection
    console.print(
        "auto: "
        f"ASR={selected.asr_backend}/{selected.asr_device}/{selected.asr_compute_type}, "
        f"diarization={selected.diarization_backend}/{selected.diarization_device}"
    )
    for note in selected.notes:
        console.print(f"[cyan]情報:[/cyan] {note}")
    for warning in report.warnings:
        console.print(f"[yellow]警告:[/yellow] {warning}")


def _model_manager() -> ModelManager:
    """Create a token-masked model manager for a CLI command."""
    provider = default_token_provider()
    register_secret(provider.get_token())
    return ModelManager(token_provider=provider)


def _job_store(config_path: Path | None) -> JobStore:
    """Create a job store from effective configuration."""
    return JobStore(Config.load(config_path=config_path).effective_job_dir)


def _validate_transcribe_path(path: Path) -> None:
    """Validate file-or-directory input before any dependency preflight."""
    if not path.exists():
        from utteran.errors import InputFileNotFoundError

        raise InputFileNotFoundError(f"入力ファイルが見つかりません: {path}")
    if not path.is_file() and not path.is_dir():
        from utteran.errors import UnsupportedInputError

        raise UnsupportedInputError(f"通常ファイルまたはフォルダを指定してください: {path}")


def _cli_overrides(
    *,
    format_names: str | None,
    output_dir: Path | None,
    asr_backend: str | None,
    asr_model: str | None,
    diarization_backend: str | None,
    diarization_model: str | None,
    device: str | None,
    asr_device: str | None,
    diarization_device: str | None,
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
    if asr_device is not None:
        asr["device"] = asr_device
    if diarization_device is not None:
        diarization["device"] = diarization_device
    if language is not None:
        normalized_language = language.strip()
        asr["language"] = None if normalized_language.casefold() == "auto" else normalized_language
    if diarization_backend is not None:
        diarization["backend"] = diarization_backend
    if diarization_model is not None:
        diarization["model"] = diarization_model
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


def _format_size(size: int | None) -> str:
    """Format bytes for compact tables."""
    if size is None:
        return "unknown"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _print_model_catalog(statuses: list[ModelStatus], *, numbered: bool) -> None:
    """Print a human-oriented catalog while retaining exact automation IDs."""
    for index, status in enumerate(statuses, start=1):
        entry = status.entry
        shown_size = status.size_bytes if status.path is not None else entry.approximate_size_bytes
        state = "導入済み" if status.installed else "不完全" if status.path else "未導入"
        prefix = f"{index}." if numbered else "-"
        console.print(f"{prefix} [bold]{entry.display_name}[/bold]")
        console.print(f"   用途: {entry.description}")
        console.print(
            f"   状態: {state} / backend: {entry.backend} / サイズ: {_format_size(shown_size)}"
        )
        console.print(f"   ライセンス: {entry.license} / gated: {'yes' if entry.gated else 'no'}")
        console.print(f"   ID: [cyan]{entry.key}[/cyan]")
        if status.path is not None:
            console.print(f"   保存先: {status.path}")


def _prompt_for_models(manager: ModelManager) -> list[ModelEntry]:
    """Show the complete catalog and prompt for comma-separated numbers or IDs."""
    if not _stdin_is_interactive():
        raise ConfigurationError(
            "非対話環境ではモデルIDを省略できません。\n"
            "候補: `utteran models list --available`\n"
            "`utteran models download <ID>` を実行してください。"
        )
    statuses = manager.list_status(available=True)
    _print_model_catalog(statuses, numbered=True)
    answer = typer.prompt(
        "取得する番号またはID (複数はカンマ区切り、Enterで中止)",
        default="",
        show_default=False,
    )
    return _parse_model_selection(answer, tuple(status.entry for status in statuses))


def _parse_model_selection(
    selection: str,
    entries: tuple[ModelEntry, ...],
) -> list[ModelEntry]:
    """Resolve stable one-based menu numbers or normal catalog identifiers."""
    selected: list[ModelEntry] = []
    for raw_token in selection.replace("、", ",").split(","):
        token = raw_token.strip()
        if not token:
            continue
        if token.isdecimal():
            index = int(token)
            if index < 1 or index > len(entries):
                raise ConfigurationError(
                    f"モデル番号 {index} は範囲外です。1〜{len(entries)} から選択してください。"
                )
            entry = entries[index - 1]
        else:
            entry = get_model(token)
        if entry not in selected:
            selected.append(entry)
    return selected


def _stdin_is_interactive() -> bool:
    """Return whether an omitted model ID may safely open a prompt."""
    return sys.stdin.isatty()


def _exit_expected(error: UtteranError) -> Never:
    """Print an actionable expected error without a traceback."""
    error_console.print(f"[red]エラー:[/red] {mask_secrets(str(error))}")
    raise typer.Exit(code=error.exit_code) from None


if __name__ == "__main__":
    app()
