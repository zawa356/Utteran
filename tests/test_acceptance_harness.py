from __future__ import annotations

from pathlib import Path
from runpy import run_path

_HARNESS = run_path(str(Path(__file__).parents[1] / "tools" / "acceptance" / "harness.py"))
_descendant_ids = _HARNESS["_descendant_ids"]


def test_descendant_ids_follows_multiple_generations_without_unrelated_processes() -> None:
    parent_by_pid = {
        101: 100,
        102: 101,
        103: 102,
        200: 1,
        201: 200,
    }

    assert _descendant_ids(100, parent_by_pid) == {100, 101, 102, 103}
