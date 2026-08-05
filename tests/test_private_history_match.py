from __future__ import annotations

import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def _namespace() -> dict[str, Any]:
    tools = Path(__file__).parents[1] / "tools"
    sys.path.insert(0, str(tools))
    try:
        return runpy.run_path(tools / "private_history_match.py")
    finally:
        sys.path.pop(0)


def test_pattern_file_contains_hashes_but_not_source_names(tmp_path: Path) -> None:
    namespace = _namespace()
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    private_name = "テスト組織_contact" + "@" + "corp.invalid_meeting.wav"
    (input_dir / private_name).touch()
    output = tmp_path / "patterns.json"

    counts = namespace["build_pattern_file"](input_dir, output, "LocalUser")
    serialized = output.read_text(encoding="utf-8")

    assert sum(counts.values()) >= 5
    assert private_name not in serialized
    assert "LocalUser" not in serialized
    assert "corp.invalid" not in serialized


def test_hashed_match_reports_only_category_and_location(tmp_path: Path) -> None:
    namespace = _namespace()
    pattern = namespace["_hashed"]("organization-name", "非公開組織")
    assert pattern is not None

    findings = namespace["_findings"](
        "prefix 非公開組織 suffix".encode(),
        (pattern,),
        scope="test",
        commit="abc",
        path="notes.txt",
    )

    assert len(findings) == 1
    finding = findings.pop()
    assert (finding.category, finding.count, finding.path) == (
        "organization-name",
        1,
        "notes.txt",
    )
    assert "非公開組織" not in repr(finding)


def test_pattern_loader_rejects_plaintext_fields(tmp_path: Path) -> None:
    namespace = _namespace()
    path = tmp_path / "patterns.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "patterns": [
                    {
                        "category": "windows-username",
                        "digest": "0" * 64,
                        "length": 4,
                        "rolling": "0" * 16,
                        "value": "must-not-be-accepted",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        namespace["load_patterns"](path)
    except ValueError as exc:
        assert "metadata only" in str(exc)
    else:
        raise AssertionError("plaintext-bearing pattern entry was accepted")


def test_rewrite_rules_are_generic(tmp_path: Path) -> None:
    namespace = _namespace()
    content = tmp_path / "content.txt"
    messages = tmp_path / "messages.txt"

    namespace["build_rewrite_rules"](content, messages)

    assert "<user>" in content.read_text(encoding="utf-8")
    assert "redacted-email" in messages.read_text(encoding="utf-8")
    assert "LocalUser" not in content.read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("git-filter-repo") is None, reason="git-filter-repo unavailable")
def test_rewrite_rules_work_with_filter_repo(tmp_path: Path) -> None:
    namespace = _namespace()
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True
    )
    tracked = repository / "note.txt"
    tracked.write_text("C:/Users/<user>/private\n", encoding="utf-8")
    subprocess.run(["git", "add", "note.txt"], cwd=repository, check=True)
    message = "subject\n\nContact: account" + "@" + "corp.invalid"
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repository, check=True)
    content = tmp_path / "content-rules.txt"
    messages = tmp_path / "message-rules.txt"
    namespace["build_rewrite_rules"](content, messages)

    subprocess.run(
        [
            "git",
            "filter-repo",
            "--force",
            "--replace-text",
            str(content),
            "--replace-message",
            str(messages),
        ],
        cwd=repository,
        check=True,
        capture_output=True,
    )

    expected = "C:/" + "Users/<user>/private"
    assert expected in tracked.read_text(encoding="utf-8")
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "redacted-email" in log
    assert "@" not in log
