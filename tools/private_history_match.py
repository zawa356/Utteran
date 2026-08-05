"""Match private strings against Git without storing or reporting their values.

The pattern builder reads names in ``input/`` but never opens those files.  Its
JSON output contains only SHA-256 digests, normalized character lengths, and
categories.  Reports contain match counts and redacted Git locations only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import public_history_scan as public_scan

CATEGORIES = (
    "organization-domain",
    "email-local-part",
    "organization-name",
    "input-filename",
    "windows-username",
)
EMAIL_IN_NAME = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
JAPANESE_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u9fff々〆ヶ]{2,}")
FILENAME_SPLIT = re.compile(r"[\s_.@+()\[\]{}\u3010\u3011\uff08\uff09-]+")
ROLLING_BASE = 257
ROLLING_MASK = (1 << 64) - 1


@dataclass(frozen=True, order=True)
class HashedPattern:
    category: str
    digest: str
    length: int
    rolling: str


@dataclass(frozen=True, order=True)
class PrivateFinding:
    category: str
    scope: str
    object_id: str
    commit: str
    path: str
    count: int


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _hashed(category: str, value: str) -> HashedPattern | None:
    normalized = _normalized(value.strip())
    if not normalized:
        return None
    return HashedPattern(
        category=category,
        digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        length=len(normalized),
        rolling=f"{_rolling_hash(normalized):016x}",
    )


def _rolling_hash(value: str) -> int:
    result = 0
    for character in value:
        result = ((result * ROLLING_BASE) + ord(character) + 1) & ROLLING_MASK
    return result


def derive_private_values(input_dir: Path, windows_username: str) -> dict[str, set[str]]:
    """Derive values from file names only; file contents are never accessed."""
    values = {category: set() for category in CATEGORIES}
    for candidate in input_dir.iterdir():
        if not candidate.is_file() or candidate.name == ".gitkeep":
            continue
        name = candidate.name
        stem = candidate.stem
        values["input-filename"].update((name, stem))
        for match in EMAIL_IN_NAME.finditer(name):
            values["email-local-part"].add(match.group(1))
            values["organization-domain"].add(match.group(2))
            values["input-filename"].add(match.group(0))
        japanese_runs = JAPANESE_RUN.findall(stem)
        if japanese_runs:
            longest = max(map(len, japanese_runs))
            if longest >= 4:
                values["organization-name"].update(
                    part for part in japanese_runs if len(part) == longest
                )
        values["input-filename"].update(
            part for part in FILENAME_SPLIT.split(stem) if len(part) >= 12 and not part.isdecimal()
        )
    if windows_username:
        values["windows-username"].add(windows_username)
    return values


def build_pattern_file(input_dir: Path, destination: Path, windows_username: str) -> dict[str, int]:
    values = derive_private_values(input_dir, windows_username)
    patterns = {
        pattern
        for category, entries in values.items()
        for value in entries
        if (pattern := _hashed(category, value)) is not None
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "privacy": "SHA-256 digests only; source values are intentionally omitted",
                "patterns": [asdict(pattern) for pattern in sorted(patterns)],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        category: sum(pattern.category == category for pattern in patterns)
        for category in CATEGORIES
    }


def load_patterns(path: Path) -> tuple[HashedPattern, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or not isinstance(raw.get("patterns"), list):
        raise ValueError("unsupported private-pattern file")
    patterns: list[HashedPattern] = []
    for item in raw["patterns"]:
        if set(item) != {"category", "digest", "length", "rolling"}:
            raise ValueError("private-pattern entries may contain metadata only")
        pattern = HashedPattern(**item)
        if (
            pattern.category not in CATEGORIES
            or not re.fullmatch(r"[0-9a-f]{64}", pattern.digest)
            or not re.fullmatch(r"[0-9a-f]{16}", pattern.rolling)
            or pattern.length < 1
        ):
            raise ValueError("invalid private-pattern metadata")
        patterns.append(pattern)
    return tuple(patterns)


def _match(data: bytes, patterns: Iterable[HashedPattern]) -> dict[str, int]:
    text = _normalized(data.decode("utf-8", errors="replace"))
    by_length: dict[int, dict[int, list[HashedPattern]]] = defaultdict(lambda: defaultdict(list))
    for pattern in patterns:
        by_length[pattern.length][int(pattern.rolling, 16)].append(pattern)
    counts: dict[str, int] = defaultdict(int)
    for length, rolling_patterns in by_length.items():
        if length > len(text):
            continue
        power = pow(ROLLING_BASE, length - 1, 1 << 64)
        rolling = _rolling_hash(text[:length])
        for index in range(len(text) - length + 1):
            candidates = rolling_patterns.get(rolling, ())
            if candidates:
                digest = hashlib.sha256(text[index : index + length].encode("utf-8")).hexdigest()
                for pattern in candidates:
                    if digest == pattern.digest:
                        counts[pattern.category] += 1
            if index + length < len(text):
                outgoing = (ord(text[index]) + 1) * power
                incoming = ord(text[index + length]) + 1
                rolling = ((rolling - outgoing) * ROLLING_BASE + incoming) & ROLLING_MASK
    return dict(counts)


def _findings(
    data: bytes,
    patterns: Iterable[HashedPattern],
    *,
    scope: str,
    object_id: str = "",
    commit: str = "",
    path: str = "",
) -> set[PrivateFinding]:
    return {
        PrivateFinding(category, scope, object_id, commit, path, count)
        for category, count in _match(data, patterns).items()
    }


def scan_private_history(root: Path, patterns: tuple[HashedPattern, ...]) -> dict[str, object]:
    inventory = public_scan._object_inventory(root)
    reachable = public_scan._reachable_objects(root)
    commits = public_scan._commits(root)
    reachable_locations, _trees = public_scan._commit_blob_locations(root, commits)
    unreachable_locations = public_scan._unreachable_locations(root, inventory, reachable)
    blob_ids = [object_id for object_id, (kind, _size) in inventory.items() if kind == "blob"]
    blobs = public_scan._read_blobs(root, blob_ids)
    findings: set[PrivateFinding] = set()

    for object_id, data in blobs.items():
        locations = reachable_locations.get(object_id, set()) | unreachable_locations.get(
            object_id, set()
        )
        if not locations:
            locations = {("", "<unreachable-blob>")}
        scope = "reachable-blob" if object_id in reachable else "unreachable-blob"
        matches = _match(data, patterns)
        for commit, path in locations:
            findings.update(
                PrivateFinding(category, scope, object_id, commit, path, count)
                for category, count in matches.items()
            )
            findings.update(
                _findings(
                    path.encode("utf-8", errors="surrogateescape"),
                    patterns,
                    scope=f"{scope}-path",
                    object_id=object_id,
                    commit=commit,
                    path=path,
                )
            )

    all_commits = [object_id for object_id, (kind, _size) in inventory.items() if kind == "commit"]
    for commit in all_commits:
        scope = "commit-message" if commit in reachable else "unreachable-commit-message"
        findings.update(
            _findings(
                public_scan._commit_message(root, commit), patterns, scope=scope, commit=commit
            )
        )

    for commit in commits:
        diff = public_scan.git(
            root, "show", "--format=", "--find-renames", "--no-ext-diff", "--unified=0", commit
        )
        for path, section in public_scan._diff_sections(diff):
            findings.update(
                _findings(section, patterns, scope="commit-diff", commit=commit, path=path)
            )

    refs = public_scan._lines(public_scan.git(root, "for-each-ref", "--format=%(refname)"))
    for ref in refs:
        findings.update(_findings(ref.encode(), patterns, scope="ref", path=ref))

    tracked = [
        raw.decode("utf-8", errors="surrogateescape")
        for raw in public_scan.git(root, "ls-files", "-z").split(b"\0")
        if raw
    ]
    for path in tracked:
        findings.update(_findings(path.encode(), patterns, scope="worktree-path", path=path))
        candidate = root / path
        if candidate.is_file():
            findings.update(
                _findings(candidate.read_bytes(), patterns, scope="worktree", path=path)
            )

    sorted_findings = sorted(findings)
    category_summary = {
        category: {
            "matched": any(finding.category == category for finding in sorted_findings),
            "count": sum(
                finding.count for finding in sorted_findings if finding.category == category
            ),
            "locations": sum(finding.category == category for finding in sorted_findings),
        }
        for category in CATEGORIES
    }
    unreachable_counts: dict[str, int] = defaultdict(int)
    for object_id, (kind, _size) in inventory.items():
        if object_id not in reachable:
            unreachable_counts[kind] += 1
    return {
        "schema_version": 1,
        "privacy": "matched values and pattern digests are intentionally omitted",
        "scope": {
            "refs": len(refs),
            "commits": len(commits),
            "objects": len(inventory),
            "reachable_objects": len(reachable),
            "unreachable_objects": dict(sorted(unreachable_counts.items())),
            "diffs": len(commits),
            "tracked_files": len(tracked),
        },
        "summary": {
            "matched": bool(sorted_findings),
            "matches": sum(finding.count for finding in sorted_findings),
            "locations": len(sorted_findings),
            "categories": category_summary,
        },
        "findings": [asdict(finding) for finding in sorted_findings],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="derive and hash patterns from input file names")
    build.add_argument("--input-dir", type=Path, default=Path("input"))
    build.add_argument("--output", type=Path, required=True)
    scan = subparsers.add_parser("scan", help="scan all Git data using a hashed pattern file")
    scan.add_argument("--repo", type=Path, default=Path.cwd())
    scan.add_argument("--patterns", type=Path, required=True)
    scan.add_argument("--json", type=Path, required=True)
    scan.add_argument("--fail-on-match", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "build":
        counts = build_pattern_file(args.input_dir, args.output, os.environ.get("USERNAME", ""))
        print(
            "private-pattern-build: "
            f"patterns={sum(counts.values())} "
            f"categories={sum(bool(value) for value in counts.values())}"
        )
        print("source values are intentionally omitted")
        return 0

    patterns = load_patterns(args.patterns)
    report = scan_private_history(args.repo.resolve(), patterns)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]
    scope = report["scope"]
    print(
        "private-history-match: "
        f"refs={scope['refs']} commits={scope['commits']} objects={scope['objects']} "
        f"matched={str(summary['matched']).lower()} matches={summary['matches']} "
        f"locations={summary['locations']}"
    )
    print("matched values and pattern digests are intentionally omitted")
    return int(bool(args.fail_on_match and summary["matched"]))


if __name__ == "__main__":
    sys.exit(main())
