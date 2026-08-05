"""Scan Git history for data that must not enter a public repository.

The report intentionally contains locations, detector names, and counts only.
Matched text is never printed or written to disk.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

MEDIA_EXTENSIONS = {
    ".aac",
    ".avi",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".ts",
    ".wav",
    ".webm",
    ".wma",
}
TRANSCRIPT_EXTENSIONS = {".json", ".md", ".srt", ".txt", ".vtt"}
TRANSCRIPT_MARKERS = {
    "acceptance",
    "asr",
    "diarization",
    "job",
    "merged",
    "output",
    "result",
    "transcript",
    "受入試験",
    "文字起こし",
}
PLACEHOLDER_MARKERS = (
    "dummy",
    "example",
    "fake",
    "person",
    "placeholder",
    "test",
    "user",
    "workdir",
    "xxxx",
)


@dataclass(frozen=True, order=True)
class Finding:
    category: str
    detector: str
    scope: str
    object_id: str
    commit: str
    path: str
    count: int
    blocking: bool


@dataclass(frozen=True)
class Pattern:
    category: str
    name: str
    regex: re.Pattern[bytes]
    blocking: bool = True
    placeholders_allowed: bool = False


PATTERNS = (
    Pattern(
        "personal-data",
        "email-address",
        re.compile(
            rb"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"
        ),
        placeholders_allowed=True,
    ),
    Pattern(
        "personal-data",
        "absolute-user-path",
        re.compile(
            rb"(?i)(?:[A-Z]:[\\/](?:Users|UserData)[\\/][^\\/\s:'\"]+"
            rb"|/h[o]me/[^/\s:'\"]+)"
        ),
        placeholders_allowed=True,
    ),
    Pattern(
        "secret",
        "hugging-face-token",
        re.compile(rb"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{16,}(?![A-Za-z0-9])"),
        placeholders_allowed=True,
    ),
    Pattern(
        "secret",
        "github-token",
        re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}(?![A-Za-z0-9])"),
        placeholders_allowed=True,
    ),
    Pattern(
        "secret",
        "aws-access-key",
        re.compile(rb"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
        placeholders_allowed=True,
    ),
    Pattern(
        "secret",
        "private-key",
        re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"),
    ),
    Pattern(
        "data-reference",
        "media-path-in-content",
        re.compile(
            rb"(?i)(?<![^\s\"'<>|])[^\s\x00<>|\"']{1,260}"
            rb"\.(?:aac|avi|flac|m4a|mkv|mov|mp3|mp4|ogg|ts|wav|webm|wma)(?![A-Za-z0-9])"
        ),
        blocking=False,
    ),
)


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def _lines(raw: bytes) -> list[str]:
    return raw.decode("utf-8", errors="surrogateescape").splitlines()


def _object_inventory(root: Path) -> dict[str, tuple[str, int]]:
    output = git(
        root,
        "cat-file",
        "--batch-all-objects",
        "--batch-check=%(objectname) %(objecttype) %(objectsize)",
    )
    inventory: dict[str, tuple[str, int]] = {}
    for line in _lines(output):
        object_id, kind, size = line.split()
        inventory[object_id] = (kind, int(size))
    return inventory


def _reachable_objects(root: Path) -> set[str]:
    return {line.split(" ", 1)[0] for line in _lines(git(root, "rev-list", "--objects", "--all"))}


def _commits(root: Path) -> list[str]:
    return _lines(git(root, "rev-list", "--all"))


def _commit_blob_locations(
    root: Path, commits: Iterable[str]
) -> tuple[dict[str, set[tuple[str, str]]], set[str]]:
    locations: dict[str, set[tuple[str, str]]] = defaultdict(set)
    tree_objects: set[str] = set()
    for commit in commits:
        for line in _lines(git(root, "ls-tree", "-rt", "--full-tree", commit)):
            metadata, _, path = line.partition("\t")
            mode, kind, object_id = metadata.split()
            del mode
            if kind == "blob":
                locations[object_id].add((commit, path))
            elif kind == "tree":
                tree_objects.add(object_id)
    return locations, tree_objects


def _unreachable_locations(
    root: Path,
    inventory: dict[str, tuple[str, int]],
    reachable: set[str],
) -> dict[str, set[tuple[str, str]]]:
    locations: dict[str, set[tuple[str, str]]] = defaultdict(set)
    unreachable_commits = [
        object_id
        for object_id, (kind, _size) in inventory.items()
        if kind == "commit" and object_id not in reachable
    ]
    commit_locations, _trees = _commit_blob_locations(root, unreachable_commits)
    for object_id, entries in commit_locations.items():
        locations[object_id].update(entries)

    unreachable_trees = [
        object_id
        for object_id, (kind, _size) in inventory.items()
        if kind == "tree" and object_id not in reachable
    ]
    for tree in unreachable_trees:
        for line in _lines(git(root, "ls-tree", "-r", "--full-tree", tree)):
            metadata, _, path = line.partition("\t")
            _mode, kind, object_id = metadata.split()
            if kind == "blob":
                locations[object_id].add(("", f"<unreachable-tree:{tree}>/{path}"))
    return locations


def _read_blobs(root: Path, object_ids: Iterable[str]) -> dict[str, bytes]:
    requested = list(object_ids)
    if not requested:
        return {}
    output = git(root, "cat-file", "--batch", input_bytes=("\n".join(requested) + "\n").encode())
    blobs: dict[str, bytes] = {}
    offset = 0
    while offset < len(output):
        header_end = output.index(b"\n", offset)
        header = output[offset:header_end].decode("ascii")
        object_id, kind, raw_size = header.split()
        if kind != "blob":
            raise RuntimeError(f"expected blob from git cat-file --batch, got {kind}")
        size = int(raw_size)
        data_start = header_end + 1
        data_end = data_start + size
        blobs[object_id] = output[data_start:data_end]
        offset = data_end + 1
    return blobs


def _is_placeholder(value: bytes) -> bool:
    lowered = value.lower()
    return (
        any(marker.encode() in lowered for marker in PLACEHOLDER_MARKERS)
        or b"@example.com" in lowered
        or b"@example.net" in lowered
        or b"@example.org" in lowered
        or bool(re.search(rb"([a-z0-9])\1{7,}", lowered))
    )


def _scan_bytes(
    data: bytes,
    *,
    scope: str,
    object_id: str = "",
    commit: str = "",
    path: str = "",
) -> list[Finding]:
    findings: list[Finding] = []
    for pattern in PATTERNS:
        matches = list(pattern.regex.finditer(data))
        if not matches:
            continue
        real_count = sum(
            1
            for match in matches
            if not (pattern.placeholders_allowed and _is_placeholder(match.group(0)))
        )
        placeholder_count = len(matches) - real_count
        if real_count:
            findings.append(
                Finding(
                    pattern.category,
                    pattern.name,
                    scope,
                    object_id,
                    commit,
                    path,
                    real_count,
                    pattern.blocking,
                )
            )
        if placeholder_count:
            findings.append(
                Finding(
                    "test-placeholder",
                    pattern.name,
                    scope,
                    object_id,
                    commit,
                    path,
                    placeholder_count,
                    False,
                )
            )
    return findings


def _path_findings(path: str, *, object_id: str, commit: str, scope: str) -> list[Finding]:
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    suffix = pure.suffix.casefold()
    lowered_parts = {part.casefold() for part in pure.parts}
    findings: list[Finding] = []
    if suffix in MEDIA_EXTENSIONS:
        findings.append(
            Finding("media", "media-file-path", scope, object_id, commit, path, 1, True)
        )
    name = pure.name.casefold()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        findings.append(
            Finding("environment", "environment-file", scope, object_id, commit, path, 1, True)
        )
    transcript_candidate = suffix in {".srt", ".vtt"} or (
        suffix in TRANSCRIPT_EXTENSIONS
        and any(marker in lowered_parts or marker in name for marker in TRANSCRIPT_MARKERS)
    )
    if transcript_candidate:
        findings.append(
            Finding(
                "artifact-candidate",
                "transcript-or-result-path",
                scope,
                object_id,
                commit,
                path,
                1,
                False,
            )
        )
    findings.extend(
        _scan_bytes(
            path.encode("utf-8", errors="surrogateescape"),
            scope=f"{scope}-path",
            object_id=object_id,
            commit=commit,
            path=path,
        )
    )
    return findings


def _commit_message(root: Path, commit: str) -> bytes:
    raw = git(root, "cat-file", "commit", commit)
    _headers, separator, message = raw.partition(b"\n\n")
    return message if separator else b""


def _diff_sections(diff: bytes) -> Iterable[tuple[str, bytes]]:
    path = "<unknown>"
    buffer = bytearray()
    for line in diff.splitlines(keepends=True):
        if line.startswith(b"diff --git "):
            if buffer:
                yield path, bytes(buffer)
            path = "<unknown>"
            buffer = bytearray(line)
            continue
        if line.startswith(b"+++ b/"):
            path = line[6:].decode("utf-8", errors="surrogateescape").rstrip("\r\n")
        buffer.extend(line)
    if buffer:
        yield path, bytes(buffer)


def scan(root: Path) -> dict[str, object]:
    inventory = _object_inventory(root)
    reachable = _reachable_objects(root)
    commits = _commits(root)
    reachable_locations, _reachable_trees = _commit_blob_locations(root, commits)
    unreachable_locations = _unreachable_locations(root, inventory, reachable)
    findings: set[Finding] = set()
    blob_ids = [object_id for object_id, (kind, _size) in inventory.items() if kind == "blob"]
    blobs = _read_blobs(root, blob_ids)

    for object_id, data in blobs.items():
        locations = reachable_locations.get(object_id, set()) | unreachable_locations.get(
            object_id, set()
        )
        if not locations:
            locations = {("", "<unreachable-blob>")}
        object_scope = "reachable-blob" if object_id in reachable else "unreachable-blob"
        for commit, path in locations:
            findings.update(
                _scan_bytes(
                    data,
                    scope=object_scope,
                    object_id=object_id,
                    commit=commit,
                    path=path,
                )
            )
            findings.update(
                _path_findings(
                    path,
                    object_id=object_id,
                    commit=commit,
                    scope=object_scope,
                )
            )

    all_commits = [object_id for object_id, (kind, _size) in inventory.items() if kind == "commit"]
    for commit in all_commits:
        scope = "commit-message" if commit in reachable else "unreachable-commit-message"
        findings.update(_scan_bytes(_commit_message(root, commit), scope=scope, commit=commit))

    for commit in commits:
        diff = git(
            root, "show", "--format=", "--find-renames", "--no-ext-diff", "--unified=0", commit
        )
        for path, section in _diff_sections(diff):
            findings.update(_scan_bytes(section, scope="commit-diff", commit=commit, path=path))

    refs = _lines(git(root, "for-each-ref", "--format=%(refname)"))
    for ref in refs:
        findings.update(_scan_bytes(ref.encode(), scope="ref", path=ref))

    unreachable_counts: dict[str, int] = defaultdict(int)
    for object_id, (kind, _size) in inventory.items():
        if object_id not in reachable:
            unreachable_counts[kind] += 1

    sorted_findings = sorted(findings)
    return {
        "schema_version": 1,
        "privacy": "matched values are intentionally omitted",
        "scope": {
            "refs": len(refs),
            "commits": len(commits),
            "objects": len(inventory),
            "reachable_objects": len(reachable),
            "unreachable_objects": dict(sorted(unreachable_counts.items())),
            "diffs": len(commits),
        },
        "summary": {
            "findings": len(sorted_findings),
            "blocking_findings": sum(finding.blocking for finding in sorted_findings),
            "counts_by_category": _count_by_category(sorted_findings),
        },
        "findings": [asdict(finding) for finding in sorted_findings],
    }


def scan_worktree(root: Path) -> dict[str, object]:
    """Scan tracked files as a fast gate for newly introduced material."""
    raw_paths = git(root, "ls-files", "-z").split(b"\0")
    paths = [raw.decode("utf-8", errors="surrogateescape") for raw in raw_paths if raw]
    findings: set[Finding] = set()
    for path in paths:
        findings.update(_path_findings(path, object_id="", commit="HEAD", scope="worktree"))
        candidate = root / path
        if candidate.is_file():
            findings.update(
                _scan_bytes(candidate.read_bytes(), scope="worktree", commit="HEAD", path=path)
            )
    sorted_findings = sorted(findings)
    return {
        "schema_version": 1,
        "privacy": "matched values are intentionally omitted",
        "scope": {"tracked_files": len(paths)},
        "summary": {
            "findings": len(sorted_findings),
            "blocking_findings": sum(finding.blocking for finding in sorted_findings),
            "counts_by_category": _count_by_category(sorted_findings),
        },
        "findings": [asdict(finding) for finding in sorted_findings],
    }


def _count_by_category(findings: Iterable[Finding]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for finding in findings:
        counts[finding.category] += finding.count
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--json", type=Path, help="write a redacted JSON report")
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="scan the currently tracked files instead of every Git object",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="return 1 when a blocking finding exists (for CI)",
    )
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    report = scan_worktree(root) if args.worktree else scan(root)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    summary = report["summary"]
    scope = report["scope"]
    if args.worktree:
        print(
            "public-worktree-scan: "
            f"tracked_files={scope['tracked_files']} findings={summary['findings']} "
            f"blocking={summary['blocking_findings']}"
        )
    else:
        print(
            "public-history-scan: "
            f"refs={scope['refs']} commits={scope['commits']} objects={scope['objects']} "
            f"findings={summary['findings']} blocking={summary['blocking_findings']}"
        )
    print("matched values are intentionally omitted; use --json for redacted locations")
    return int(bool(args.fail_on_findings and summary["blocking_findings"]))


if __name__ == "__main__":
    sys.exit(main())
