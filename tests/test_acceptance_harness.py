from __future__ import annotations

from pathlib import Path
from runpy import run_path

import pytest

_HARNESS = run_path(str(Path(__file__).parents[1] / "tools" / "acceptance" / "harness.py"))
_descendant_ids = _HARNESS["_descendant_ids"]
_parse_gpu_memory = _HARNESS["_parse_gpu_memory"]
_placeholders = _HARNESS["_placeholders"]


def test_descendant_ids_follows_multiple_generations_without_unrelated_processes() -> None:
    parent_by_pid = {
        101: 100,
        102: 101,
        103: 102,
        200: 1,
        201: 200,
    }

    assert _descendant_ids(100, parent_by_pid) == {100, 101, 102, 103}


def test_parse_gpu_memory_converts_mib_and_rejects_unavailable_values() -> None:
    assert _parse_gpu_memory("5120, 8192\n") == (5120 * 1024**2, 8192 * 1024**2)
    assert _parse_gpu_memory("[N/A], 8192\n") is None


def test_phase_specific_paths_can_override_harness_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "win-intel" / "utteran.exe"
    acceptance = tmp_path / "_acceptance_p3"
    monkeypatch.setenv("UTTERAN_ACCEPTANCE_UTTERAN", str(executable))
    monkeypatch.setenv("UTTERAN_ACCEPTANCE_ROOT", str(acceptance))

    placeholders = _placeholders()

    assert placeholders["utteran"] == str(executable)
    assert placeholders["acceptance"] == str(acceptance)
    assert placeholders["jobs"] == str(acceptance / "jobs")
