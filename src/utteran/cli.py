"""Thin Typer command-line interface over utteran's reusable core APIs."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Never, TypedDict, TypeVar, cast

import typer
from rich.console import Console
from rich.progress import Progress, TaskID
from rich.table import Table

from utteran.asr.registry import validate_asr_configuration
from utteran.audio import find_ffmpeg
from utteran.batch import BatchSummary, discover_inputs, run_batch
from utteran.benchmark import (
    BENCHMARK_MODES,
    SCORE_DISCLAIMER,
    BenchmarkMeasurement,
    BenchmarkModeName,
    apply_target,
    benchmark_warning,
    collect_environment,
    default_result_dir,
    detect_benchmark_environment,
    latest_run,
    markdown_report,
    mode_durations,
    new_run_payload,
    parse_durations,
    prepared_audio_lengths,
    recommend,
    resolve_benchmark_targets,
    run_benchmark,
    save_run,
    version_changed,
    wav_duration,
)
from utteran.config import (
    Config,
    TokenProvider,
    default_config_path,
    default_token_provider,
    initialize_config,
    resolve_token_status,
)
from utteran.devices import (
    PROBE_PROGRESS_STATES,
    DeviceReport,
    ProbeProgress,
    detect_devices,
)
from utteran.diarization.registry import preflight_diarization_backend
from utteran.diarization_quality import (
    DiarizationGroundTruth,
    evaluate_diarization,
    segments_from_dict,
)
from utteran.errors import (
    CancelledError,
    ConfigurationError,
    HuggingFaceAuthenticationError,
    HuggingFaceTokenMissingError,
    JobNotFoundError,
    ModelAgreementError,
    ModelNotFoundError,
    UtteranError,
)
from utteran.exporters import export_all
from utteran.jobs import (
    INTERMEDIATE_SCHEMA_VERSION,
    STAGES,
    Job,
    JobStore,
    JobSummary,
    config_hash,
)
from utteran.logging import (
    close_runtime_logging,
    configure_runtime_logging,
    mask_secrets,
    register_secret,
    remove_all_logs,
    resolve_log_dir,
    write_diagnostic_snapshot,
)
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
from utteran.progress import JsonProgressReporter, combine_progress
from utteran.types import CancelToken, ExportOptions, PipelineOutcome, PipelineResult, ProgressEvent

T = TypeVar("T")


class RuntimeSummary(TypedDict):
    """Transcript-free runtime metadata shared by CLI and GUI."""

    job_id: str | None
    asr_backend: str
    asr_model: str
    asr_device: str
    executed_stages: list[str]
    reused_stages: list[str]


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
logs_app = typer.Typer(help="実行ログの保存場所と削除を管理します。", no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(jobs_app, name="jobs")
app.add_typer(config_app, name="config")
app.add_typer(profiles_app, name="profiles")
app.add_typer(native_app, name="native")
app.add_typer(memory_app, name="memory")
app.add_typer(logs_app, name="logs")


@logs_app.command("path")
def logs_path_command(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Show the effective log directory and whether fallback was necessary."""
    config = Config.load(config_path=config_path)
    selected, preferred, fell_back = resolve_log_dir(config.general.log_dir)
    console.print(str(selected))
    if fell_back:
        console.print(f"書き込み不可のため退避: {preferred}")


