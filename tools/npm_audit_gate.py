"""Blocking npm audit gate for the frontend production graph, with owner-approved exceptions.

Runs ``npm audit --omit=dev --json`` in ``frontend/`` and fails (exit 1) when any high or
critical vulnerability remains. The only way a high/critical finding can pass is an entry in
``frontend/audit-exceptions.json`` that is, all at once:

* for exactly one GitHub advisory (GHSA id + npm advisory source id) on one package, with the
  vulnerable range, severity and installed versions recorded at review time -- if npm reports
  anything else (the advisory was updated, a patched release changed the range, the severity
  rose, the lockfile moved) the entry no longer matches and the finding fails again;
* approved by the repository owner (``approved_by`` = OWNER, ``approval_url`` = a comment or a
  review on this repository); an unapproved entry is never applied;
* inside its window (created <= today <= expires), the window being at most MAX_DAYS days;
* within the fixed scope ALLOWED_SCOPE (one GHSA, npm source, package and issue) and alone
  in the file (MAX_ENTRIES = 1): any other advisory needs a reviewed change of this script.

A finding that is only a consequence of other advisories (npm "via" names a package, e.g. vite
-> picomatch) passes only when every advisory it leads to is excepted. Every finding of every
severity is printed. An exception that matches nothing fails the gate (remove it). Any npm,
network or parse failure fails the gate; it is never reported as clean. Stdlib only.

Issue #62 / docs/architecture/backend/testing-and-operations.md.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

OWNER = "zawa356"
REPOSITORY = "zawa356/Utteran"
MAX_DAYS = 31
# The only exception this gate can apply (Issue #62, PR #64 owner review P2-1). Another advisory
# needs a reviewed change of this constant, not just another entry in the JSON file.
MAX_ENTRIES = 1
ALLOWED_SCOPE = {
    "advisory": "GHSA-fc5q-39xc-vjpf",
    "source": 1247710,
    "package": "picomatch",
    "issue": 62,
}
BLOCKING = ("high", "critical")
SEVERITIES = ("info", "low", "moderate", "high", "critical")
_GHSA = re.compile(r"GHSA(-[23456789cfghjmpqrvwx]{4}){3}")
_APPROVAL = re.compile(
    r"https://github\.com/" + re.escape(REPOSITORY) + r"/(issues|pull)/[1-9][0-9]*"
    r"#(issuecomment|pullrequestreview)-[1-9][0-9]*"
)
_FIELDS = {
    "advisory",
    "source",
    "package",
    "vulnerable_range",
    "severity",
    "installed_versions",
    "created",
    "expires",
    "issue",
    "reason",
    "approved_by",
    "approval_url",
}


class GateError(Exception):
    """The gate cannot decide: always a failure."""


@dataclass(frozen=True)
class Exception_:
    advisory: str
    source: int
    package: str
    vulnerable_range: str
    severity: str
    installed_versions: tuple[str, ...]
    created: date
    expires: date
    issue: int
    approved: bool


def _date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise GateError(f"exception field {field} must be YYYY-MM-DD")
    return date.fromisoformat(value)


def load_exceptions(path: Path, today: date) -> tuple[list[Exception_], list[str]]:
    """(entries that may be applied, problems that make the gate fail)."""
    if not path.is_file():
        return [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateError(f"exceptions file unreadable: {type(exc).__name__}") from None
    if not isinstance(data, dict) or set(data) != {"schema_version", "exceptions"}:
        raise GateError("exceptions file must be {schema_version, exceptions}")
    if data["schema_version"] != 1 or not isinstance(data["exceptions"], list):
        raise GateError("exceptions file: unsupported schema")
    if len(data["exceptions"]) > MAX_ENTRIES:
        raise GateError(f"exceptions file may hold at most {MAX_ENTRIES} entry")
    entries: list[Exception_] = []
    problems: list[str] = []
    seen: set[str] = set()
    for raw in data["exceptions"]:
        if not isinstance(raw, dict) or set(raw) != _FIELDS:
            raise GateError("exception entry must have exactly the documented fields")
        advisory = raw["advisory"]
        if not isinstance(advisory, str) or not _GHSA.fullmatch(advisory) or advisory in seen:
            raise GateError("exception advisory must be one unique GHSA id")
        seen.add(advisory)
        if type(raw["source"]) is not int or type(raw["issue"]) is not int:
            raise GateError(f"{advisory}: source and issue must be integers")
        if any(raw[key] != value for key, value in ALLOWED_SCOPE.items()):
            raise GateError(
                f"{advisory}: outside the allowed scope (only {ALLOWED_SCOPE['advisory']} on "
                f"{ALLOWED_SCOPE['package']}, issue #{ALLOWED_SCOPE['issue']})"
            )
        for field in ("package", "vulnerable_range", "reason"):
            if not isinstance(raw[field], str) or not raw[field].strip():
                raise GateError(f"{advisory}: {field} must be a non-empty string")
        if raw["severity"] not in BLOCKING:
            raise GateError(f"{advisory}: severity must be one of {BLOCKING}")
        versions = raw["installed_versions"]
        if (
            not isinstance(versions, list)
            or not versions
            or not all(isinstance(v, str) and v for v in versions)
        ):
            raise GateError(f"{advisory}: installed_versions must be a non-empty list")
        created = _date(raw["created"], "created")
        expires = _date(raw["expires"], "expires")
        if not created <= expires or (expires - created).days > MAX_DAYS:
            raise GateError(f"{advisory}: expires must be within {MAX_DAYS} days of created")
        approved_by, url = raw["approved_by"], raw["approval_url"]
        approved = False
        if approved_by is None and url is None:
            problems.append(f"{advisory}: NOT APPROVED by the owner (approved_by/approval_url)")
        elif approved_by != OWNER or not isinstance(url, str) or not _APPROVAL.fullmatch(url):
            raise GateError(f"{advisory}: approval must be by {OWNER} with a link on {REPOSITORY}")
        elif today < created:
            # A window that starts later cannot be used to push the 31-day limit forward.
            problems.append(
                f"{advisory}: NOT YET VALID (created {created.isoformat()} is after today)"
            )
        elif today > expires:
            problems.append(f"{advisory}: EXPIRED on {expires.isoformat()} (re-review needed)")
        else:
            approved = True
        entries.append(
            Exception_(
                advisory=advisory,
                source=raw["source"],
                package=raw["package"],
                vulnerable_range=raw["vulnerable_range"],
                severity=raw["severity"],
                installed_versions=tuple(versions),
                created=created,
                expires=expires,
                issue=raw["issue"],
                approved=approved,
            )
        )
    return entries, problems


def _installed(lock: dict[str, Any], nodes: list[str]) -> tuple[str, ...]:
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise GateError("package-lock.json has no packages map")
    versions = []
    for node in nodes:
        entry = packages.get(node)
        if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
            raise GateError("audit names a node that the lockfile does not have")
        versions.append(entry["version"])
    return tuple(sorted(set(versions)))


def evaluate(
    audit: dict[str, Any], lock: dict[str, Any], exceptions: list[Exception_], problems: list[str]
) -> tuple[bool, list[str]]:
    """(passed, report lines). Pure: no I/O."""
    if "error" in audit or not isinstance(audit.get("vulnerabilities"), dict):
        raise GateError("npm audit did not return a vulnerability report")
    vulns: dict[str, Any] = audit["vulnerabilities"]
    lines: list[str] = []
    failures: list[str] = list(problems)
    used: set[str] = set()

    def advisories_of(name: str, trail: frozenset[str]) -> list[dict[str, Any]]:
        """Every advisory object a finding leads to through 'via' package names."""
        if name in trail:
            return []  # a cycle in "via" adds nothing new
        entry = vulns.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("via"), list):
            raise GateError("npm audit 'via' names an unknown package")
        found: list[dict[str, Any]] = []
        for via in entry["via"]:
            if isinstance(via, dict):
                found.append({**via, "_package": name})
            elif isinstance(via, str):
                found.extend(advisories_of(via, trail | {name}))
            else:
                raise GateError("npm audit 'via' has an unknown shape")
        return found

    def excepted(advisory: dict[str, Any]) -> Exception_ | None:
        url = advisory.get("url")
        for item in exceptions:
            if not item.approved or url != f"https://github.com/advisories/{item.advisory}":
                continue
            entry = vulns.get(advisory["_package"], {})
            nodes = entry.get("nodes") if isinstance(entry, dict) else None
            if not isinstance(nodes, list) or not nodes:
                raise GateError("npm audit finding has no nodes")
            if (
                advisory.get("source") == item.source
                and advisory["_package"] == item.package
                and advisory.get("name") == item.package
                and advisory.get("range") == item.vulnerable_range
                and advisory.get("severity") == item.severity
                and set(_installed(lock, nodes)) <= set(item.installed_versions)
            ):
                return item
        return None

    for name in sorted(vulns):
        entry = vulns[name]
        severity = entry.get("severity") if isinstance(entry, dict) else None
        if severity not in SEVERITIES:
            raise GateError("npm audit finding has an unknown severity")
        roots = advisories_of(name, frozenset())
        ids = sorted({str(a.get("url", "?")).rsplit("/", 1)[-1] for a in roots}) or ["?"]
        if severity not in BLOCKING:
            lines.append(f"  [{severity}] {name}: {', '.join(ids)} (below the blocking level)")
            continue
        matched = [excepted(a) for a in roots]
        if roots and all(item is not None for item in matched):
            for item in matched:
                assert item is not None
                used.add(item.advisory)
            expiry = min(item.expires for item in matched if item is not None)
            lines.append(
                f"  [{severity}] {name}: {', '.join(ids)} -> EXCEPTED (owner-approved, "
                f"expires {expiry.isoformat()}, issue #{matched[0].issue})"  # type: ignore[union-attr]
            )
        else:
            lines.append(f"  [{severity}] {name}: {', '.join(ids)} -> BLOCKING")
            failures.append(f"{name}: {severity} {', '.join(ids)}")
    for item in exceptions:
        if item.approved and item.advisory not in used:
            failures.append(
                f"{item.advisory}: exception matches no current finding (stale: remove it, or "
                "the advisory/range/severity/installed version changed and needs re-review)"
            )
    counts = audit.get("metadata", {}).get("vulnerabilities", {})
    header = "npm audit --omit=dev: " + ", ".join(
        f"{level} {counts.get(level, 0)}" for level in SEVERITIES[1:]
    )
    report = [header, *lines]
    report += [f"FAIL: {f}" for f in failures]
    return not failures, report


def run_npm_audit(frontend: Path) -> dict[str, Any]:
    npm = shutil.which("npm")
    if npm is None:
        raise GateError("npm not found")
    # npm exits 1 whenever it reports vulnerabilities; the JSON decides, not the exit code.
    completed = subprocess.run(
        [npm, "audit", "--omit=dev", "--json"],
        cwd=frontend,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    try:
        data = json.loads(completed.stdout)
    except ValueError:
        raise GateError(f"npm audit output is not JSON (exit {completed.returncode})") from None
    if not isinstance(data, dict):
        raise GateError("npm audit output is not an object")
    if completed.returncode not in (0, 1):
        raise GateError(f"npm audit failed (exit {completed.returncode})")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frontend", type=Path, default=Path(__file__).parents[1] / "frontend")
    parser.add_argument("--audit-json", type=Path, help="evaluate a saved report (tests)")
    parser.add_argument("--today", help="YYYY-MM-DD (tests); default: today in UTC")
    args = parser.parse_args(argv)
    frontend: Path = args.frontend
    today = _date(args.today, "today") if args.today else datetime.now(UTC).date()
    try:
        exceptions, problems = load_exceptions(frontend / "audit-exceptions.json", today)
        if args.audit_json:
            audit = json.loads(args.audit_json.read_text(encoding="utf-8"))
        else:
            audit = run_npm_audit(frontend)
        lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
        passed, report = evaluate(audit, lock, exceptions, problems)
    except (GateError, OSError, ValueError) as exc:
        print(f"npm audit gate: FAIL (cannot decide): {exc}", file=sys.stderr)
        return 1
    print("\n".join(report))
    print("npm audit gate: " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
