"""Fail when commits that are new relative to trusted history introduce private data.

The full-history audit (``public_history_scan.py``) reports every finding ever reachable
and therefore cannot gate: a value added in commit A and removed in commit B is gone from
the final tree but stays public in Git history. This gate scans exactly the commits that
are reachable from the candidate but not from the trusted history:

    git rev-list <candidate> --not <trusted base> <reviewed cutover>

* ``trusted base`` comes from the CI event (pull_request: the base SHA; push to ``main``:
  the pre-push SHA; push to another branch: the current ``origin/main``).
* ``reviewed cutover`` is pinned in ``public_history_baseline.json`` (a reviewed, immutable
  commit). It is never taken from the command line, the environment, or the candidate
  HEAD, so new commits after it are always scanned.

A finding is *new* when a commit introduces it relative to **every** parent: a matched
value (keyed per run with a random HMAC key, never stored) that the same path did not
contain in a parent, a path-based finding (media, ``.env``, path patterns) that the parent
version of that path did not have, or any finding in the commit message except an exact
bot ``Co-authored-by`` trailer listed in the baseline. Output carries only category,
detector, scope, short commit, a path hash and counts -- never matched values.

Exit codes: 0 no new blocking finding, 1 new blocking finding, 2 fail closed (missing or
unknown commit, shallow history, invalid baseline, empty range, unsupported event, git or
scanner error).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import re
import secrets
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

DEFAULT_BASELINE = Path(__file__).with_name("public_history_baseline.json")
ZERO_SHA = "0" * 40
_SHA = re.compile(r"[0-9a-f]{40}")
_TRAILER = re.compile(
    rb"(?i)^co-authored-by: [A-Za-z0-9 ._()\-]{1,64} <([A-Za-z0-9._+\-]+@[A-Za-z0-9.\-]+)>$"
)


class GateError(Exception):
    """Fail-closed condition with a fixed, value-free diagnostic code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _load_scanner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "public_history_scan", Path(__file__).with_name("public_history_scan.py")
    )
    if spec is None or spec.loader is None:
        raise GateError("scanner_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)  # dataclasses resolve via sys.modules
    spec.loader.exec_module(module)
    return module


SCAN = _load_scanner()


@dataclass(frozen=True, order=True)
class DeltaFinding:
    category: str
    detector: str
    scope: str
    commit: str
    path_id: str
    suffix: str
    count: int


# --------------------------------------------------------------------------- git


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode != 0:
        raise GateError("git_failed")
    return result.stdout


def _resolve_commit(root: Path, sha: str, code: str) -> str:
    if not _SHA.fullmatch(sha) or sha == ZERO_SHA:
        raise GateError(code)
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root, capture_output=True, check=False
    )
    if result.returncode != 0:
        raise GateError(code)
    return sha


def _ref_sha(root: Path, ref: str, code: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    sha = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode != 0 or not _SHA.fullmatch(sha):
        raise GateError(code)
    return sha


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class Baseline:
    cutover: str
    bot_addresses: frozenset[bytes]


def load_baseline(path: Path) -> Baseline:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            raise GateError("baseline_invalid")
        cutover = str(payload["reviewed_cutover"]["sha"])
        if not payload["reviewed_cutover"].get("reason"):
            raise GateError("baseline_invalid")
        addresses = set()
        entries = payload["trusted_bot_coauthor_addresses"]
        if not isinstance(entries, list) or len(entries) > 5:
            raise GateError("baseline_invalid")
        for entry in entries:
            local, domain = str(entry["local"]), str(entry["domain"])
            # Bot addresses only: an exact noreply mailbox, never a pattern or a person.
            if local != "noreply" or not re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", domain):
                raise GateError("baseline_invalid")
            addresses.add(f"{local}@{domain}".encode())
    except GateError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GateError("baseline_invalid") from exc
    if not _SHA.fullmatch(cutover):
        raise GateError("baseline_invalid")
    return Baseline(cutover, frozenset(addresses))


@dataclass(frozen=True)
class Range:
    event: str
    candidate: str
    trusted_base: str


def resolve_range(
    event_name: str, payload: dict[str, Any], *, main_sha: str | None, main_branch: str = "main"
) -> Range:
    """Map a GitHub event to (candidate, trusted base). Pure, so it is unit-tested."""
    if event_name == "pull_request":
        try:
            candidate = str(payload["pull_request"]["head"]["sha"])
            base = str(payload["pull_request"]["base"]["sha"])
        except (KeyError, TypeError) as exc:
            raise GateError("event_incomplete") from exc
    elif event_name == "push":
        candidate = str(payload.get("after", ""))
        if payload.get("ref") == f"refs/heads/{main_branch}":
            # Push to main: compare with the SHA main had *before* this push, never HEAD.
            base = str(payload.get("before", ""))
            if base == ZERO_SHA:
                raise GateError("push_before_missing")
        else:
            if main_sha is None:
                raise GateError("main_ref_missing")
            base = main_sha
    else:
        raise GateError("unsupported_event")
    for value in (candidate, base):
        if not _SHA.fullmatch(value) or value == ZERO_SHA:
            raise GateError("event_incomplete")
    if candidate == base:
        raise GateError("empty_range")
    return Range(event_name, candidate, base)


# --------------------------------------------------------------------------- scanning


class _Keyed:
    """Per-run HMAC so equal values compare equal without storing or printing them."""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)

    def __call__(self, detector: str, value: bytes) -> str:
        return hmac.new(self._key, detector.encode() + b"\0" + value, hashlib.sha256).hexdigest()