@logs_app.command("clean")
def logs_clean_command(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Delete all retained application, CLI, raw, and diagnostic logs."""
    config = Config.load(config_path=config_path)
    selected, _preferred, _fell_back = resolve_log_dir(config.general.log_dir)
    close_runtime_logging()
    result = remove_all_logs(selected)
    console.print(
        f"ログを削除しました: {result.files_deleted} files / {result.bytes_deleted} bytes"
    )


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
def main(ctx: typer.Context) -> None:
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
    # Every CLI invocation gets an event-only JSONL stream. ``logs clean`` is
    # excluded because Windows cannot remove the log file currently held open.
    if ctx.invoked_subcommand != "transcribe" and sys.argv[1:3] != ["logs", "clean"]:
        try:
            config = Config.load()
            configure_runtime_logging(
                level=config.general.log_level,
                log_dir=config.general.log_dir,
                raw_enabled=config.general.raw_subprocess_logs,
                retention_days=config.general.log_retention_days,
                max_bytes=config.general.log_max_mib * 1024 * 1024,
                raw_max_bytes=config.general.raw_log_max_mib * 1024 * 1024,
                command=ctx.invoked_subcommand or "utteran",
            )
        except (OSError, UtteranError) as exc:
            error_console.print(
                f"[yellow]ログを初期化できません: {mask_secrets(str(exc))}[/yellow]"
            )


@app.command()
def benchmark(
    audio: Annotated[Path, typer.Option("--audio", help="測定用WAV (実データは明示指定)")],
    variants: Annotated[str | None, typer.Option(help="構成名のカンマ区切り")] = None,
    targets: Annotated[
        str | None,
        typer.Option(help="backend/device/modelのカンマ区切り (model省略可、--variantsより優先)"),
    ] = None,
    mode: Annotated[str | None, typer.Option(help="quick|standard|detailed")] = None,
    word_timestamps: Annotated[str, typer.Option(help="auto|always|never")] = "auto",
    repeat: Annotated[int | None, typer.Option(min=1)] = None,
    warmup: Annotated[int | None, typer.Option(min=0)] = None,
    durations: Annotated[
        str,
        typer.Option(help="測定する秒数のカンマ区切り。fullは入力全体 (例: 180,900,full)"),
    ] = "",
    reference_text: Annotated[
        Path | None, typer.Option("--reference-text", help="CER用の正解テキスト")
    ] = None,
    json_path: Annotated[Path | None, typer.Option("--json", help="JSON出力先")] = None,
    markdown_path: Annotated[
        Path | None, typer.Option("--markdown", help="共有用Markdown出力先")
    ] = None,
    apply: Annotated[bool, typer.Option("--apply", help="推奨構成を設定へ保存")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Measure ASR variants without creating pipeline jobs or retaining recognized text."""
    if word_timestamps not in {"auto", "always", "never"}:
        raise typer.BadParameter(
            "auto|always|neverを指定してください", param_hint="--word-timestamps"
        )
    config = Config.load(config_path=config_path)
    if mode is not None and mode not in BENCHMARK_MODES:
        raise typer.BadParameter("quick|standard|detailedを指定してください", param_hint="--mode")
    mode_name: BenchmarkModeName = "quick" if mode is None else mode
    selected_mode = BENCHMARK_MODES[mode_name]
    selected_repeat = repeat if repeat is not None else (selected_mode.repeat if mode else 3)
    selected_warmup = warmup if warmup is not None else (selected_mode.warmup if mode else 1)
    console.print("他の高負荷処理を停止してください。結果に文字起こし内容は保存しません。")
    if mode:
        console.print(
            f"{selected_mode.label}モード: 所要時間の目安 {selected_mode.estimated_minutes} "
            f"(warmup {selected_warmup} / repeat {selected_repeat})"
        )
    payload: dict[str, object] | None = None
    result_file: Path | None = None
    try:
        source_duration = wav_duration(audio)
        requested_durations = (
            parse_durations(durations, source_duration)
            if durations
            else mode_durations(selected_mode, source_duration)
            if mode
            else (source_duration,)
        )
        report = detect_benchmark_environment(config)
        if targets:
            availability = resolve_benchmark_targets(
                config,
                report,
                "targets",
                targets=tuple(item.strip() for item in targets.split(",") if item.strip()),
            )
        elif mode:
            availability = resolve_benchmark_targets(
                config,
                report,
                "mode",
                multiple_models=selected_mode.multiple_models,
            )
        else:
            selected_variants = variants or "cpu,openvino,vulkan,openvino_vulkan,faster-whisper"
            availability = resolve_benchmark_targets(
                config,
                report,
                "variants",
                variants=tuple(
                    item.strip() for item in selected_variants.split(",") if item.strip()
                ),
            )
        runnable = tuple(item.target for item in availability if item.state == "runnable")
        for item in availability:
            if item.state == "preparation":
                console.print(
                    f"[yellow]準備すれば可能: {item.target.target_id} — {item.reason}; "
                    f"{item.preparation or ''}[/yellow]"
                )
            elif item.state == "unknown":
                console.print(f"[yellow]判定不能: {item.target.target_id} — {item.reason}[/yellow]")
        reference_path = reference_text
        if mode and not selected_mode.accuracy:
            reference_path = None
        elif reference_path is None and audio.with_suffix(".txt").is_file():
            reference_path = audio.with_suffix(".txt")
        reference = reference_path.read_text(encoding="utf-8") if reference_path else None
        if mode and selected_mode.accuracy and reference is None:
            console.print("[yellow]正解テキストがないため精度測定を省略します。[/yellow]")
        result_dir = default_result_dir(config)
        previous_run = latest_run(result_dir)
        if previous_run is not None:
            console.print(f"前回結果と比較します: {previous_run[0]}")
            if version_changed(previous_run[1]):
                console.print(
                    "[yellow]utteranのバージョンが変わったため再測定を推奨します。[/yellow]"
                )
        stamp = time.strftime("%Y%m%d-%H%M%S")
        result_file = result_dir / f"benchmark-{stamp}.json"
        payload = new_run_payload(
            mode_name,
            source_duration,
            collect_environment(report),
            availability,
        )
        save_run(result_file, payload)
        assert payload is not None and result_file is not None
        measurements: list[BenchmarkMeasurement] = []
        with prepared_audio_lengths(audio, requested_durations) as prepared:
            for measured_duration, measured_audio in prepared:
                warning = benchmark_warning(measured_duration)
                if warning:
                    console.print(f"[yellow]警告 ({measured_duration:.3f}秒): {warning}[/yellow]")
                partial_results: list[Any] = []

                def persist_partial(
                    result: Any,
                    partial: list[Any] = partial_results,
                    duration: float = measured_duration,
                    selected_warning: str | None = warning,
                ) -> None:
                    partial.append(result)
                    payload["in_progress_measurement"] = BenchmarkMeasurement(
                        duration, selected_warning, tuple(partial)
                    ).as_dict()
                    payload["updated_at"] = datetime.now(UTC).isoformat()
                    save_run(result_file, payload)

                results = run_benchmark(
                    config,
                    measured_audio,
                    targets=runnable,
                    word_timestamps=word_timestamps == "always",
                    repeat=selected_repeat,
                    warmup=selected_warmup,
                    reference_text=reference,
                    result_callback=persist_partial,
                )
                measurement = BenchmarkMeasurement(measured_duration, warning, tuple(results))
                measurements.append(measurement)
                cast(list[object], payload["measurements"]).append(measurement.as_dict())
                payload.pop("in_progress_measurement", None)
                payload["updated_at"] = datetime.now(UTC).isoformat()
                save_run(result_file, payload)
    except KeyboardInterrupt:
        if payload is not None and result_file is not None:
            payload["status"] = "interrupted"
            payload["updated_at"] = datetime.now(UTC).isoformat()
            save_run(result_file, payload)
            console.print(f"[yellow]中断しました。完了分を保存しました: {result_file}[/yellow]")
        raise typer.Exit(130) from None
    except (OSError, ValueError, UtteranError) as exc:
        error_console.print(f"エラー: {mask_secrets(str(exc))}")
        raise typer.Exit(3) from None
    if not any(measurement.results for measurement in measurements):
        error_console.print("エラー: 指定した構成に利用可能なバックエンド/モデルがありません。")
        raise typer.Exit(3)
    table = Table(
        "音声長",
        "構成",
        "モデル/量子化",
        "load",
        "推論",
        "速度スコア(load込/除外)",
        "1時間換算",
        "精度",
    )
    for measurement in measurements:
        for result in measurement.results:
            target = result.target
            table.add_row(
                f"{measurement.audio_duration_seconds:.3f}s",
                f"{target.backend} / {target.device}" if target else result.variant,
                f"{target.model} / {target.quantization or '-'}" if target else "-",
                f"{result.median_load_seconds:.3f}s",
                f"{result.median_transcribe_seconds:.3f}s",
                f"{result.speed_score}/{result.speed_score_excluding_load}"
                + (" (参考値)" if target and not target.baseline else ""),
                f"約{result.hour_minutes}分",
                (
                    f"{result.accuracy_score} (CER {result.character_error_rate * 100:.1f}%)"
                    if result.accuracy_score is not None and result.character_error_rate is not None
                    else "未測定"
                ),
            )
    console.print(table)
    console.print(SCORE_DISCLAIMER)
    recommendation = recommend(measurements)
    payload["status"] = "completed"
    payload["updated_at"] = datetime.now(UTC).isoformat()
    if recommendation and recommendation.target:
        longest = max(measurements, key=lambda item: item.audio_duration_seconds)
        payload["recommendation"] = {
            "target": asdict(recommendation.target),
            "speed_score": recommendation.speed_score,
            "accuracy_score": recommendation.accuracy_score,
            "audio_duration_seconds": longest.audio_duration_seconds,
            "model": recommendation.target.model,
            "measured_at": payload["updated_at"],
        }
        console.print(f"推奨構成: {recommendation.target.target_id}")
        auto = report.auto_selection
        if (
            recommendation.target.backend != auto.asr_backend
            or recommendation.target.device != auto.asr_device
        ):
            console.print(
                f"[yellow]現在のauto ({auto.asr_backend}/{auto.asr_device}) と"
                "測定結果が異なります。[/yellow]"
            )
    if previous_run is not None:
        old_recommendation = previous_run[1].get("recommendation")
        old_score = (
            old_recommendation.get("speed_score") if isinstance(old_recommendation, dict) else None
        )
        new_recommendation = payload.get("recommendation")
        new_score = (
            new_recommendation.get("speed_score") if isinstance(new_recommendation, dict) else None
        )
        payload["comparison"] = {
            "previous_result": str(previous_run[0]),
            "utteran_version_changed": version_changed(previous_run[1]),
            "previous_speed_score": old_score,
            "speed_score_change": (
                new_score - old_score
                if isinstance(new_score, int) and isinstance(old_score, int)
                else None
            ),
        }
    save_run(result_file, payload)
    console.print(f"結果を保存しました: {result_file}")
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(markdown_report(payload), encoding="utf-8")
    if apply:
        if recommendation is None or recommendation.target is None:
            raise typer.BadParameter("--applyできる推奨構成がありません")
        longest = max(measurements, key=lambda item: item.audio_duration_seconds)
        apply_target(
            config_path or default_config_path(),
            recommendation.target,
            longest.audio_duration_seconds,
            str(payload["updated_at"]),
        )
        console.print(f"設定へ適用しました: {recommendation.target.target_id}")


@app.command("eval")
def eval_command(
    reference: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="正解タイムラインJSON"),
    ],
    hypothesis: Annotated[
        Path,
        typer.Argument(exists=True, dir_okay=False, help="utteran出力または評価用JSON"),
    ],
    output: Annotated[Path | None, typer.Option("--output", help="指標JSONの保存先")] = None,
    max_der: Annotated[
        float | None, typer.Option("--max-der", min=0.0, help="DER上限 (超過時exit 1)")
    ] = None,
    max_mid_word_boundaries: Annotated[
        int | None,
        typer.Option("--max-mid-word-boundaries", min=0, help="語中話者境界数の上限"),
    ] = None,
    max_short_turns: Annotated[
        int | None, typer.Option("--max-short-turns", min=0, help="0.5秒未満の話者島数の上限")
    ] = None,
    max_unknown_ratio: Annotated[
        float | None,
        typer.Option("--max-unknown-ratio", min=0.0, max=1.0, help="UNKNOWN時間率の上限"),
    ] = None,
    min_acknowledgement_retention: Annotated[
        float | None,
        typer.Option(
            "--min-acknowledgement-retention",
            min=0.0,
            max=1.0,
            help="短い相槌保持率の下限",
        ),
    ] = None,
) -> None:
    """Measure diarization quality against timestamped synthetic or confidential ground truth."""
    try:
        ground_truth = DiarizationGroundTruth.load(reference)
        payload = json.loads(hypothesis.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("hypothesis JSON must be an object")
        metrics = evaluate_diarization(ground_truth, segments_from_dict(payload))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        error_console.print(f"評価入力エラー: {mask_secrets(str(exc))}")
        raise typer.Exit(2) from None

    rendered = json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    typer.echo(rendered)

    violations: list[str] = []
    if max_der is not None and metrics["diarization_error_rate"] > max_der:
        violations.append(f"DER {metrics['diarization_error_rate']:.6f} > {max_der:.6f}")
    if (
        max_mid_word_boundaries is not None
        and metrics["mid_word_speaker_boundary_count"] > max_mid_word_boundaries
    ):
        violations.append(
            f"語中話者境界 {metrics['mid_word_speaker_boundary_count']} > {max_mid_word_boundaries}"
        )
    if max_short_turns is not None and metrics["short_speaker_turn_count"] > max_short_turns:
        violations.append(f"短い話者島 {metrics['short_speaker_turn_count']} > {max_short_turns}")
    if max_unknown_ratio is not None and metrics["unknown_ratio"] > max_unknown_ratio:
        violations.append(f"UNKNOWN率 {metrics['unknown_ratio']:.6f} > {max_unknown_ratio:.6f}")
    if (
        min_acknowledgement_retention is not None
        and metrics["acknowledgement_retained_ratio"] < min_acknowledgement_retention
    ):
        violations.append(
            "相槌保持率 "
            f"{metrics['acknowledgement_retained_ratio']:.6f} < "
            f"{min_acknowledgement_retention:.6f}"
        )
    if violations:
        for violation in violations:
            error_console.print(f"品質基準違反: {violation}")
        raise typer.Exit(1)


class RichProgressReporter:
    """Adapt backend-neutral progress events to Rich tasks."""

    def __init__(self, progress: Progress) -> None:
        self._progress = progress
        self._tasks: dict[str, TaskID] = {}

    def __call__(self, event: ProgressEvent) -> None:
        """Create or update one task per pipeline stage."""
        if event.event_type not in {"progress", "stage_start", "stage_done"}:
            return
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
    progress_json: Annotated[
        bool,
        typer.Option(
            "--progress-json",
            help="標準エラーへ機械可読な進捗をJSON Linesで出力",
        ),
    ] = False,
) -> None:
    """Transcribe one file or a stable sequential folder batch."""
    _restore_windows_ctrl_c()
    json_progress = JsonProgressReporter() if progress_json else None
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
        validate_asr_configuration(config)
        configure_runtime_logging(
            level=config.general.log_level,
            log_dir=config.general.log_dir,
            raw_enabled=config.general.raw_subprocess_logs,
            retention_days=config.general.log_retention_days,
            max_bytes=config.general.log_max_mib * 1024 * 1024,
            raw_max_bytes=config.general.raw_log_max_mib * 1024 * 1024,
            command="transcribe",
        )
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
            if json_progress is not None:
                json_progress.done(0)
            return
        if input_path.is_dir() and not discover_inputs(
            input_path,
            recursive=recursive,
            include=includes,
            exclude=excludes,
        ):
            console.print("処理対象ファイルはありません。")
            if json_progress is not None:
                json_progress.done(0)
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
                json_progress,
                recursive=recursive,
                include=includes,
                exclude=excludes,
                resume=resume,
                force=force,
                force_unlock=force_unlock,
            )
            _print_batch_summary(summary)
            _print_batch_stage_timings(summary)
            if json_progress is not None:
                json_progress.done(summary.exit_code)
            if summary.exit_code:
                raise typer.Exit(code=summary.exit_code)
            return

        outcome = _run_with_progress(
            input_path,
            config,
            token_provider,
            quiet,
            json_progress,
            resume=resume,
            force=force,
            force_unlock=force_unlock,
        )
    except KeyboardInterrupt:
        error = CancelledError()
        if json_progress is not None:
            json_progress.error(error, error.exit_code)
            json_progress.done(error.exit_code)
        _exit_expected(error)
    except UtteranError as exc:
        if json_progress is not None:
            json_progress.error(exc, exc.exit_code)
            json_progress.done(exc.exit_code)
        _exit_expected(exc)
    except typer.Exit as exc:
        if json_progress is not None:
            json_progress.done(exc.exit_code)
        raise
    except Exception as exc:
        if verbose:
            logging.getLogger(__name__).exception("予期しないエラー")
        error_console.print(f"[red]予期しないエラー:[/red] {mask_secrets(str(exc))}")
        if json_progress is not None:
            json_progress.error(exc, 1)
            json_progress.done(1)
        raise typer.Exit(code=1) from None

    runtime_summary = _outcome_summary(outcome)
    console.print(f"ジョブ: {outcome.job_id}")
    console.print(
        f"ASR: {runtime_summary['asr_backend']} / {runtime_summary['asr_model']} / "
        f"{runtime_summary['asr_device']}"
    )
    console.print(
        "ステージ: 実行 "
        f"{', '.join(runtime_summary['executed_stages']) or 'なし'} / 再利用 "
        f"{', '.join(runtime_summary['reused_stages']) or 'なし'}"
    )
    for path in outcome.output_paths:
        console.print(f"出力: {path}")
    _print_stage_timings(outcome.stage_durations)
    if json_progress is not None:
        json_progress.emit("run_summary", **runtime_summary)
        json_progress.done(0)


