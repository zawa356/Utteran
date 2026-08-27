"""Persistent jobs, deterministic stage hashes, manifests, and process locks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import warnings
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TypeAlias, cast

from utteran.config import Config
from utteran.errors import JobLockedError, JobManifestError, JobNotFoundError

MANIFEST_VERSION = 1
INTERMEDIATE_SCHEMA_VERSION = 1
PRESENTATION_SCHEMA_VERSION = 1
FINGERPRINT_CHUNK_SIZE = 1024 * 1024
FLOAT_PRECISION = 12
ASR_POLICY_VERSION = 7
ALIGNMENT_POLICY_VERSION = 5

StageName: TypeAlias = Literal["audio", "asr", "diarization", "merge", "export"]
StageStatus: TypeAlias = Literal["pending", "running", "done", "failed"]
STAGES: tuple[StageName, ...] = ("audio", "asr", "diarization", "merge", "export")
_DEPENDENTS: dict[StageName, tuple[StageName, ...]] = {
    "audio": ("asr", "diarization", "merge", "export"),
    "asr": ("merge", "export"),
    "diarization": ("merge", "export"),
    "merge": ("export",),
    "export": (),
}
_INTERMEDIATE_FILES: dict[StageName, str] = {
    "asr": "asr.json",
    "diarization": "diarization.json",
    "merge": "merged.json",
}
_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")


def _now() -> str:
    """Return an unambiguous local timestamp."""
    return datetime.now().astimezone().isoformat()


@dataclass(frozen=True)
class InputFingerprint:
    """Stable metadata and bounded-content identity for one input file."""

    path: str
    size: int
    mtime: float
    hash: str

    def to_dict(self) -> dict[str, object]:
        """Return JSON-compatible fingerprint fields."""
        return {
            "path": self.path,
            "size": self.size,
            "mtime": self.mtime,
            "hash": self.hash,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> InputFingerprint:
        """Validate and restore fingerprint fields."""
        return cls(
            path=str(data["path"]),
            size=int(cast(int | str, data["size"])),
            mtime=float(cast(float | int | str, data["mtime"])),
            hash=str(data["hash"]),
        )


@dataclass
class StageRecord:
    """Persistent state for one independently resumable pipeline stage."""

    status: StageStatus = "pending"
    config_hash: str | None = None
    finished_at: str | None = None
    error: str | None = None
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Return the manifest representation."""
        return {
            "status": self.status,
            "config_hash": self.config_hash,
            "finished_at": self.finished_at,
            "error": self.error,
            "artifacts": list(self.artifacts),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> StageRecord:
        """Restore a stage, resetting interrupted work to pending."""
        raw_status = str(data.get("status", "pending"))
        status: StageStatus = (
            cast(StageStatus, raw_status)
            if raw_status in {"pending", "running", "done", "failed"}
            else "pending"
        )
        if status == "running":
            status = "pending"
        raw_artifacts = data.get("artifacts", [])
        artifacts = [str(item) for item in raw_artifacts] if isinstance(raw_artifacts, list) else []
        return cls(
            status=status,
            config_hash=(None if data.get("config_hash") is None else str(data["config_hash"])),
            finished_at=(None if data.get("finished_at") is None else str(data["finished_at"])),
            error=None if data.get("error") is None else str(data["error"]),
            artifacts=artifacts,
        )


@dataclass
class JobManifest:
    """Versioned job manifest persisted after every stage transition."""

    job_id: str
    input: InputFingerprint
    created_at: str
    updated_at: str
    stages: dict[StageName, StageRecord]
    version: int = MANIFEST_VERSION

    @classmethod
    def create(cls, job_id: str, fingerprint: InputFingerprint) -> JobManifest:
        """Create a new pending manifest."""
        timestamp = _now()
        return cls(
            job_id=job_id,
            input=fingerprint,
            created_at=timestamp,
            updated_at=timestamp,
            stages={stage: StageRecord() for stage in STAGES},
        )

    def to_dict(self) -> dict[str, object]:
        """Return the documented manifest schema plus artifact diagnostics."""
        return {
            "version": self.version,
            "job_id": self.job_id,
            "input": self.input.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "stages": {name: self.stages[name].to_dict() for name in STAGES},
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> JobManifest:
        """Validate and restore a supported manifest."""
        if int(cast(int | str, data.get("version", 0))) != MANIFEST_VERSION:
            raise JobManifestError("未対応のジョブ manifest バージョンです。再計算します。")
        raw_stages = data.get("stages")
        if not isinstance(raw_stages, Mapping):
            raise JobManifestError("ジョブ manifest に stages がありません。")
        stages: dict[StageName, StageRecord] = {}
        for stage in STAGES:
            raw_stage = raw_stages.get(stage, {})
            stages[stage] = StageRecord.from_dict(
                cast(Mapping[str, object], raw_stage) if isinstance(raw_stage, Mapping) else {}
            )
        raw_input = data.get("input")
        if not isinstance(raw_input, Mapping):
            raise JobManifestError("ジョブ manifest に input がありません。")
        return cls(
            version=MANIFEST_VERSION,
            job_id=str(data["job_id"]),
            input=InputFingerprint.from_dict(cast(Mapping[str, object], raw_input)),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            stages=stages,
        )

    @property
    def status(self) -> str:
        """Return a compact overall status for job listings."""
        statuses = {record.status for record in self.stages.values()}
        if "failed" in statuses:
            return "failed"
        if statuses == {"done"}:
            return "done"
        if "running" in statuses:
            return "running"
        return "pending"


@dataclass(frozen=True)
class JobSummary:
    """Human/CLI-facing job list entry."""

    job_id: str
    input_name: str
    status: str
    updated_at: str
    size_bytes: int
    created_at: str | None = None
    asr_backend: str | None = None
    asr_model: str | None = None
    asr_device: str | None = None
    diarization_backend: str | None = None
    diarization_model: str | None = None
    diarization_device: str | None = None
    speaker_count: int | None = None
    duration_seconds: float | None = None
    result_available: bool = False
    result_schema_version: int | None = None
    result_error: str | None = None
    output_paths: tuple[str, ...] = ()
    result_path: str | None = None
    presentation_path: str | None = None


class Job:
    """One job directory and its mutable manifest."""

    def __init__(self, root: Path, manifest: JobManifest) -> None:
        self.root = root
        self.manifest = manifest

    @property
    def manifest_path(self) -> Path:
        """Return this job's manifest path."""
        return self.root / "manifest.json"

    @property
    def lock_path(self) -> Path:
        """Return this job's lock path."""
        return self.root / ".lock"

    @property
    def audio_path(self) -> Path:
        """Return the persistent normalized WAV path."""
        return self.root / "audio.wav"

    @property
    def presentation_path(self) -> Path:
        """Return per-job export presentation metadata kept beside the transcript."""
        return self.root / "presentation.json"

    def intermediate_path(self, stage: StageName) -> Path:
        """Return a stage JSON path."""
        try:
            return self.root / _INTERMEDIATE_FILES[stage]
        except KeyError:
            raise ValueError(f"中間 JSON を持たないステージです: {stage}") from None

    def save(self) -> None:
        """Atomically persist the complete manifest."""
        self.manifest.updated_at = _now()
        _atomic_write_json(self.manifest_path, self.manifest.to_dict())

    def lock(self, *, force: bool = False) -> JobLock:
        """Return a lock context manager for this job."""
        return JobLock(self.lock_path, force=force)

    def reconcile(self, hashes: Mapping[StageName, str], *, force: bool = False) -> None:
        """Invalidate stale or incomplete stages while preserving independent work."""
        invalid: set[StageName] = set(STAGES if force else ())
        if not force:
            for stage in STAGES:
                record = self.manifest.stages[stage]
                if (
                    record.status != "done"
                    or record.config_hash != hashes[stage]
                    or not self._artifacts_valid(stage, record.artifacts)
                ):
                    invalid.add(stage)
        for stage in tuple(invalid):
            invalid.update(_DEPENDENTS[stage])
        for stage in invalid:
            self.manifest.stages[stage] = StageRecord()
        self.save()

    def is_done(self, stage: StageName, config_hash: str) -> bool:
        """Return whether a stage and its artifacts are reusable."""
        record = self.manifest.stages[stage]
        return (
            record.status == "done"
            and record.config_hash == config_hash
            and self._artifacts_valid(stage, record.artifacts)
        )

    def start_stage(self, stage: StageName, config_hash: str) -> None:
        """Persist a running transition before starting expensive work."""
        self.manifest.stages[stage] = StageRecord(
            status="running",
            config_hash=config_hash,
        )
        self.save()

    def complete_stage(
        self,
        stage: StageName,
        config_hash: str,
        artifacts: Sequence[Path] = (),
    ) -> None:
        """Persist a successful stage after all artifacts exist."""
        self.manifest.stages[stage] = StageRecord(
            status="done",
            config_hash=config_hash,
            finished_at=_now(),
            artifacts=[str(path.absolute()) for path in artifacts],
        )
        self.save()

    def fail_stage(self, stage: StageName, config_hash: str, error: str) -> None:
        """Persist an actionable failed state without a traceback or secret."""
        self.manifest.stages[stage] = StageRecord(
            status="failed",
            config_hash=config_hash,
            finished_at=_now(),
            error=error,
        )
        self.save()

    def interrupt_stage(self, stage: StageName) -> None:
        """Return an interrupted running stage to pending for the next resume."""
        self.manifest.stages[stage] = StageRecord()
        self.save()

    def write_intermediate(self, stage: StageName, result: object) -> Path:
        """Atomically write a versioned stage payload."""
        path = self.intermediate_path(stage)
        _atomic_write_json(
            path,
            {"schema_version": INTERMEDIATE_SCHEMA_VERSION, "result": result},
        )
        return path

    def read_intermediate(self, stage: StageName) -> object | None:
        """Read a compatible payload, returning None when it must be recomputed."""
        path = self.intermediate_path(stage)
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, dict):
                raise ValueError
            if data.get("schema_version") != INTERMEDIATE_SCHEMA_VERSION:
                warnings.warn(
                    f"{path.name} のスキーマが古いため再計算します。",
                    UserWarning,
                    stacklevel=2,
                )
                return None
            return cast(object, data["result"])
        except (OSError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            warnings.warn(
                f"{path.name} を読み込めないため再計算します。",
                UserWarning,
                stacklevel=2,
            )
            return None

    def read_merged_payload(self) -> dict[str, object]:
        """Read the authoritative viewer/export payload with strict schema validation."""
        path = self.intermediate_path("merge")
        try:
            with path.open(encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise JobManifestError(
                "文字起こし結果を読み込めません。merged.json が存在し、正常か確認してください。"
            ) from exc
        if not isinstance(data, Mapping):
            raise JobManifestError("文字起こし結果のルート形式が不正です。")
        raw_version = data.get("schema_version")
        try:
            version = int(cast(int | str, raw_version))
        except (TypeError, ValueError):
            raise JobManifestError("文字起こし結果に schema_version がありません。") from None
        if version != INTERMEDIATE_SCHEMA_VERSION:
            raise JobManifestError(
                "文字起こし結果のスキーマに対応していません。"
                f"対応={INTERMEDIATE_SCHEMA_VERSION}、検出={version}。"
            )
        result = data.get("result")
        if not isinstance(result, Mapping):
            raise JobManifestError("文字起こし結果に result オブジェクトがありません。")
        return {str(key): cast(object, value) for key, value in result.items()}

    def read_presentation(self) -> dict[str, object] | None:
        """Read optional per-job labels/output choices without using GUI settings."""
        try:
            with self.presentation_path.open(encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, Mapping):
                return None
            if data.get("schema_version") != PRESENTATION_SCHEMA_VERSION:
                return None
            labels = data.get("speaker_labels", {})
            formats = data.get("formats", [])
            outputs = data.get("outputs", [])
            if not isinstance(labels, Mapping) or not isinstance(formats, list):
                return None
            if not isinstance(outputs, list):
                return None
            return {
                "schema_version": PRESENTATION_SCHEMA_VERSION,
                "output_dir": str(data.get("output_dir", "")),
                "formats": [str(item) for item in formats],
                "speaker_labels": {str(key): str(value) for key, value in labels.items()},
                "outputs": [str(item) for item in outputs],
                "updated_at": str(data.get("updated_at", "")),
            }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def write_presentation(
        self,
        *,
        output_dir: Path,
        formats: Sequence[str],
        speaker_labels: Mapping[str, str],
        outputs: Sequence[Path],
    ) -> None:
        """Persist participant display names only inside their associated job."""
        _atomic_write_json(
            self.presentation_path,
            {
                "schema_version": PRESENTATION_SCHEMA_VERSION,
                "output_dir": str(output_dir.resolve()),
                "formats": list(dict.fromkeys(str(item) for item in formats)),
                "speaker_labels": {
                    str(key): str(value) for key, value in speaker_labels.items() if str(value)
                },
                "outputs": [str(path.resolve()) for path in outputs],
                "updated_at": _now(),
            },
        )

    def _artifacts_valid(self, stage: StageName, artifacts: Sequence[str]) -> bool:
        """Validate recorded outputs and intermediate schema versions."""
        if stage == "audio":
            return self.audio_path.is_file() and self.audio_path.stat().st_size > 0
        if stage in _INTERMEDIATE_FILES:
            path = self.intermediate_path(stage)
            if not path.is_file():
                return False
            try:
                with path.open(encoding="utf-8") as file:
                    data = json.load(file)
                return (
                    isinstance(data, dict)
                    and data.get("schema_version") == INTERMEDIATE_SCHEMA_VERSION
                    and "result" in data
                )
            except (OSError, json.JSONDecodeError):
                return False
        if stage == "export":
            return bool(artifacts) and all(Path(path).is_file() for path in artifacts)
        return False


class JobLock:
    """Atomic PID lock with stale-process recovery."""

    def __init__(self, path: Path, *, force: bool = False) -> None:
        self.path = path
        self.force = force
        self._held = False

    def __enter__(self) -> JobLock:
        """Acquire the lock or report its live owner."""
        self.acquire()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        """Release only a lock still owned by this process."""
        self.release()

    def acquire(self) -> None:
        """Atomically create the lock after resolving stale ownership."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.force:
            self.path.unlink(missing_ok=True)
        payload = json.dumps({"pid": os.getpid(), "started_at": _now()})
        for _attempt in range(3):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                owner_pid = _read_lock_pid(self.path)
                if owner_pid is not None and _process_exists(owner_pid):
                    raise JobLockedError(
                        f"ジョブは PID {owner_pid} で実行中です。"
                        "確認のうえ --force-unlock を指定できます。"
                    ) from None
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
                lock_file.write(payload)
                lock_file.flush()
                os.fsync(lock_file.fileno())
            self._held = True
            return
        raise JobLockedError("ジョブロックを取得できません。もう一度実行してください。")

    def release(self) -> None:
        """Remove this process's lock without disturbing a replacement owner."""
        if not self._held:
            return
        if _read_lock_pid(self.path) == os.getpid():
            self.path.unlink(missing_ok=True)
        self._held = False


class JobStore:
    """Create, inspect, and clean jobs below one configured directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def open(self, input_path: Path) -> Job:
        """Open the input's single job, recovering incompatible manifests safely."""
        fingerprint = fingerprint_input(input_path)
        job_id = job_id_from_input_hash(fingerprint.hash)
        job_root = self.root / job_id
        job_root.mkdir(parents=True, exist_ok=True)
        manifest_path = job_root / "manifest.json"
        manifest = _load_manifest(manifest_path)
        if manifest is None:
            manifest = JobManifest.create(job_id, fingerprint)
            job = Job(job_root, manifest)
            job.save()
            return job
        if manifest.job_id != job_id or manifest.input.hash != fingerprint.hash:
            raise JobManifestError(f"ジョブ識別情報が入力と一致しません: {manifest_path}")
        manifest.input = fingerprint
        job = Job(job_root, manifest)
        job.save()
        return job

    def get(self, job_id: str) -> Job:
        """Open an existing job by its safe fixed-width identifier."""
        if not _JOB_ID_PATTERN.fullmatch(job_id):
            raise JobNotFoundError(f"不正なジョブ ID です: {job_id}")
        root = self.root / job_id
        manifest = _load_manifest(root / "manifest.json", recover=False)
        if manifest is None:
            raise JobNotFoundError(f"ジョブが見つかりません: {job_id}")
        return Job(root, manifest)

    def list_jobs(self) -> list[JobSummary]:
        """List valid manifests in stable newest-first order."""
        if not self.root.is_dir():
            return []
        summaries: list[JobSummary] = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or not _JOB_ID_PATTERN.fullmatch(directory.name):
                continue
            manifest = _load_manifest(directory / "manifest.json", recover=False)
            if manifest is None:
                try:
                    updated_at = (
                        datetime.fromtimestamp(
                            directory.stat().st_mtime,
                            tz=UTC,
                        )
                        .astimezone()
                        .isoformat()
                    )
                except OSError:
                    updated_at = datetime.fromtimestamp(0, tz=UTC).isoformat()
                summaries.append(
                    JobSummary(
                        job_id=directory.name,
                        input_name="<manifest unreadable>",
                        status="corrupt",
                        updated_at=updated_at,
                        size_bytes=_directory_size(directory),
                    )
                )
                continue
            result_metadata = _result_metadata(directory / _INTERMEDIATE_FILES["merge"])
            status = manifest.status
            if (
                manifest.stages["merge"].status == "done"
                and not result_metadata["result_available"]
            ):
                status = "corrupt"
                if result_metadata["result_error"] is None:
                    result_metadata["result_error"] = (
                        "文字起こし結果を読み込めません。merged.json がありません。"
                    )
            summaries.append(
                JobSummary(
                    job_id=manifest.job_id,
                    input_name=Path(manifest.input.path).name,
                    status=status,
                    updated_at=manifest.updated_at,
                    size_bytes=_directory_size(directory),
                    created_at=manifest.created_at,
                    asr_backend=cast(str | None, result_metadata["asr_backend"]),
                    asr_model=cast(str | None, result_metadata["asr_model"]),
                    asr_device=cast(str | None, result_metadata["asr_device"]),
                    diarization_backend=cast(str | None, result_metadata["diarization_backend"]),
                    diarization_model=cast(str | None, result_metadata["diarization_model"]),
                    diarization_device=cast(str | None, result_metadata["diarization_device"]),
                    speaker_count=cast(int | None, result_metadata["speaker_count"]),
                    duration_seconds=cast(float | None, result_metadata["duration_seconds"]),
                    result_available=bool(result_metadata["result_available"]),
                    result_schema_version=cast(
                        int | None, result_metadata["result_schema_version"]
                    ),
                    result_error=cast(str | None, result_metadata["result_error"]),
                    output_paths=tuple(manifest.stages["export"].artifacts),
                    result_path=str((directory / _INTERMEDIATE_FILES["merge"]).resolve()),
                    presentation_path=str((directory / "presentation.json").resolve()),
                )
            )
        return sorted(summaries, key=lambda item: (item.updated_at, item.job_id), reverse=True)

    def clean_candidates(
        self,
        *,
        all_jobs: bool = False,
        failed: bool = False,
        older_than_days: int | None = None,
    ) -> list[JobSummary]:
        """Select jobs for a separately confirmed destructive operation."""
        cutoff = (
            None if older_than_days is None else datetime.now(UTC) - timedelta(days=older_than_days)
        )
        selected: list[JobSummary] = []
        for summary in self.list_jobs():
            updated = datetime.fromisoformat(summary.updated_at)
            if (
                all_jobs
                or (failed and summary.status in {"failed", "corrupt"})
                or (cutoff is not None and updated.astimezone(UTC) < cutoff)
            ):
                selected.append(summary)
        return selected

    def remove(self, job_ids: Sequence[str]) -> None:
        """Remove only validated direct child job directories."""
        for job_id in job_ids:
            if not _JOB_ID_PATTERN.fullmatch(job_id):
                raise JobNotFoundError(f"不正なジョブ ID です: {job_id}")
            directory = self.root / job_id
            if directory.is_dir():
                owner_pid = _read_lock_pid(directory / ".lock")
                if owner_pid is not None and _process_exists(owner_pid):
                    raise JobLockedError(f"ジョブは PID {owner_pid} で実行中のため削除できません。")
                shutil.rmtree(directory)


def _result_metadata(path: Path) -> dict[str, object]:
    """Extract history fields from merged.json without exposing transcript text."""
    metadata: dict[str, object] = {
        "asr_backend": None,
        "asr_model": None,
        "asr_device": None,
        "diarization_backend": None,
        "diarization_model": None,
        "diarization_device": None,
        "speaker_count": None,
        "duration_seconds": None,
        "result_available": False,
        "result_schema_version": None,
        "result_error": None,
    }
    if not path.is_file():
        return metadata
    try:
        with path.open(encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, Mapping):
            raise ValueError("root")
        raw_version = data.get("schema_version")
        version = int(cast(int | str, raw_version))
        metadata["result_schema_version"] = version
        if version != INTERMEDIATE_SCHEMA_VERSION:
            metadata["result_error"] = (
                "文字起こし結果のスキーマに対応していません。"
                f"対応={INTERMEDIATE_SCHEMA_VERSION}、検出={version}。"
            )
            return metadata
        result = data.get("result")
        if not isinstance(result, Mapping):
            raise ValueError("result")
        transcription = result.get("transcription")
        if not isinstance(transcription, Mapping):
            raise ValueError("transcription")
        metadata.update(
            {
                "asr_backend": str(transcription.get("backend", "")) or None,
                "asr_model": str(transcription.get("model_id", "")) or None,
                "asr_device": str(transcription.get("device", "")) or None,
                "duration_seconds": float(cast(float | int | str, transcription["duration"])),
            }
        )
        diarization = result.get("diarization")
        if isinstance(diarization, Mapping):
            metadata.update(
                {
                    "diarization_backend": str(diarization.get("backend", "")) or None,
                    "diarization_model": str(diarization.get("model_id", "")) or None,
                    "diarization_device": str(diarization.get("device", "")) or None,
                }
            )
        segments = result.get("segments")
        if not isinstance(segments, list):
            raise ValueError("segments")
        speakers: set[str] = set()
        for segment in segments:
            if isinstance(segment, Mapping) and segment.get("speaker") is not None:
                speakers.add(str(segment["speaker"]))
        metadata["speaker_count"] = len(speakers)
        metadata["result_available"] = True
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        metadata["result_error"] = "文字起こし結果を読み込めません。merged.json が破損しています。"
    return metadata


def fingerprint_input(path: Path) -> InputFingerprint:
    """Hash size, mtime, and bounded leading/trailing content as specified."""
    resolved = path.resolve()
    stat_before = resolved.stat()
    digest = hashlib.sha256()
    digest.update(str(stat_before.st_size).encode("ascii"))
    digest.update(b"\0")
    digest.update(str(stat_before.st_mtime_ns).encode("ascii"))
    with resolved.open("rb") as input_file:
        digest.update(input_file.read(FINGERPRINT_CHUNK_SIZE))
        if stat_before.st_size > FINGERPRINT_CHUNK_SIZE:
            input_file.seek(max(0, stat_before.st_size - FINGERPRINT_CHUNK_SIZE))
            digest.update(input_file.read(FINGERPRINT_CHUNK_SIZE))
    stat_after = resolved.stat()
    if (
        stat_before.st_size != stat_after.st_size
        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
    ):
        raise JobManifestError("ハッシュ計算中に入力ファイルが変更されました。再実行してください。")
    return InputFingerprint(
        path=str(resolved),
        size=stat_before.st_size,
        mtime=stat_before.st_mtime,
        hash=digest.hexdigest(),
    )


def job_id_from_input_hash(input_hash: str) -> str:
    """Derive the fixed-width job ID from only the input fingerprint hash."""
    return hashlib.sha256(input_hash.encode("ascii")).hexdigest()[:16]


def canonical_json(value: object) -> str:
    """Serialize config material independently of dict order and locale."""
    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def config_hash(value: object) -> str:
    """Hash one canonical configuration value."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stage_config_hashes(config: Config, input_hash: str) -> dict[StageName, str]:
    """Calculate all documented stage hashes from their exact dependencies."""
    audio_hash = config_hash(
        {
            "input_hash": input_hash,
            "sample_rate": 16_000,
            "channels": 1,
            "sample_format": "pcm_s16le",
        }
    )
    asr_hash = config_hash(
        {
            "audio_config_hash": audio_hash,
            "asr": config.asr.model_dump(mode="json"),
            "policy_version": ASR_POLICY_VERSION,
        }
    )
    diarization_hash = config_hash(
        {
            "audio_config_hash": audio_hash,
            "diarization": config.diarization.model_dump(mode="json"),
        }
    )
    merge_hash = config_hash(
        {
            "asr_config_hash": asr_hash,
            "diarization_config_hash": diarization_hash,
            "alignment": config.alignment.model_dump(mode="json"),
            "policy_version": ALIGNMENT_POLICY_VERSION,
        }
    )
    export_hash = config_hash(
        {
            "merge_config_hash": merge_hash,
            "output": config.output.model_dump(mode="json"),
            "output_dir": str(config.general.output_dir.absolute()),
        }
    )
    return {
        "audio": audio_hash,
        "asr": asr_hash,
        "diarization": diarization_hash,
        "merge": merge_hash,
        "export": export_hash,
    }


def _canonicalize(value: object) -> object:
    """Recursively normalize floats, paths, mappings, and sequences."""
    if isinstance(value, float):
        return format(value, f".{FLOAT_PRECISION}f")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(value[key]) for key in sorted(value, key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"config hash に変換できない型です: {type(value).__name__}")


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Write JSON through a same-directory temporary file and replace atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _load_manifest(path: Path, *, recover: bool = True) -> JobManifest | None:
    """Load a manifest, optionally preserving broken content and starting fresh."""
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as file:
            raw = json.load(file)
        if not isinstance(raw, Mapping):
            raise JobManifestError("ジョブ manifest のルートがオブジェクトではありません。")
        return JobManifest.from_dict(cast(Mapping[str, object], raw))
    except (
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        JobManifestError,
    ) as exc:
        if not recover:
            return None
        backup = path.with_name(f"manifest.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
        with suppress(OSError):
            path.replace(backup)
        warnings.warn(
            f"ジョブ manifest を読み込めないため新規作成します: {exc}",
            UserWarning,
            stacklevel=2,
        )
        return None


def _read_lock_pid(path: Path) -> int | None:
    """Read a positive PID from a lock, treating malformed locks as stale."""
    try:
        with path.open(encoding="utf-8") as lock_file:
            data = json.load(lock_file)
        pid = int(data["pid"])
        return pid if pid > 0 else None
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _process_exists(pid: int) -> bool:
    """Check process liveness without sending a terminating Windows signal."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_exists(pid: int) -> bool:
    """Query a Windows process handle and exit code without using os.kill."""
    import ctypes
    from ctypes import wintypes

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        return False
    kernel32 = loader("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
        return int(get_last_error()) == 5
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return int(exit_code.value) == 259
    finally:
        kernel32.CloseHandle(handle)


def _directory_size(path: Path) -> int:
    """Return the sum of regular file sizes below a job directory."""
    total = 0
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                total += candidate.stat().st_size
        except OSError:
            continue
    return total
