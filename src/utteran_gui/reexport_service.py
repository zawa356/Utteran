"""E-C: re-export a finished job's verified JSON into a new directory (no ASR, no model).

Separate from ASR jobs and the queue: nothing here submits, claims a heavy slot, starts a
worker, imports core or writes the B5/B6/B7/E-B stores. The job store is only *read*
(job, private snapshot, completion evidence).

Order of one execution (``execute``):

1. strict body validation; a known Idempotency-Key answers from its record (no new work);
2. one re-export at a time in this process (``reexport_busy`` otherwise);
3. job read: ``succeeded``, revision equal to ``If-Match``, per-job directory (not legacy);
4. the JSON is read by descriptor, its bytes hashed against the completion evidence and
   compared with the ``source_sha256`` the plan showed, then parsed (the same bytes);
5. every output rendered in memory and size-checked (nothing exists yet);
6. ``pending`` record committed (key, digest of the whole request, parent);
7. new directory created, its name recorded, files written with O_EXCL, fsynced;
8. ``completed`` (per-format SHA-256/size) or ``failed`` (fixed code, ``partial`` when files
   may remain). A ``pending`` record of another process (crash, kill) reads ``interrupted``
   and is never redone: a new re-export is always a new key and a new directory.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from utteran_contracts.output_naming import ID_LENGTHS, job_output_name, reexport_output_name
from utteran_contracts.transcript_export import (
    FORMATS,
    MAX_SPEAKERS,
    RenderOptions,
    SourceError,
    TranscriptView,
    parse_json_v1,
    render_bytes,
    speakers,
)
from utteran_control.errors import ControlError
from utteran_gui import reexport_files
from utteran_gui.reexport_files import FileError, PartialOutput, Protected
from utteran_gui.reexport_store import Record, ReexportError, ReexportStore, canonical, iso

MAX_LABEL = 256
_FINAL_ATTEMPTS = 4
_FINAL_RETRY_SECONDS = 0.5
MAX_PARENT = 4096
_SHA = re.compile(r"[0-9a-f]{64}")
_BODY_KEYS = {
    "schema_version",
    "source_sha256",
    "formats",
    "show_speaker",
    "srt_bom",
    "newline",
    "speaker_labels",
    "output_parent",
}
# Refusals of the source and their HTTP status (all fixed codes, no values).
_SOURCE_STATUS = {
    "job_not_succeeded": 409,
    "source_legacy": 409,
    "source_unverified": 409,
    "source_json_not_recorded": 409,
    "source_missing": 409,
    "source_link": 409,
    "source_special": 409,
    "source_changed": 409,
    "source_unavailable": 503,
    "source_too_large": 413,
    "source_invalid": 422,
    "source_noncanonical": 422,
    "source_schema_unsupported": 422,
    "reexport_platform_unsupported": 409,
}


class _Refused(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ReexportService:
    def __init__(
        self,
        store: ReexportStore,
        jobs: Callable[[], Any],
        *,
        clock: Callable[[], float] = time.time,
        supported: Callable[[], bool] = reexport_files.supported,
    ) -> None:
        self.store = store
        self._jobs = jobs
        self._clock = clock
        self._supported = supported
        self._lock = threading.Lock()
        # Keys whose execution is in this process now. Registered *before* the pending record
        # is committed, so a same-key replay in that gap reads ``pending``, never
        # ``interrupted`` (PR review P2-1). Guarded by ``_state``.
        self._state = threading.Lock()
        self._running: set[str] = set()
        # The plan reads, hashes, parses and re-renders the whole source: at most two at once.
        self._plans = threading.BoundedSemaphore(2)

    # ---- source -------------------------------------------------------------------------

    def _job(self, job_id: str) -> tuple[Any, Any, dict[str, tuple[str, int]] | None]:
        store = self._jobs()
        try:
            job = store.get_job(job_id)
            snapshot = store.get_snapshot(job_id)
            evidence = store.artifact_evidence(job_id)
        except ControlError as exc:
            raise ReexportError(exc.code.value, exc.status, retryable=exc.status == 503) from None
        return job, snapshot, evidence

    def _source(
        self, job_id: str, snapshot: Any, evidence: Any, status: str
    ) -> tuple[TranscriptView, str, int, str, Protected]:
        """(view, sha256, size, newline, the job directory) of the verified JSON, or
        _Refused(code). The job directory is what no destination may be inside (P2-2)."""
        if status != "succeeded":
            raise _Refused("job_not_succeeded")
        if not self._supported():
            raise _Refused("reexport_platform_unsupported")
        if snapshot.output_stamp is None:
            raise _Refused("source_legacy")  # pre-0.1.54: ownership of files is not provable
        if not evidence:
            raise _Refused("source_unverified")  # no per-format completion record
        if "json" not in evidence:
            raise _Refused("source_json_not_recorded")
        names = []
        for length in ID_LENGTHS:
            try:
                names.append(
                    job_output_name(
                        snapshot.input_identity.path,
                        snapshot.output_stamp,
                        job_id,
                        parent=snapshot.file.output_dir,
                        id_length=length,
                    )
                )
            except ValueError:
                break
        try:
            source = reexport_files.read_verified_json(
                snapshot.file.output_dir, names, job_id, evidence["json"]
            )
            view, newline = parse_json_v1(source.data)
        except (FileError, SourceError) as exc:
            raise _Refused(exc.code) from None
        protected = Protected(source.directory, source.path)
        return view, evidence["json"][0], evidence["json"][1], newline, protected

    def plan(self, job_id: str) -> dict[str, Any]:
        """What the dialog shows: availability with a fixed reason, the frozen defaults, the
        displayed speaker labels (private, authenticated body only) and the source digest the
        execution must match."""
        if not self._plans.acquire(blocking=False):
            raise ReexportError("reexport_busy", 503)
        try:
            return self._plan(job_id)
        finally:
            self._plans.release()

    def _plan(self, job_id: str) -> dict[str, Any]:
        job, snapshot, evidence = self._job(job_id)
        output = snapshot.resolved.settings.output
        body: dict[str, Any] = {
            "schema_version": 1,
            "job_id": job_id,
            "job_revision": job.revision,
            "available": False,
            "refusal": None,
            "source": None,
            "defaults": {
                "formats": list(output.formats),
                "show_speaker": output.show_speaker,
                "srt_bom": output.srt_bom,
                "newline": output.newline,
            },
            # The JSON holds labels after this mapping: it is never applied a second time.
            "original_labels_applied": bool(output.speaker_labels),
            "default_parent": snapshot.file.output_dir,
            "limits": {
                "max_source_bytes": reexport_files.MAX_SOURCE_BYTES,
                "max_label": MAX_LABEL,
                "max_speakers": MAX_SPEAKERS,
            },
        }
        try:
            view, digest, size, newline, _ = self._source(
                job_id, snapshot, evidence, str(job.status)
            )
        except _Refused as exc:
            body["refusal"] = exc.code
            return body
        body["available"] = True
        body["source"] = {
            "sha256": digest,
            "size": size,
            "newline": newline,
            "segments": len(view.segments),
            "words": sum(len(segment.words) for segment in view.segments),
            "speakers": speakers(view),
            "diarization": view.diarization is not None,
        }
        return body

    # ---- request ------------------------------------------------------------------------

    @staticmethod
    def check_body(body: Any) -> dict[str, Any]:
        def invalid(code: str = "contract_validation_failed") -> ReexportError:
            return ReexportError(code, 422, retryable=False)

        if type(body) is not dict or set(body) != _BODY_KEYS:
            raise invalid()
        if type(body["schema_version"]) is not int or body["schema_version"] != 1:
            raise invalid("schema_version_unsupported")
        if type(body["source_sha256"]) is not str or not _SHA.fullmatch(body["source_sha256"]):
            raise invalid()
        formats = body["formats"]
        if (
            type(formats) is not list
            or not formats
            or any(type(item) is not str for item in formats)
            or len(set(formats)) != len(formats)
            or any(item not in FORMATS for item in formats)
        ):
            raise invalid("formats_invalid")
        for name in ("show_speaker", "srt_bom"):
            if type(body[name]) is not bool:
                raise invalid()
        if body["newline"] not in ("lf", "crlf"):
            raise invalid()
        if body["srt_bom"] and "srt" not in formats:
            raise invalid("option_not_applicable")  # the BOM applies to SRT only
        labels = body["speaker_labels"]
        if type(labels) is not dict or len(labels) > MAX_SPEAKERS:
            raise invalid("speaker_labels_invalid")
        for value in labels.values():
            if (
                type(value) is not str
                or len(value) > MAX_LABEL
                or any(ord(c) < 32 or ord(c) == 127 for c in value)
            ):
                raise invalid("speaker_labels_invalid")
        parent = body["output_parent"]
        if parent is not None and (
            type(parent) is not str or not parent or len(parent) > MAX_PARENT
        ):
            raise invalid("output_parent_invalid")
        # Canonical order: formats keep the B7 order whatever the client sent.
        return {
            **body,
            "formats": [item for item in FORMATS if item in formats],
            "speaker_labels": dict(sorted(labels.items())),
        }

    # ---- execution ----------------------------------------------------------------------

    def execute(self, job_id: str, expected: int, key: str, raw: Any) -> tuple[int, dict[str, Any]]:
        body = self.check_body(raw)
        digest_input = {"job_id": job_id, "job_revision": expected, "body": body}
        if self.store.exists():
            existing = self.store.by_key(key)
            if existing is not None:
                return self._replay(existing, digest_input)
        if not self._lock.acquire(blocking=False):
            raise ReexportError("reexport_busy", 503)
        with self._state:
            self._running.add(key)
        try:
            try:
                return self._execute(job_id, expected, key, body, digest_input)
            except ReexportError as exc:
                # A refusal before our record: if another process committed a record for this
                # key meanwhile, answer from it (a replay), not with the refusal (re-review P3).
                if exc.status in (409, 412, 413, 422) and self.store.exists():
                    try:
                        existing = self.store.by_key(key)
                    except ReexportError:
                        existing = None
                    # Only a record of another process: never our own pending record, whose
                    # error (e.g. ``store_incomplete`` while claiming) must stay visible.
                    if existing is not None and existing.owner != self.store.owner:
                        return self._replay(existing, digest_input)
                raise
        finally:
            with self._state:
                self._running.discard(key)
            self._lock.release()

    def _replay(self, existing: Record, digest_input: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if existing.digest != self.store.digest(digest_input):
            raise ReexportError("idempotency_conflict", 409, retryable=False)
        return 200, self.to_wire(existing)

    def _execute(
        self,
        job_id: str,
        expected: int,
        key: str,
        body: dict[str, Any],
        digest_input: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        job, snapshot, evidence = self._job(job_id)
        if job.revision != expected:
            raise ReexportError("revision_conflict", 412, retryable=False)
        try:
            view, digest, size, _, protected = self._source(
                job_id, snapshot, evidence, str(job.status)
            )
        except _Refused as exc:
            raise ReexportError(
                exc.code, _SOURCE_STATUS.get(exc.code, 409), retryable=False
            ) from None
        if digest != body["source_sha256"]:
            raise ReexportError("source_changed", 409, retryable=False)
        if set(body["speaker_labels"]) - set(speakers(view)):
            raise ReexportError("speaker_label_unknown", 422, retryable=False)
        options = RenderOptions(
            speaker_labels=body["speaker_labels"],
            show_speaker=body["show_speaker"],
            srt_bom=body["srt_bom"],
            newline=body["newline"],
        )
        files: dict[str, bytes] = {}
        total = 0
        for name in body["formats"]:
            files[name] = render_bytes(name, view, options)
            total += len(files[name])
            if total > reexport_files.MAX_OUTPUT_BYTES:
                raise ReexportError("output_too_large", 413, retryable=False)
        explicit = body["output_parent"]
        # The destination is pinned and every refusal of it (inside the job's directory, Git,
        # path budget) happens *before* the record exists (owner review P2-1/P2-2).
        try:
            destination = reexport_files.Destination(
                explicit if explicit is not None else snapshot.file.output_dir, [protected]
            )
        except FileError as exc:
            raise ReexportError(exc.code, 409, retryable=False) from None
        try:
            identifier = "rex_" + uuid4().hex
            stamp = datetime.fromtimestamp(self._clock()).astimezone().strftime("%Y%m%d-%H%M%S")
            names = []
            for length in ID_LENGTHS:
                try:
                    names.append(
                        reexport_output_name(
                            snapshot.input_identity.path,
                            stamp,
                            identifier,
                            parent=destination.parent,
                            id_length=length,
                        )
                    )
                except ValueError:
                    break
            if not names:
                raise ReexportError("output_path_too_long", 409, retryable=False)
            try:
                destination.check_git(names, files, reexport_files.git_check)
            except FileError as exc:
                raise ReexportError(exc.code, 409, retryable=False) from None
            self.store.create_if_missing()  # the first attempted write creates the store
            record, created = self.store.begin(
                job_id=job_id,
                job_revision=expected,
                key=key,
                digest=self.store.digest(digest_input),
                request=canonical(body | {"output_parent": explicit}),
                parent=destination.parent,
                source_sha256=digest,
                source_size=size,
                identifier=identifier,
            )
            if not created:
                return self._replay(record, digest_input)
            return 201, self.to_wire(self._write(record, destination, names, files))
        finally:
            destination.close()

    def _write(
        self,
        record: Record,
        destination: reexport_files.Destination,
        names: list[str],
        files: dict[str, bytes],
    ) -> Record:
        try:
            written = reexport_files.create_output(
                destination,
                names,
                record.id,
                files,
                on_claimed=lambda name: self.store.claim(record.id, name),
            )
        except PartialOutput as exc:
            return self.store.fail(record.id, exc.code, partial=True)
        except FileError as exc:
            return self.store.fail(record.id, exc.code, partial=False)
        files_written = [{"format": f, "sha256": s, "size": n} for f, s, n in written.files]
        # The files are complete and fsynced: retry a busy store a few times before the record
        # is left pending (read as ``interrupted``, never redone) (PR review P3-3).
        for attempt in range(_FINAL_ATTEMPTS):
            try:
                return self.store.complete(record.id, files_written, written.location_verified)
            except ReexportError as exc:
                if exc.code != "store_busy" or attempt == _FINAL_ATTEMPTS - 1:
                    raise
                time.sleep(_FINAL_RETRY_SECONDS)
        raise AssertionError("unreachable")

    # ---- reading ------------------------------------------------------------------------

    def public_state(self, record: Record) -> str:
        with self._state:
            running = record.key in self._running
        if record.state == "pending" and (record.owner != self.store.owner or not running):
            return "interrupted"  # crashed/killed (or its final write failed): files may remain
        return record.state

    def to_wire(self, record: Record) -> dict[str, Any]:
        options = record.options()
        state = self.public_state(record)
        directory = (
            None if record.directory is None else os.path.join(record.parent, record.directory)
        )
        return {
            "schema_version": 1,
            "id": record.id,
            "job_id": record.job_id,
            "job_revision": record.job_revision,
            "state": state,
            "error_code": record.error_code,
            # Files may exist in the new directory (never deleted automatically).
            # An interrupted run may have made its directory before recording the name.
            "partial": record.partial or state == "interrupted",
            "created_at": iso(record.created_ms),
            "updated_at": iso(record.updated_ms),
            "formats": options["formats"],
            "options": {
                "show_speaker": options["show_speaker"],
                "srt_bom": options["srt_bom"],
                "newline": options["newline"],
                "speaker_label_count": len(options["speaker_labels"]),
                "explicit_parent": options["output_parent"] is not None,
            },
            "source_sha256": record.source_sha256,
            "directory": directory,
            "files": [] if record.files is None else json.loads(record.files),
            "location_verified": record.location_verified,
        }

    def list_for_job(self, job_id: str) -> dict[str, Any]:
        records = self.store.for_job(job_id)
        return {
            "schema_version": 1,
            "job_id": job_id,
            "items": [self.to_wire(record) for record in records],
        }

    def get(self, reexport_id: str) -> dict[str, Any]:
        record = self.store.get(reexport_id)
        if record is None:
            raise ReexportError("reexport_not_found", 404, retryable=False)
        return self.to_wire(record)