def _outcome_summary(outcome: PipelineOutcome) -> RuntimeSummary:
    """Return transcript-free runtime and resume decisions for CLI/GUI display."""
    executed = list(outcome.executed_stages)
    transcription = outcome.result.transcription
    return {
        "job_id": outcome.job_id,
        "asr_backend": transcription.backend,
        "asr_model": transcription.model_id,
        "asr_device": transcription.device,
        "executed_stages": executed,
        "reused_stages": [stage for stage in STAGES if stage not in executed],
    }


@app.command("devices")
def devices_command(
    json_output: Annotated[bool, typer.Option("--json", help="機械可読な JSON を出力")] = False,
    config_path: Annotated[Path | None, typer.Option("--config", help="config.toml のパス")] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="キャッシュを使わずデバイスを再検出")
    ] = False,
    probe_timeout: Annotated[
        float | None,
        typer.Option("--probe-timeout", min=0.1, max=300.0, help="各プローブの秒数"),
    ] = None,
) -> None:
    """Display hardware, runtimes, dependencies, and actual auto choices."""
    try:
        config = Config.load(config_path=config_path)
        report = detect_devices(
            config.ffmpeg.path,
            venv_dir=config.general.venv_dir,
            native_dir=config.general.native_dir,
            refresh=refresh,
            probe_timeout_seconds=(
                probe_timeout
                if probe_timeout is not None
                else config.general.device_probe_timeout_seconds
            ),
            progress=_print_probe_progress,
        )
        write_diagnostic_snapshot("devices", report.to_dict())
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
                        "format": status.entry.format,
                        "description": status.entry.description,
                        "approximate_size_bytes": status.entry.approximate_size_bytes,
                        "size_bytes": status.size_bytes,
                        "gated": status.entry.gated,
                        "recommended": status.entry.recommended,
                        "english_only": status.entry.english_only,
                        "model_size": status.entry.model_size,
                        "quantization": status.entry.quantization,
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
    progress_json: Annotated[
        bool, typer.Option("--progress-json", help="進捗をJSON Linesで出力")
    ] = False,
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
            if progress_json:
                path = manager.download(entry, progress=JsonProgressReporter())
            else:
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


