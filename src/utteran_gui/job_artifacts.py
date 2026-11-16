"""Read-only, value-free view of a job's own output directory. Never writes or deletes.

Only the 0.1.54 per-job directory is inspected, and only when its ownership marker names
this job. Nothing about names or paths leaves this module: the public view carries the
format, the size and whether the bytes still equal the digest recorded at completion.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from utteran_contracts.jobs import JobArtifacts, JobSnapshot
from utteran_contracts.output_naming import ID_LENGTHS, job_output_name
from utteran_gui.job_dispatcher import OUTPUT_MARKER, _owner

FORMATS = ("srt", "vtt", "json", "txt", "md")
# Transcripts are small text files; anything larger is not hashed for a listing.
HASH_LIMIT = 64 * 1024 * 1024


def owned_directory(snapshot: JobSnapshot, job_id: str) -> Path | None:
    """The directory whose marker names this job, at any id length; None when absent."""
    if snapshot.output_stamp is None:
        return None
    parent = snapshot.file.output_dir
    for length in ID_LENGTHS:
        try:
            name = job_output_name(
                snapshot.input_identity.path,
                snapshot.output_stamp,
                job_id,
                parent=parent,
                id_length=length,
            )
        except ValueError:
            break
        candidate = Path(parent) / name
        if _owner(candidate) == job_id:
            return candidate
    return None


def _digest(path: Path) -> str | None:
    try:
        if path.stat().st_size > HASH_LIMIT:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def job_artifacts(
    snapshot: JobSnapshot,
    job_id: str,
    evidence: Mapping[str, tuple[str, int]] | None,
    *,
    attempted: bool,
) -> JobArtifacts:
    """``evidence``: format -> (sha256, size) recorded at completion (store read).

    A file is ``verified`` only when the record for *its own format* has the same size
    and digest. Bytes that equal another artifact of the same job (e.g. the .md content
    saved as .srt) are ``changed``. Without a record for the format the file is
    ``unverified`` (no completion evidence, or evidence written before per-file records)
    -- never treated as verified. A known-format file the completed job did not produce
    counts as unexpected.
    """

    def view(state: str, items: list[dict[str, object]], unexpected: int = 0) -> JobArtifacts:
        return JobArtifacts.model_validate(
            {
                "schema_version": 1,
                "job_id": job_id,
                "state": state,
                "items": items,
                "unexpected_count": unexpected,
            }
        )

    if snapshot.output_stamp is None:
        return view("legacy", [])
    directory = owned_directory(snapshot, job_id)
    if directory is None:
        return view("missing" if attempted else "none", [])
    items: list[dict[str, object]] = []
    unexpected = 0
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return view("missing", [])
    for entry in entries:
        if entry.name == OUTPUT_MARKER:
            continue
        suffix = entry.suffix[1:]
        if (
            entry.is_symlink()
            or not entry.is_file()
            or entry.stem != directory.name
            or suffix not in FORMATS
        ):
            unexpected += 1
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            unexpected += 1
            continue
        expected = None if not evidence else evidence.get(suffix)
        if evidence and expected is None:
            unexpected += 1  # the completed job recorded its files; this is not one of them
            continue
        if expected is None:
            integrity = "unverified"
        elif size != expected[1]:
            integrity = "changed"
        else:
            found = _digest(entry)
            integrity = (
                "unverified" if found is None else "verified" if found == expected[0] else "changed"
            )
        items.append({"format": suffix, "size": size, "integrity": integrity})
    items.sort(key=lambda item: FORMATS.index(str(item["format"])))
    return view("available", items, unexpected)