def _content_keys(data: bytes, keyed: _Keyed) -> Counter[tuple[str, str, str]]:
    """Blocking, non-placeholder matches keyed by (category, detector, value-hmac)."""
    keys: Counter[tuple[str, str, str]] = Counter()
    for pattern in SCAN.PATTERNS:
        if not pattern.blocking:
            continue
        for match in pattern.regex.finditer(data):
            value = match.group(0)
            if pattern.placeholders_allowed and SCAN._is_placeholder(value):
                continue
            keys[(pattern.category, pattern.name, keyed(pattern.name, value))] += 1
    return keys


def _path_keys(path: str, content: bytes | None) -> set[tuple[str, str]]:
    findings = SCAN._path_findings(path, object_id="", commit="", scope="delta", content=content)
    return {(f.category, f.detector) for f in findings if f.blocking}


def _message_body(root: Path, commit: str, bots: frozenset[bytes]) -> bytes:
    raw = _git(root, "cat-file", "commit", commit)
    _headers, separator, message = raw.partition(b"\n\n")
    if not separator:
        return b""
    paragraphs = message.rstrip(b"\n").split(b"\n\n")
    trailer = paragraphs[-1].split(b"\n")
    kept = []
    for line in trailer:
        match = _TRAILER.match(line)
        # Only an exact listed bot address in the final (trailer) paragraph is exempt.
        if match and match.group(1).lower() in bots:
            continue
        kept.append(line)
    paragraphs[-1] = b"\n".join(kept)
    return b"\n\n".join(paragraphs)


def _changes(root: Path, commit: str, parent: str | None) -> dict[str, tuple[str, str, str]]:
    """path -> (old_blob, new_blob, new_mode) for entries whose new side exists."""
    args = ["diff-tree", "-r", "-z", "--raw", "--no-renames", "--no-commit-id"]
    args += ["--root", commit] if parent is None else [parent, commit]
    fields = _git(root, *args).split(b"\0")
    changes: dict[str, tuple[str, str, str]] = {}
    index = 0
    while index + 1 < len(fields) and fields[index]:
        meta = fields[index].decode("ascii").split()
        path = fields[index + 1].decode("utf-8", errors="surrogateescape")
        index += 2
        _old_mode, new_mode, old_blob, new_blob, status = meta
        if status.startswith("D"):
            continue
        changes[path] = (old_blob, new_blob, new_mode)
    return changes


def _blob(root: Path, sha: str, cache: dict[str, bytes]) -> bytes:
    if sha not in cache:
        cache[sha] = _git(root, "cat-file", "blob", sha)
    return cache[sha]


