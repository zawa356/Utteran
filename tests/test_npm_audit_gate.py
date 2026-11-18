"""Issue #62: the frontend npm audit gate and its owner-approved, single-advisory exception.

The gate must stay a blocking gate: only one recorded advisory, on one package, at the
recorded range/severity/installed version, approved by the owner and not expired, may pass.
Everything else that is high or critical fails. Synthetic audit reports in the shape of
``npm audit --json`` (npm 11); no network.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import npm_audit_gate as gate

GHSA = "GHSA-fc5q-39xc-vjpf"
APPROVAL = "https://github.com/zawa356/Utteran/issues/62#issuecomment-123456789"
TODAY = "2026-11-20"


def advisory(**overrides: Any) -> dict[str, Any]:
    return {
        "source": 1247710,
        "name": "picomatch",
        "dependency": "picomatch",
        "title": "picomatch vulnerable to ReDoS through single-branch repeated extglobs",
        "url": f"https://github.com/advisories/{GHSA}",
        "severity": "high",
        "cwe": ["CWE-1333"],
        "range": "<=4.0.7",
        **overrides,
    }


def finding(name: str, via: list[Any], severity: str = "high") -> dict[str, Any]:
    return {
        "name": name,
        "severity": severity,
        "isDirect": name == "vite",
        "via": via,
        "effects": [],
        "range": "*",
        "nodes": [f"node_modules/{name}"],
        "fixAvailable": {"name": "vite", "version": "6.2.7", "isSemVerMajor": True},
    }


def report(*extra: tuple[str, dict[str, Any]], picomatch: dict[str, Any] | None = None) -> dict:
    vulns = {
        "picomatch": finding("picomatch", [picomatch or advisory()]),
        "fdir": finding("fdir", ["picomatch"]),
        "tinyglobby": finding("tinyglobby", ["fdir", "picomatch"]),
        "vite": finding("vite", ["picomatch", "tinyglobby"]),
    }
    vulns.update(dict(extra))
    counts = {s: 0 for s in gate.SEVERITIES}
    for item in vulns.values():
        counts[item["severity"]] += 1
    counts["total"] = len(vulns)
    return {
        "auditReportVersion": 2,
        "vulnerabilities": vulns,
        "metadata": {"vulnerabilities": counts},
    }


LOCK = {
    "packages": {
        "": {},
        "node_modules/picomatch": {"version": "4.0.7"},
        "node_modules/fdir": {"version": "6.5.0"},
        "node_modules/tinyglobby": {"version": "0.2.17"},
        "node_modules/vite": {"version": "8.3.5"},
        "node_modules/other": {"version": "1.0.0"},
        "node_modules/lodash-like": {"version": "1.0.0"},
    }
}


def entry(**overrides: Any) -> dict[str, Any]:
    return {
        "advisory": GHSA,
        "source": 1247710,
        "package": "picomatch",
        "vulnerable_range": "<=4.0.7",
        "severity": "high",
        "installed_versions": ["4.0.7"],
        "created": "2026-11-18",
        "expires": "2026-12-18",
        "issue": 62,
        "reason": "synthetic",
        "approved_by": "zawa356",
        "approval_url": APPROVAL,
        **overrides,
    }


def run(
    tmp_path: Path,
    audit: dict[str, Any],
    exceptions: list[dict[str, Any]] | None,
    *,
    today: str = TODAY,
    lock: dict[str, Any] | None = None,
) -> tuple[int, str]:
    frontend = tmp_path / "frontend"
    frontend.mkdir(exist_ok=True)
    (frontend / "package-lock.json").write_text(json.dumps(lock or LOCK), encoding="utf-8")
    if exceptions is not None:
        (frontend / "audit-exceptions.json").write_text(
            json.dumps({"schema_version": 1, "exceptions": exceptions}), encoding="utf-8"
        )
    saved = tmp_path / "audit.json"
    saved.write_text(json.dumps(audit), encoding="utf-8")
    import contextlib
    import io

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gate.main(
            ["--frontend", str(frontend), "--audit-json", str(saved), "--today", today]
        )
    return code, out.getvalue() + err.getvalue()


# ----------------------------------------------------------------------------- passes


def test_a_clean_report_passes_without_any_exception(tmp_path: Path) -> None:
    clean = {"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {"vulnerabilities": {}}}
    code, text = run(tmp_path, clean, None)
    assert code == 0 and "PASS" in text


def test_the_approved_unexpired_exception_covers_exactly_this_advisory_and_its_chain(
    tmp_path: Path,
) -> None:
    code, text = run(tmp_path, report(), [entry()])
    assert code == 0, text
    assert text.count("EXCEPTED") == 4  # picomatch and the packages that lead only to it
    assert "expires 2026-12-18" in text and "issue #62" in text


def test_below_blocking_findings_are_reported_but_do_not_fail(tmp_path: Path) -> None:
    moderate = finding(
        "other",
        [
            advisory(
                url="https://github.com/advisories/GHSA-aaaa-bbbb-cccc",
                source=1,
                name="other",
                severity="moderate",
            )
        ],
        "moderate",
    )
    code, text = run(tmp_path, report(("other", moderate)), [entry()])
    assert code == 0 and "[moderate] other" in text


# ----------------------------------------------------------------------------- failures


def test_the_committed_unapproved_exception_never_applies(tmp_path: Path) -> None:
    code, text = run(tmp_path, report(), [entry(approved_by=None, approval_url=None)])
    assert code == 1
    assert "NOT APPROVED" in text and text.count("BLOCKING") == 4


@pytest.mark.parametrize(
    "approval",
    [
        {"approved_by": "someone-else"},
        {"approval_url": "https://github.com/other/repo/issues/1#issuecomment-1"},
        {"approval_url": "https://example.com/approved"},
        {"approved_by": "zawa356", "approval_url": None},
    ],
)
def test_an_approval_not_by_the_owner_on_this_repository_is_refused(
    tmp_path: Path, approval: dict[str, Any]
) -> None:
    code, text = run(tmp_path, report(), [entry(**approval)])
    assert code == 1 and "cannot decide" in text


def test_an_expired_exception_fails_and_asks_for_review(tmp_path: Path) -> None:
    code, text = run(tmp_path, report(), [entry()], today="2026-12-19")
    assert code == 1 and "EXPIRED" in text
    assert run(tmp_path, report(), [entry()], today="2026-12-18")[0] == 0  # last valid day


def test_an_exception_longer_than_the_maximum_window_is_refused(tmp_path: Path) -> None:
    code, text = run(tmp_path, report(), [entry(expires="2026-12-20")])
    assert code == 1 and "within 31 days" in text


@pytest.mark.parametrize(
    ("change", "value"),
    [
        ("range", "<4.0.9"),  # upstream fixed it: the advisory range changed
        ("severity", "critical"),  # escalated
        ("source", 999),  # another npm advisory record
        ("url", "https://github.com/advisories/GHSA-2222-3333-4444"),  # another advisory
    ],
)
def test_any_change_of_the_advisory_makes_the_finding_block_again(
    tmp_path: Path, change: str, value: Any
) -> None:
    changed = report(picomatch=advisory(**{change: value}))
    if change == "severity":
        changed["vulnerabilities"]["picomatch"]["severity"] = value
    code, text = run(tmp_path, changed, [entry()])
    assert code == 1, text
    assert "BLOCKING" in text and "stale" in text


def test_a_different_installed_version_needs_a_new_review(tmp_path: Path) -> None:
    lock = copy.deepcopy(LOCK)
    lock["packages"]["node_modules/picomatch"]["version"] = "4.0.6"
    code, text = run(tmp_path, report(), [entry()], lock=lock)
    assert code == 1 and "BLOCKING" in text


@pytest.mark.parametrize("severity", ["high", "critical"])
def test_another_high_or_critical_advisory_is_never_hidden(tmp_path: Path, severity: str) -> None:
    other = finding(
        "lodash-like",
        [
            advisory(
                url="https://github.com/advisories/GHSA-5555-6666-7777",
                source=2,
                name="lodash-like",
                severity=severity,
                range="<2",
            )
        ],
        severity,
    )
    code, text = run(tmp_path, report(("lodash-like", other)), [entry()])
    assert code == 1
    assert f"FAIL: lodash-like: {severity} GHSA-5555-6666-7777" in text
    assert text.count("EXCEPTED") == 4  # the reviewed chain is still reported as excepted


def test_a_package_reached_through_the_excepted_one_and_another_advisory_blocks(
    tmp_path: Path,
) -> None:
    other = finding(
        "other",
        [
            advisory(
                url="https://github.com/advisories/GHSA-8888-9999-cccc",
                source=3,
                name="other",
                range="<2",
            )
        ],
    )
    audit = report(("other", other))
    audit["vulnerabilities"]["vite"]["via"] = ["picomatch", "tinyglobby", "other"]
    code, text = run(tmp_path, audit, [entry()])
    assert code == 1 and "FAIL: vite: high" in text


def test_the_same_advisory_on_another_package_is_not_covered(tmp_path: Path) -> None:
    moved = finding("other", [advisory(name="other")])
    code, text = run(tmp_path, report(("other", moved)), [entry()])
    assert code == 1 and "FAIL: other: high" in text


def test_an_exception_that_matches_nothing_fails_as_stale(tmp_path: Path) -> None:
    clean = {"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {"vulnerabilities": {}}}
    code, text = run(tmp_path, clean, [entry()])
    assert code == 1 and "stale" in text


@pytest.mark.parametrize(
    "bad",
    [
        {"error": {"code": "ENOAUDIT", "summary": "registry unavailable"}},
        {"vulnerabilities": "nope"},
        {},
    ],
)
def test_an_npm_or_registry_failure_is_never_clean(tmp_path: Path, bad: dict[str, Any]) -> None:
    code, text = run(tmp_path, bad, [entry()])
    assert code == 1 and "cannot decide" in text


@pytest.mark.parametrize(
    "broken",
    [
        {**entry(), "extra": 1},
        {k: v for k, v in entry().items() if k != "expires"},
        entry(advisory="not-a-ghsa"),
        entry(severity="moderate"),
        entry(installed_versions=[]),
    ],
)
def test_a_malformed_exceptions_file_fails_closed(tmp_path: Path, broken: dict[str, Any]) -> None:
    code, text = run(tmp_path, report(), [broken])
    assert code == 1 and "cannot decide" in text


def test_the_committed_exceptions_file_is_well_formed_and_awaits_the_owner() -> None:
    path = Path(__file__).parents[1] / "frontend" / "audit-exceptions.json"
    entries, problems = gate.load_exceptions(path, gate.date(2026, 11, 18))
    assert [e.advisory for e in entries] == [GHSA]
    assert entries[0].package == "picomatch" and entries[0].vulnerable_range == "<=4.0.7"
    assert (entries[0].expires - entries[0].created).days <= gate.MAX_DAYS
    # The owner approves by filling approved_by/approval_url; until then nothing is applied.
    data = json.loads(path.read_text(encoding="utf-8"))["exceptions"][0]
    if data["approved_by"] is None:
        assert not entries[0].approved and problems
    else:
        assert data["approved_by"] == gate.OWNER and entries[0].approved
