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


def test_generalized_user_paths_are_non_blocking_placeholders() -> None:
    findings = _scan_bytes(b"C:/Users/<user>/workspace", scope="test")

    assert findings[0].category == "test-placeholder"
    assert findings[0].blocking is False


def test_typescript_source_is_not_media_but_transport_stream_still_blocks() -> None:
    typescript = b'import { defineCollection } from "astro:content";\nexport const x = 1;\n'
    packet = bytes([0x47]) + bytes(187)
    transport_stream = packet * 4

    source = _path_findings(
        "site/src/content.config.ts", object_id="", commit="c", scope="t", content=typescript
    )
    stream = _path_findings(
        "input/recording.ts", object_id="", commit="c", scope="t", content=transport_stream
    )
    unknown = _path_findings("input/recording.ts", object_id="", commit="c", scope="t")

    assert any(f.category == "source-code" for f in source)
    assert not any(f.blocking for f in source)
    assert any(f.category == "media" and f.blocking for f in stream)
    # Without content the classifier must fail closed.
    assert any(f.category == "media" and f.blocking for f in unknown)


def test_reviewed_media_requires_exact_path_and_blob() -> None:
    git_blob_id = SCAN["git_blob_id"]
    reviewed = SCAN["REVIEWED_MEDIA"]
    assert reviewed, "reviewed media manifest must load"
    path, blob = sorted(reviewed)[0]
    content = (Path(__file__).parents[1] / path).read_bytes()
    assert git_blob_id(content) == blob

    exact = _path_findings(path, object_id="", commit="c", scope="t", content=content)
    edited = _path_findings(path, object_id="", commit="c", scope="t", content=content + b"\0")
    moved = _path_findings("input/" + path, object_id="", commit="c", scope="t", content=content)

    assert any(f.category == "reviewed-fixture" for f in exact)
    assert not any(f.blocking for f in exact)
    assert any(f.category == "media" and f.blocking for f in edited)
    assert any(f.category == "media" and f.blocking for f in moved)


def test_reviewed_media_manifest_entries_are_current_and_justified() -> None:
    import json
    import subprocess

    root = Path(__file__).parents[1]
    manifest = json.loads(
        (root / "tools" / "public_scan_reviewed_media.json").read_text(encoding="utf-8")
    )
    for entry in manifest["entries"]:
        assert entry["reason"] and entry["evidence"]
        current = subprocess.run(
            ["git", "hash-object", "--", entry["path"]],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert current == entry["git_blob_sha1"], entry["path"]


def test_diff_markers_are_not_part_of_matched_values() -> None:
    diff_sections = SCAN["_diff_sections"]
    diff = (
        b"diff --git a/src/cli.py b/src/cli.py\n"
        b"--- a/src/cli.py\n"
        b"+++ b/src/cli.py\n"
        b"@@ -1,0 +1,3 @@\n"
        # Assembled so this file itself holds no address-shaped literal.
        + b"+"
        + b'@app.command("eval")\n'
        + b"+"
        + b'@router.get("/api/settings")\n'
        + b"+contact = 'private.account"
        + b"@"
        + b"corp.invalid'\n"
    )
    sections = list(diff_sections(diff))
    assert [path for path, _ in sections] == ["src/cli.py"]
    findings = _scan_bytes(sections[0][1], scope="commit-diff", path="src/cli.py")
    emails = [f for f in findings if f.detector == "email-address"]
    # Only the real address in the added line is counted, not the decorators.
    assert [(f.category, f.count) for f in emails] == [("personal-data", 1)]