def _path_id(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8", errors="surrogateescape")).hexdigest()[:16]


def commit_findings(
    root: Path, commit: str, baseline: Baseline, keyed: _Keyed, cache: dict[str, bytes]
) -> list[DeltaFinding]:
    short = commit[:12]
    findings: list[DeltaFinding] = []
    message = _content_keys(_message_body(root, commit, baseline.bot_addresses), keyed)
    for (category, detector, _), count in sorted(message.items()):
        findings.append(DeltaFinding(category, detector, "commit-message", short, "", "", count))

    parents = _git(root, "rev-list", "--parents", "-n", "1", commit).decode().split()[1:]
    per_parent = [_changes(root, commit, p) for p in parents] or [_changes(root, commit, None)]
    # New relative to EVERY parent: content inherited from any parent (e.g. a merged,
    # trusted main) was already scanned where it was introduced.
    paths = set.intersection(*(set(c) for c in per_parent))
    for path in sorted(paths):
        new_blob, mode = per_parent[0][path][1], per_parent[0][path][2]
        # A gitlink (submodule, mode 160000) has no blob content to read.
        content = None if mode == "160000" else _blob(root, new_blob, cache)
        old_contents = [
            _blob(root, c[path][0], cache) if c[path][0] != ZERO_SHA and mode != "160000" else None
            for c in per_parent
        ]
        new_keys = _content_keys(content, keyed) if content is not None else Counter()
        old_keys: Counter[tuple[str, str, str]] = Counter()
        old_paths: set[tuple[str, str]] = set()
        for old, change in zip(old_contents, per_parent, strict=True):
            if old is not None:
                old_keys |= _content_keys(old, keyed)
            if change[path][0] != ZERO_SHA:
                old_paths |= _path_keys(path, old)
        suffix = PurePosixPath(path).suffix.casefold()[:12]
        added = Counter({k: n for k, n in new_keys.items() if k not in old_keys})
        for (category, detector, _), count in sorted(added.items()):
            findings.append(
                DeltaFinding(category, detector, "blob", short, _path_id(path), suffix, count)
            )
        for category, detector in sorted(_path_keys(path, content) - old_paths):
            findings.append(
                DeltaFinding(category, detector, "path", short, _path_id(path), suffix, 1)
            )
    return findings


def run_gate(root: Path, rng: Range, baseline: Baseline) -> dict[str, Any]:
    if _git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise GateError("shallow_repository")
    candidate = _resolve_commit(root, rng.candidate, "candidate_missing")
    base = _resolve_commit(root, rng.trusted_base, "trusted_base_missing")
    cutover = _resolve_commit(root, baseline.cutover, "cutover_missing")
    if candidate in {base, cutover}:
        raise GateError("empty_range")
    commits = _git(root, "rev-list", "--topo-order", candidate, "--not", base, cutover)
    new_commits = commits.decode("ascii").split()
    if not new_commits:
        # Nothing new is only plausible when the candidate is already trusted; refuse
        # rather than report a vacuous "0 new findings".
        raise GateError("empty_range")
    keyed = _Keyed()
    cache: dict[str, bytes] = {}
    findings: list[DeltaFinding] = []
    for commit in new_commits:
        findings.extend(commit_findings(root, commit, baseline, keyed, cache))
    return {
        "schema_version": 1,
        "privacy": "matched values are never stored; paths are SHA-256 prefixes",
        "event": rng.event,
        "candidate": candidate,
        "trusted_base": base,
        "reviewed_cutover": cutover,
        "new_commits": len(new_commits),
        "new_blocking_findings": sum(f.count for f in findings),
        "findings": [asdict(f) for f in sorted(findings)],
    }


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--github-event", help="GITHUB_EVENT_NAME (push or pull_request)")
    parser.add_argument("--github-event-path", type=Path, help="GITHUB_EVENT_PATH JSON")
    parser.add_argument("--candidate", help="local use: candidate commit SHA")
    parser.add_argument("--trusted-base", help="local use: trusted base commit SHA")
    parser.add_argument("--main-ref", default="refs/remotes/origin/main")
    parser.add_argument("--json", type=Path, help="write the redacted report")
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    try:
        baseline = load_baseline(args.baseline)
        if args.github_event:
            if args.github_event_path is None:
                raise GateError("event_incomplete")
            try:
                payload = json.loads(args.github_event_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise GateError("event_incomplete") from exc
            main_sha = None
            if args.github_event == "push":
                main_sha = _ref_sha(root, args.main_ref, "main_ref_missing")
            rng = resolve_range(args.github_event, payload, main_sha=main_sha)
        elif args.candidate and args.trusted_base:
            rng = Range("local", args.candidate, args.trusted_base)
        else:
            raise GateError("range_missing")
        report = run_gate(root, rng, baseline)
    except GateError as exc:
        print(f"public-history-delta: FAIL-CLOSED code={exc.code}", file=sys.stderr)
        return 2
    except Exception as exc:  # any scanner failure must fail closed
        print(f"public-history-delta: FAIL-CLOSED code=scanner_error type={type(exc).__name__}")
        return 2
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "public-history-delta: "
        f"event={report['event']} candidate={report['candidate']} "
        f"trusted_base={report['trusted_base']} reviewed_cutover={report['reviewed_cutover']} "
        f"new_commits={report['new_commits']} new_blocking={report['new_blocking_findings']}"
    )
    for item in report["findings"]:
        print(
            "  new finding: "
            f"{item['category']}/{item['detector']} scope={item['scope']} "
            f"commit={item['commit']} path_id={item['path_id'] or '-'} "
            f"suffix={item['suffix'] or '-'} count={item['count']}"
        )
    print("matched values are never printed; paths are shown as SHA-256 prefixes")
    return 1 if report["new_blocking_findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
