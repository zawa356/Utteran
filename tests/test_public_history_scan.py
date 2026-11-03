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
    assert _is_placeholder("github-token", b"ghp_example000000000000000000000000000")


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


# ---------------------------------------------------------------- placeholder rules (PR #34 P2)
# Every value is assembled at runtime so this file holds no path-, address- or token-shaped
# literal for the tracked-file scanner. "blocking" means a real finding; "placeholder" means
# the non-blocking test-placeholder category.

BS = "\\"


def _verdict(value: str) -> set[tuple[str, bool]]:
    findings = _scan_bytes(value.encode(), scope="test")
    return {(f.detector, f.blocking) for f in findings if f.detector != "media-path-in-content"}


def _win(root: str, account: str) -> str:
    return "C:" + BS + root + BS + account + BS + "notes"


def test_t01_windows_users_path_blocks() -> None:
    assert _verdict(_win("Users", "alice")) == {("absolute-user-path", True)}


def test_t02_userdata_with_test_and_user_in_the_account_blocks() -> None:
    assert _verdict(_win("UserData", "test" + "user")) == {("absolute-user-path", True)}
    assert _verdict(_win("Users", "user")) == {("absolute-user-path", True)}


def test_t03_unix_home_with_marker_substring_blocks() -> None:
    assert _verdict("/" + "home" + "/" + "person123/x") == {("absolute-user-path", True)}
    assert _verdict("/" + "home" + "/" + "fake" + "user/x") == {("absolute-user-path", True)}


def test_t04_only_explicit_angle_bracket_accounts_are_placeholders() -> None:
    assert _verdict(_win("Users", "<user>")) == {("absolute-user-path", False)}
    assert _verdict("/" + "home" + "/" + "<user>" + "/x") == {("absolute-user-path", False)}
    assert _verdict(_win("Users", "<名前>")) == {("absolute-user-path", False)}
    # Near misses stay blocking.
    for account in ("<user", "user>", "<>", "<us er>", "<user>x"):
        assert ("absolute-user-path", True) in _verdict(_win("Users", account)), account


def test_t05_marker_words_in_local_part_or_real_domain_block() -> None:
    for address in (
        "test" + "@" + "corp-mail.jp",
        "example.user" + "@" + "gmail-like.co",
        "alice" + "@" + "testcorp.com",
        "bob" + "@" + "example-mail.com",
    ):
        assert _verdict(address) == {("email-address", True)}, address


def test_t06_reserved_example_domains_are_the_only_email_placeholders() -> None:
    for address in (
        "anyone" + "@" + "example.com",
        "a.b" + "@" + "mail.example.org",
        "x" + "@" + "EXAMPLE.NET",
    ):
        assert _verdict(address) == {("email-address", False)}, address
    for address in (
        "anyone" + "@" + "example.co",
        "anyone" + "@" + "example.com.evil.io",
        "anyone" + "@" + "myexample.com",
        "anyone" + "@" + "corp.invalid",
    ):
        assert _verdict(address) == {("email-address", True)}, address


def test_t07_valid_shaped_tokens_with_marker_substrings_block() -> None:
    for token, detector in (
        ("hf_" + "test" + "Q7zLmN4pRs8TuVw2", "hugging-face-token"),
        ("hf_" + "Q7zLmN" + "user" + "4pRs8TuVw2", "hugging-face-token"),
        ("ghp_" + "example" + "Q7zLmN4pRs8TuVw2XyZa0123456", "github-token"),
        ("AKIA" + "TEST" + "Q7ZLMN4PRS8T", "aws-access-key"),
    ):
        assert _verdict(token) == {(detector, True)}, detector


def test_t08_only_whole_value_filler_tokens_are_placeholders() -> None:
    for token, detector in (
        ("hf_" + "x" * 20, "hugging-face-token"),
        ("ghp_" + "example" + "0" * 30, "github-token"),
        ("AKIA" + "X" * 16, "aws-access-key"),
    ):
        assert _verdict(token) == {(detector, False)}, detector
    for token, detector in (
        ("hf_" + "x" * 19 + "A", "hugging-face-token"),  # one real character: not filler
        ("ghp_" + "examples" + "0" * 30, "github-token"),
        ("ghp_" + "0" * 15 + "example" + "0" * 15, "github-token"),
    ):
        assert _verdict(token) == {(detector, True)}, detector


def test_private_keys_never_have_placeholders() -> None:
    header = "-----BEGIN " + "EXAMPLE PRIVATE KEY" + "-----"
    assert _verdict(header) == {("private-key", True)}


def test_t11_worktree_scan_blocks_a_real_windows_path(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "notes.txt").write_text(_win("Users", "alice") + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "notes.txt"], cwd=tmp_path, check=True)
    report = SCAN["scan_worktree"](tmp_path)
    assert report["summary"]["blocking_findings"] > 0