@models_app.command("genai-cache")
def models_genai_cache(
    json_output: Annotated[bool, typer.Option("--json", help="JSONで表示")] = False,
) -> None:
    """Show the managed OpenVINO GenAI compiled-model cache path and size."""
    from utteran.asr.openvino_genai import cache_usage_bytes, resolve_cache_dir

    path = resolve_cache_dir()
    size = cache_usage_bytes(path)
    if json_output:
        typer.echo(
            json.dumps(
                {"path": str(path), "size_bytes": size, "exists": path.is_dir()},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    console.print(f"OpenVINO GenAIキャッシュ: {path}")
    console.print(f"使用量: {_format_size(size)}")


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
def models_list_openvino(
    json_output: Annotated[bool, typer.Option("--json", help="JSONで表示")] = False,
) -> None:
    """変換済みOpenVINO encoder IRを一覧表示します。"""
    from utteran.models.openvino import OpenVINOManager

    statuses = OpenVINOManager(_model_manager()).list()
    if json_output:
        typer.echo(
            json.dumps(
                [
                    {
                        "model_size": status.model_size,
                        "installed": status.installed,
                        "xml_path": str(status.xml_path),
                    }
                    for status in statuses
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    table = Table("モデルサイズ", "状態", "XML")
    for status in statuses:
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
    json_output: Annotated[bool, typer.Option("--json", help="JSONで表示")] = False,
) -> None:
    """List jobs with input name, status, update time, and disk size."""
    try:
        config = Config.load(config_path=config_path)
        store = JobStore(config.effective_job_dir)
        summaries = store.list_jobs()
    except UtteranError as exc:
        _exit_expected(exc)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "schema_version": 1,
                    "speaker_labels": config.output.speaker_labels,
                    "jobs": [_job_summary_payload(item) for item in summaries],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
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
    json_output: Annotated[bool, typer.Option("--json", help="JSONで表示")] = False,
) -> None:
    """Show one manifest and every stage config hash."""
    try:
        job = _job_store(config_path).get(job_id)
    except UtteranError as exc:
        _exit_expected(exc)
    if json_output:
        try:
            config = Config.load(config_path=config_path)
            payload = _job_detail_payload(job, config)
        except UtteranError as exc:
            _exit_expected(exc)
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
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
    job_id: Annotated[
        str | None, typer.Option("--job-id", help="指定した1件のジョブだけ削除")
    ] = None,
    older_than: Annotated[
        int | None, typer.Option("--older-than", min=0, help="指定日数より古いジョブ")
    ] = None,
    yes: Annotated[bool, typer.Option("--yes", help="確認を省略")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSONで結果を表示")] = False,
) -> None:
    """Display and then remove jobs matching exactly one filter."""
    try:
        if sum((all_jobs, failed, older_than is not None, job_id is not None)) != 1:
            raise ConfigurationError(
                "--all / --failed / --older-than / --job-id のいずれか1つを指定してください。"
            )
        store = _job_store(config_path)
        if job_id is None:
            candidates = store.clean_candidates(
                all_jobs=all_jobs,
                failed=failed,
                older_than_days=older_than,
            )
        else:
            candidates = [item for item in store.list_jobs() if item.job_id == job_id]
            if not candidates:
                raise JobNotFoundError(f"ジョブが見つかりません: {job_id}")
        if not candidates:
            if json_output:
                typer.echo(
                    json.dumps(
                        {"schema_version": 1, "deleted": [], "freed_bytes": 0},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                console.print("削除対象のジョブはありません。")
            return
        freed_bytes = sum(item.size_bytes for item in candidates)
        if not json_output:
            for item in candidates:
                console.print(
                    f"削除対象: {item.job_id}  {item.input_name}  {item.status}  "
                    f"{_format_size(item.size_bytes)}"
                )
        if not yes and not typer.confirm(f"{len(candidates)}件を削除しますか?"):
            console.print("キャンセルしました。")
            return
        store.remove([item.job_id for item in candidates])
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "schema_version": 1,
                        "deleted": [item.job_id for item in candidates],
                        "freed_bytes": freed_bytes,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            console.print(f"{len(candidates)}件のジョブを削除しました。")
    except UtteranError as exc:
        _exit_expected(exc)


@jobs_app.command("export")
def jobs_export(
    job_id: Annotated[str, typer.Argument(help="16文字のジョブ ID")],
    format_names: Annotated[
        str | None, typer.Option("--format", help="出力形式 (srt,vtt,json,txt,md)")
    ] = None,
    output_dir: Annotated[Path | None, typer.Option("--output-dir", help="出力先")] = None,
    speaker_labels: Annotated[
        list[str] | None,
        typer.Option("--speaker-label", help="内部ラベル=表示名 (複数指定可)"),
    ] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSONで結果を表示")] = False,
) -> None:
    """Regenerate presentation files from merged.json without running inference."""
    try:
        config = Config.load(config_path=config_path)
        job = JobStore(config.effective_job_dir).get(job_id)
        with job.lock():
            result = PipelineResult.from_dict(job.read_merged_payload())
            presentation = job.read_presentation()
            stored_formats = presentation.get("formats") if presentation is not None else None
            formats = _parse_export_formats(
                format_names,
                stored_formats if isinstance(stored_formats, list) else config.output.formats,
            )
            stored_labels = presentation.get("speaker_labels") if presentation is not None else None
            base_labels = (
                {str(key): str(value) for key, value in stored_labels.items()}
                if isinstance(stored_labels, dict)
                else dict(config.output.speaker_labels)
            )
            speakers = tuple(
                dict.fromkeys(segment.speaker for segment in result.segments if segment.speaker)
            )
            labels = _parse_speaker_labels(speaker_labels, speakers, base_labels)
            selected_output = _resolve_export_output_dir(output_dir, presentation, job, config)
            options = ExportOptions(
                speaker_labels=labels,
                show_speaker=config.output.show_speaker,
                srt_bom=config.output.srt_bom,
                newline=config.output.newline,
            )
            paths = export_all(result, selected_output, formats, options)
            export_material = config.output.model_dump(mode="json")
            export_material["formats"] = formats
            export_material["speaker_labels"] = labels
            export_hash = config_hash(
                {
                    "merge_config_hash": job.manifest.stages["merge"].config_hash,
                    "output": export_material,
                    "output_dir": str(selected_output.absolute()),
                }
            )
            job.complete_stage("export", export_hash, paths)
            job.write_presentation(
                output_dir=selected_output,
                formats=formats,
                speaker_labels=labels,
                outputs=paths,
            )
    except (UtteranError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, UtteranError):
            _exit_expected(exc)
        _exit_expected(ConfigurationError("文字起こし結果の形式が不正です。"))
    payload = {
        "schema_version": 1,
        "job_id": job_id,
        "executed_stages": ["export"],
        "outputs": [str(path) for path in paths],
        "output_dir": str(selected_output),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    console.print("export ステージだけを実行しました。")
    for path in paths:
        console.print(f"出力: {path}")


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


@config_app.command("token-status")
def config_token_status(
    json_output: Annotated[bool, typer.Option("--json", help="JSONで表示")] = False,
    check_model: Annotated[
        str | None, typer.Option("--check-model", help="gated modelへのアクセスも確認")
    ] = None,
) -> None:
    """Report effective HF credential/access state without exposing the token."""
    resolution = resolve_token_status()
    access = "not_checked"
    if check_model:
        if not resolution.configured:
            access = "token_missing"
        else:
            try:
                ModelManager(token_provider=default_token_provider()).check_access(
                    get_model(check_model)
                )
                access = "available"
            except HuggingFaceTokenMissingError:
                access = "token_missing"
            except HuggingFaceAuthenticationError:
                access = "token_invalid"
            except ModelAgreementError:
                access = "agreement_required"
            except (ModelNotFoundError, OSError):
                access = "network_error"
    payload = {
        "configured": resolution.configured,
        "source": resolution.source,
        "keyring_available": resolution.keyring_available,
        "access": access,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return
    console.print(
        f"configured={payload['configured']} source={payload['source']} access={payload['access']}"
    )


@profiles_app.command("list")
def profiles_list_command(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSONで表示")] = False,
) -> None:
    """List every known profile's presence, disk usage, and last update."""
    try:
        config = Config.load(config_path=config_path)
        root = resolve_venv_root(Path.cwd(), configured=config.general.venv_dir)
        statuses = list_profile_statuses(root)
    except UtteranError as exc:
        _exit_expected(exc)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "venv_root": str(root),
                    "current": current_profile_name(),
                    "profiles": [
                        {
                            "name": status.name,
                            "extras": list(status.extras),
                            "path": str(status.path),
                            "exists": status.exists,
                            "size_bytes": status.size_bytes,
                            "updated_at": status.updated_at,
                        }
                        for status in statuses
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
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
        console.print(
            "ネイティブビルドを開始します。構成により数分から数十分かかります。"
            "処理中は終了せず、そのままお待ちください。"
        )
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
    if not any(name in backends for name in variants):
        error_console.print(
            "[red]エラー:[/red] 要求した構成を構築できませんでした。"
            "上記の不足項目と導入方法を確認してください。"
        )
        raise typer.Exit(code=3)


@native_app.command("status")
def native_status_command(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="JSONで表示")] = False,
) -> None:
    """Show the native build manifest and whether each variant is runnable now."""
    try:
        config = Config.load(config_path=config_path)
        native_dir = resolve_native_dir(config.general.native_dir)
        status = NativeBuilder(native_dir).status()
    except UtteranError as exc:
        _exit_expected(exc)
    if json_output:
        typer.echo(json.dumps(status, ensure_ascii=False, indent=2))
        return
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
    json_progress: JsonProgressReporter | None,
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
                progress=json_progress,
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
                progress=combine_progress(RichProgressReporter(progress), json_progress),
                cancel=cancel,
                token_provider=token_provider,
                resume=resume,
                force=force,
                force_unlock=force_unlock,
            )

    return _run_interruptibly(
        operation,
        hard_exit_on_interrupt=True,
        interrupt_reporter=json_progress,
    )


def _run_batch_with_progress(
    input_path: Path,
    config: Config,
    token_provider: TokenProvider,
    quiet: bool,
    json_progress: JsonProgressReporter | None,
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
                progress=json_progress,
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
                progress=combine_progress(RichProgressReporter(progress), json_progress),
                cancel=cancel,
                recursive=recursive,
                include=include,
                exclude=exclude,
                resume=resume,
                force=force,
                force_unlock=force_unlock,
            )

    return _run_interruptibly(
        operation,
        hard_exit_on_interrupt=True,
        interrupt_reporter=json_progress,
    )


def _run_interruptibly(
    operation: Callable[[CancelToken], T],
    *,
    hard_exit_on_interrupt: bool = False,
    interrupt_reporter: JsonProgressReporter | None = None,
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
            error = CancelledError()
            error_console.print(f"[red]エラー:[/red] {error}")
            if interrupt_reporter is not None:
                interrupt_reporter.error(error, error.exit_code)
                interrupt_reporter.done(error.exit_code)
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


_PROBE_PROGRESS_LABELS = {
    "started": "実行中",
    "completed": "成功",
    "timeout": "タイムアウト (判定不能、次へ進みます)",
    "error": "失敗 (判定不能、次へ進みます)",
    "cached": "成功 (キャッシュ済み)",
    "unknown": "不明 (詳細ログを確認してください)",
}


def _print_probe_progress(item: ProbeProgress) -> None:
    label = _PROBE_PROGRESS_LABELS.get(item.state)
    if label is None:
        logging.getLogger(__name__).warning("Unknown device probe progress state: %r", item.state)
        label = _PROBE_PROGRESS_LABELS["unknown"]
    typer.echo(
        f"[probe {item.position}/{item.total}] {item.label}: {label}",
        err=True,
    )


assert set(_PROBE_PROGRESS_LABELS) == PROBE_PROGRESS_STATES


def _probe_display(available: bool, status: str) -> str:
    if status != "completed":
        return "unknown"
    return "available" if available else "unavailable"


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
        _probe_display(ct2.available, ct2.cpu_status),
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
        (
            "unknown"
            if torch.cuda_status != "completed"
            else "usable"
            if torch.cuda_available
            else "unavailable"
        ),
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
        (
            "unknown"
            if torch.xpu_status != "completed"
            else "usable"
            if torch.xpu_available
            else "unavailable"
        ),
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
        _probe_display(report.openvino.available, report.openvino.status),
        ", ".join(report.openvino.values) or report.openvino.error or "-",
    )
    table.add_row(
        "ONNX Runtime",
        _probe_display(report.onnxruntime.available, report.onnxruntime.status),
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
        _probe_display(report.vulkan.build_available, report.vulkan.status),
        report.vulkan.build_error or "glslc found",
    )
    table.add_row(
        "Vulkan (runtime)",
        _probe_display(report.vulkan.runtime_available, report.vulkan.status),
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
        "デバイスプローブ: "
        + ("キャッシュを使用 (--refresh で再取得)" if report.probe_cache_hit else "再取得済み")
    )
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


def _job_summary_payload(item: JobSummary) -> dict[str, object]:
    """Return the stable machine-readable history summary contract."""
    return {
        "job_id": item.job_id,
        "input_name": item.input_name,
        "status": item.status,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "size_bytes": item.size_bytes,
        "duration_seconds": item.duration_seconds,
        "speaker_count": item.speaker_count,
        "result_available": item.result_available,
        "result_schema_version": item.result_schema_version,
        "result_error": item.result_error,
        "processing": {
            "asr": (
                None
                if item.asr_backend is None
                else {
                    "backend": item.asr_backend,
                    "model": item.asr_model,
                    "device": item.asr_device,
                }
            ),
            "diarization": (
                None
                if item.diarization_backend is None
                else {
                    "backend": item.diarization_backend,
                    "model": item.diarization_model,
                    "device": item.diarization_device,
                }
            ),
        },
        "output_paths": list(item.output_paths),
        "result_path": item.result_path,
        "presentation_path": item.presentation_path,
    }


def _job_detail_payload(job: Job, config: Config) -> dict[str, object]:
    """Build a viewer contract from the authoritative merged intermediate."""
    presentation = job.read_presentation()
    stored_labels = presentation.get("speaker_labels") if presentation is not None else None
    labels = (
        {str(key): str(value) for key, value in stored_labels.items()}
        if isinstance(stored_labels, Mapping)
        else dict(config.output.speaker_labels)
    )
    output_paths = list(job.manifest.stages["export"].artifacts)
    stored_format_items = presentation.get("formats") if presentation is not None else None
    formats = (
        [str(item) for item in stored_format_items]
        if isinstance(stored_format_items, list)
        else [Path(path).suffix.lstrip(".") for path in output_paths]
    )
    output_dir = (
        str(presentation.get("output_dir", ""))
        if presentation is not None
        else (str(Path(output_paths[0]).parent) if output_paths else str(config.general.output_dir))
    )
    detail: dict[str, object] = {
        "schema_version": 1,
        "expected_result_schema_version": INTERMEDIATE_SCHEMA_VERSION,
        "job": {
            "job_id": job.manifest.job_id,
            "input_name": Path(job.manifest.input.path).name,
            "input_path": job.manifest.input.path,
            "status": job.manifest.status,
            "created_at": job.manifest.created_at,
            "updated_at": job.manifest.updated_at,
            "output_paths": output_paths,
            "output_dir": output_dir,
            "formats": list(dict.fromkeys(formats)),
        },
        "manifest": job.manifest.to_dict(),
        "result": None,
        "result_error": None,
    }
    try:
        result = PipelineResult.from_dict(cast(dict[str, Any], job.read_merged_payload()))
    except UtteranError as exc:
        detail["result_error"] = str(exc)
        cast(dict[str, object], detail["job"])["status"] = "corrupt"
        return detail
    except (KeyError, TypeError, ValueError):
        detail["result_error"] = "文字起こし結果の内容が不正です。merged.json を確認してください。"
        cast(dict[str, object], detail["job"])["status"] = "corrupt"
        return detail
    speaker_ids = list(
        dict.fromkeys(segment.speaker for segment in result.segments if segment.speaker is not None)
    )
    detail["result"] = {
        "schema_version": 1,
        "input": {
            "path": str(result.input_path),
            "duration": result.transcription.duration,
        },
        "processing": {
            "asr": {
                "backend": result.transcription.backend,
                "model": result.transcription.model_id,
                "device": result.transcription.device,
            },
            "diarization": (
                None
                if result.diarization is None
                else {
                    "backend": result.diarization.backend,
                    "model": result.diarization.model_id,
                    "device": result.diarization.device,
                }
            ),
            "created_at": result.created_at,
        },
        "speakers": [
            {"id": speaker, "name": labels.get(speaker, speaker)} for speaker in speaker_ids
        ],
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "speaker": segment.speaker,
                "speaker_display": (
                    None
                    if segment.speaker is None
                    else labels.get(segment.speaker, segment.speaker)
                ),
                "text": segment.text,
                "words": [
                    {
                        "start": word.start,
                        "end": word.end,
                        "text": word.text,
                        "probability": word.probability,
                    }
                    for word in segment.words
                ],
            }
            for segment in result.segments
        ],
    }
    return detail


def _parse_export_formats(raw: str | None, defaults: object) -> list[str]:
    """Validate formats for export-only regeneration."""
    formats = (
        [item.strip().lower() for item in raw.split(",") if item.strip()]
        if raw is not None
        else [str(item).lower() for item in cast(list[object], defaults)]
    )
    formats = list(dict.fromkeys(formats))
    supported = {"srt", "vtt", "json", "txt", "md"}
    if not formats:
        raise ConfigurationError("--format には1つ以上の出力形式を指定してください。")
    unknown = [item for item in formats if item not in supported]
    if unknown:
        raise ConfigurationError(f"未対応の出力形式です: {', '.join(unknown)}")
    return formats


def _parse_speaker_labels(
    raw_labels: list[str] | None,
    speakers: tuple[str, ...],
    defaults: Mapping[str, str],
) -> dict[str, str]:
    """Apply explicit label replacements while allowing a saved label to be cleared."""
    labels = {str(key): str(value) for key, value in defaults.items() if str(value)}
    if raw_labels is None:
        return labels
    known = set(speakers)
    for raw in raw_labels:
        if "=" not in raw:
            raise ConfigurationError("--speaker-label は 内部ラベル=表示名 で指定してください。")
        speaker, display_name = (item.strip() for item in raw.split("=", 1))
        if speaker not in known:
            raise ConfigurationError(f"結果に存在しない話者ラベルです: {speaker}")
        if len(display_name) > 100:
            raise ConfigurationError("話者の表示名は100文字以内で指定してください。")
        if display_name:
            labels[speaker] = display_name
        else:
            labels.pop(speaker, None)
    return labels


def _resolve_export_output_dir(
    explicit: Path | None,
    presentation: dict[str, object] | None,
    job: Job,
    config: Config,
) -> Path:
    """Choose an explicit, per-job, previous-artifact, or configured destination."""
    if explicit is not None:
        return explicit
    if presentation is not None:
        stored = str(presentation.get("output_dir", "")).strip()
        if stored:
            return Path(stored)
    artifacts = job.manifest.stages["export"].artifacts
    if artifacts:
        return Path(artifacts[0]).parent
    return config.general.output_dir


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
