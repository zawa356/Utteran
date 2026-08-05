from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any


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
    private_name = "テスト組織_contact@corp.invalid_meeting.wav"
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
