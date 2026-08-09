"""Core-independent job index and direct merged.json viewer adapter."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from utteran_gui.cli import CliAdapter
from utteran_gui.security import sanitize_json

HISTORY_SCHEMA_VERSION = 1
MERGED_SCHEMA_VERSION = 1
VIEWER_SCHEMA_VERSION = 1
PRESENTATION_SCHEMA_VERSION = 1
MAX_RESULT_BYTES = 512 * 1024 * 1024
_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")


class HistoryError(RuntimeError):
    """A history/result contract is unavailable or incompatible."""


class HistoryService:
    """Locate through the CLI, then read the authoritative JSON without core startup."""

    def __init__(self, cli: CliAdapter) -> None:
        self.cli = cli
        self._indexes: dict[str, dict[str, dict[str, Any]]] = {}
        self._default_labels: dict[str, dict[str, str]] = {}

    def list(self, profile: str, *, refresh: bool = True) -> dict[str, object]:
        """Return and cache a validated jobs list contract."""
        if not refresh and profile in self._indexes:
            return {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "jobs": [self._public_job(item) for item in self._indexes[profile].values()],
            }
        raw = self.cli.list_jobs(profile)
        if not isinstance(raw, Mapping) or raw.get("schema_version") != HISTORY_SCHEMA_VERSION:
            raise HistoryError("Job history schema is not supported")
        jobs = raw.get("jobs")
        if not isinstance(jobs, list):
            raise HistoryError("Job history does not contain a jobs list")
        index: dict[str, dict[str, Any]] = {}
        raw_labels = raw.get("speaker_labels", {})
        self._default_labels[profile] = (
            {str(key): str(value) for key, value in raw_labels.items() if str(value)}
            if isinstance(raw_labels, Mapping)
            else {}
        )
        for raw_job in jobs:
            if not isinstance(raw_job, Mapping):
                continue
            job = {str(key): value for key, value in raw_job.items()}
            job_id = str(job.get("job_id", ""))
            if _JOB_ID_PATTERN.fullmatch(job_id):
                index[job_id] = job
        self._indexes[profile] = index
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "jobs": [self._public_job(item) for item in index.values()],
        }

    def detail(self, profile: str, job_id: str) -> dict[str, object]:
        """Read and normalize merged.json in-process for sub-second initial rendering."""
        if not _JOB_ID_PATTERN.fullmatch(job_id):
            raise HistoryError("Invalid job ID")
        if profile not in self._indexes or job_id not in self._indexes[profile]:
            self.list(profile)
        try:
            summary = self._indexes[profile][job_id]
        except KeyError:
            raise HistoryError("Job not found") from None
        detail = self._empty_detail(summary)
        result_path = self._job_file(summary.get("result_path"), job_id, "merged.json")
        if result_path is None:
            detail["result_error"] = "文字起こし結果の場所を取得できません。"
            self._mark_corrupt(detail)
            return detail
        try:
            if result_path.stat().st_size > MAX_RESULT_BYTES:
                raise HistoryError("文字起こし結果がGUIの安全な読込上限を超えています。")
            with result_path.open(encoding="utf-8") as file:
                payload = json.load(file)
            if not isinstance(payload, Mapping):
                raise HistoryError("文字起こし結果のルート形式が不正です。")
            detected = payload.get("schema_version")
            if detected != MERGED_SCHEMA_VERSION:
                raise HistoryError(
                    "文字起こし結果のスキーマに対応していません。"
                    f"対応={MERGED_SCHEMA_VERSION}、検出={detected}。"
                )
            raw_result = payload.get("result")
            if not isinstance(raw_result, Mapping):
                raise HistoryError("文字起こし結果にresultオブジェクトがありません。")
            detail["result"] = self._viewer_result(raw_result, summary, profile, job_id)
        except HistoryError as exc:
            detail["result_error"] = str(exc)
            self._mark_corrupt(detail)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            detail["result_error"] = "文字起こし結果を読み込めません。merged.jsonが破損しています。"
            self._mark_corrupt(detail)
        return cast(dict[str, object], sanitize_json(detail))

    def invalidate(self, profile: str, job_id: str | None = None) -> None:
        """Discard paths after external delete/export changes."""
        if job_id is None:
            self._indexes.pop(profile, None)
            self._default_labels.pop(profile, None)
            return
        self._indexes.get(profile, {}).pop(job_id, None)

    def _viewer_result(
        self,
        result: Mapping[str, object],
        summary: Mapping[str, object],
        profile: str,
        job_id: str,
    ) -> dict[str, object]:
        transcription = self._mapping(result.get("transcription"), "transcription")
        segments = result.get("segments")
        if not isinstance(segments, list):
            raise HistoryError("文字起こし結果にsegments配列がありません。")
        diarization_value = result.get("diarization")
        diarization = (
            None if diarization_value is None else self._mapping(diarization_value, "diarization")
        )
        labels = self._speaker_labels(summary, profile, job_id)
        speaker_ids: list[str] = []
        viewer_segments: list[dict[str, object]] = []
        for raw_segment in segments:
            segment = self._mapping(raw_segment, "segment")
            raw_words = segment.get("words", [])
            if not isinstance(raw_words, list):
                raise HistoryError("文字起こしsegmentのwords形式が不正です。")
            speaker_value = segment.get("speaker")
            speaker = None if speaker_value is None else str(speaker_value)
            if speaker is not None and speaker not in speaker_ids:
                speaker_ids.append(speaker)
            viewer_segments.append(
                {
                    "start": float(cast(float | int | str, segment["start"])),
                    "end": float(cast(float | int | str, segment["end"])),
                    "speaker": speaker,
                    "speaker_display": None if speaker is None else labels.get(speaker, speaker),
                    "text": str(segment["text"]),
                    "word_count": len(raw_words),
                }
            )
        return {
            "schema_version": VIEWER_SCHEMA_VERSION,
            "input": {
                "path": str(result.get("input_path", "")),
                "duration": float(cast(float | int | str, transcription["duration"])),
            },
            "processing": {
                "asr": {
                    "backend": str(transcription.get("backend", "")),
                    "model": str(transcription.get("model_id", "")),
                    "device": str(transcription.get("device", "")),
                },
                "diarization": (
                    None
                    if diarization is None
                    else {
                        "backend": str(diarization.get("backend", "")),
                        "model": str(diarization.get("model_id", "")),
                        "device": str(diarization.get("device", "")),
                    }
                ),
                "created_at": str(result.get("created_at", "")),
            },
            "speakers": [
                {"id": speaker, "name": labels.get(speaker, speaker)} for speaker in speaker_ids
            ],
            "segments": viewer_segments,
        }

    def _speaker_labels(
        self, summary: Mapping[str, object], profile: str, job_id: str
    ) -> dict[str, str]:
        path = self._job_file(summary.get("presentation_path"), job_id, "presentation.json")
        if path is None or not path.is_file():
            return dict(self._default_labels.get(profile, {}))
        try:
            with path.open(encoding="utf-8") as file:
                payload = json.load(file)
            if not isinstance(payload, Mapping):
                return dict(self._default_labels.get(profile, {}))
            if payload.get("schema_version") != PRESENTATION_SCHEMA_VERSION:
                return dict(self._default_labels.get(profile, {}))
            labels = payload.get("speaker_labels")
            if not isinstance(labels, Mapping):
                return dict(self._default_labels.get(profile, {}))
            return {str(key): str(value) for key, value in labels.items() if str(value)}
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return dict(self._default_labels.get(profile, {}))

    @staticmethod
    def _job_file(value: object, job_id: str, expected_name: str) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value).expanduser().resolve()
        if path.name != expected_name or path.parent.name != job_id:
            raise HistoryError("Job result path failed validation")
        return path

    @staticmethod
    def _mapping(value: object, name: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise HistoryError(f"文字起こし結果の{name}形式が不正です。")
        return cast(Mapping[str, Any], value)

    @staticmethod
    def _empty_detail(summary: Mapping[str, object]) -> dict[str, object]:
        output_paths = summary.get("output_paths", [])
        paths = [str(item) for item in output_paths] if isinstance(output_paths, list) else []
        formats = list(dict.fromkeys(Path(path).suffix.lstrip(".") for path in paths))
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "expected_result_schema_version": MERGED_SCHEMA_VERSION,
            "job": {
                **HistoryService._public_job(summary),
                "input_path": "",
                "output_paths": paths,
                "output_dir": str(Path(paths[0]).parent) if paths else "",
                "formats": formats,
            },
            "result": None,
            "result_error": None,
        }

    @staticmethod
    def _public_job(summary: Mapping[str, object]) -> dict[str, object]:
        """Keep trusted locator paths server-side rather than returning them to WebView."""
        return {
            str(key): value
            for key, value in summary.items()
            if key not in {"result_path", "presentation_path"}
        }

    @staticmethod
    def _mark_corrupt(detail: dict[str, object]) -> None:
        job = detail.get("job")
        if isinstance(job, dict):
            job["status"] = "corrupt"
