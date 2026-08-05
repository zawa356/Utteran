import runpy
from pathlib import Path

SCAN = runpy.run_path(str(Path(__file__).parents[1] / "tools" / "public_history_scan.py"))
_is_placeholder = SCAN["_is_placeholder"]
_path_findings = SCAN["_path_findings"]
_scan_bytes = SCAN["_scan_bytes"]


def test_email_finding_never_contains_matched_value() -> None:
    findings = _scan_bytes(
        b"contact=private.account" + b"@" + b"corp.invalid",
        scope="test",
        object_id="abc",
        commit="def",
        path="notes.txt",
    )
    assert [(finding.category, finding.detector, finding.count) for finding in findings] == [
        ("personal-data", "email-address", 1)
    ]
    assert "private.account" not in repr(findings)


def test_secret_placeholders_are_non_blocking_but_real_shapes_are_blocking() -> None:
    placeholder = _scan_bytes(b"hf_xxxxxxxxxxxxxxxxxxxx", scope="test")
    candidate = _scan_bytes(b"hf_" + b"AbCdEf0123456789GhIj", scope="test")
    assert placeholder[0].category == "test-placeholder"
    assert placeholder[0].blocking is False
    assert candidate[0].category == "secret"
    assert candidate[0].blocking is True
    assert _is_placeholder(b"ghp_example000000000000000000000000000")


def test_sensitive_paths_are_classified_without_reading_files() -> None:
    media = _path_findings("input/private-meeting.wav", object_id="a", commit="b", scope="test")
    dotenv = _path_findings(".env.production", object_id="a", commit="b", scope="test")
    artifact = _path_findings("output/transcript.json", object_id="a", commit="b", scope="test")
    assert any(finding.category == "media" and finding.blocking for finding in media)
    assert any(finding.category == "environment" and finding.blocking for finding in dotenv)
    assert any(finding.category == "artifact-candidate" for finding in artifact)
