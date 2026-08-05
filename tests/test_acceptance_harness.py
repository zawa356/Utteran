from __future__ import annotations

import json
import sys
from pathlib import Path
from runpy import run_path

import pytest

_HARNESS = run_path(str(Path(__file__).parents[1] / "tools" / "acceptance" / "harness.py"))
_descendant_ids = _HARNESS["_descendant_ids"]
_parse_gpu_memory = _HARNESS["_parse_gpu_memory"]
_placeholders = _HARNESS["_placeholders"]
_Case = _HARNESS["Case"]
load_cases = _HARNESS["load_cases"]
select_cases = _HARNESS["select_cases"]
unmet_requirements = _HARNESS["unmet_requirements"]
_profile_from_executable = _HARNESS["_profile_from_executable"]
run_selected = _HARNESS["run_selected"]


def _write_cases(path: Path, cases: list[dict[str, object]]) -> None:
    path.write_text(json.dumps(cases), encoding="utf-8")


def _passing_command(exit_code: int = 0) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.exit({exit_code})"]


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


def test_case_metadata_defaults_when_omitted_from_json(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(
        cases_path,
        [{"id": "X-1", "group": "X", "description": "d", "command": ["{utteran}", "--help"]}],
    )

    (case,) = load_cases(cases_path)

    assert case.requires == {}
    assert case.destructive is False
    assert case.estimated_seconds is None


def test_case_metadata_is_parsed_when_present(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(
        cases_path,
        [
            {
                "id": "X-1",
                "group": "X",
                "description": "d",
                "command": ["{utteran}", "--help"],
                "requires": {"cuda": True, "models": ["whisper-cpp:base"]},
                "destructive": True,
                "estimated_seconds": 12.5,
            }
        ],
    )

    (case,) = load_cases(cases_path)

    assert case.requires == {"cuda": True, "models": ["whisper-cpp:base"]}
    assert case.destructive is True
    assert case.estimated_seconds == 12.5


def _case(case_id: str, group: str, *, destructive: bool = False) -> object:
    return _Case(
        case_id=case_id,
        group=group,
        description="d",
        command=("true",),
        expected_exit_codes=(0,),
        timeout_seconds=1.0,
        environment={},
        minimum_peak_memory_bytes=None,
        measure_vram=False,
        destructive=destructive,
    )


def test_select_cases_excludes_long_and_destructive_groups_by_default() -> None:
    cases = [
        _case("G1", "G1"),
        _case("G13", "G13"),
        _case("P1-2", "P1", destructive=True),
    ]

    selected = select_cases(cases)

    assert [case.case_id for case in selected] == ["G1"]


def test_select_cases_include_long_and_destructive_flags_opt_back_in() -> None:
    cases = [_case("G1", "G1"), _case("G13", "G13"), _case("P1-2", "P1", destructive=True)]

    selected = select_cases(cases, include_long=True, include_destructive=True)

    assert {case.case_id for case in selected} == {"G1", "G13", "P1-2"}


def test_select_cases_explicit_group_overrides_long_and_destructive_exclusion() -> None:
    """An explicit --group should still surface long/destructive cases within it."""
    cases = [_case("G1", "G1"), _case("P1-2", "P1", destructive=True)]

    selected = select_cases(cases, groups={"P1"})

    assert [case.case_id for case in selected] == ["P1-2"]


def test_select_cases_rerun_narrows_to_named_ids() -> None:
    cases = [_case("G1", "G1"), _case("G2", "G1")]

    selected = select_cases(cases, rerun={"G2"})

    assert [case.case_id for case in selected] == ["G2"]


def test_unmet_requirements_is_empty_for_no_requirements() -> None:
    assert unmet_requirements({}, None) == []
    assert unmet_requirements({}, {"devices": {}, "models": {}}) == []


def test_unmet_requirements_reports_missing_environment_probe() -> None:
    assert unmet_requirements({"cuda": True}, None) == ["environment probe unavailable"]


def test_unmet_requirements_checks_profile_backend_native_model_and_devices() -> None:
    environment = {
        "devices": {
            "profile": {"current": "intel"},
            "backends": {"whisper-cpp": True, "pyannote": False},
            "native": {"variants": {"vulkan": True, "cpu": False}},
            "ctranslate2": {"cuda_device_count": 0},
            "pytorch": {"xpu_available": True},
        },
        "models": {"whisper-cpp:base": {"installed": True}},
    }

    assert unmet_requirements({"profile": "intel"}, environment) == []
    assert unmet_requirements({"profile": ["cpu", "vulkan"]}, environment) != []
    assert unmet_requirements({"backends": ["whisper-cpp"]}, environment) == []
    assert unmet_requirements({"backends": ["pyannote"]}, environment) != []
    assert unmet_requirements({"native_variants": ["vulkan"]}, environment) == []
    assert unmet_requirements({"native_variants": ["cpu"]}, environment) != []
    assert unmet_requirements({"models": ["whisper-cpp:base"]}, environment) == []
    assert unmet_requirements({"models": ["whisper-cpp:missing"]}, environment) != []
    assert unmet_requirements({"cuda": True}, environment) != []
    assert unmet_requirements({"xpu": True}, environment) == []


def test_profile_from_executable_reads_the_os_dash_profile_directory_name() -> None:
    assert _profile_from_executable(Path("/root/.venvs/win-intel/Scripts/utteran.exe")) == "intel"
    assert _profile_from_executable(Path("/root/.venvs/linux-cpu/bin/utteran")) == "cpu"
    assert _profile_from_executable(Path("/some/other/tree/utteran.exe")) is None


def test_run_selected_records_pass_fail_and_environment_skip(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(
        cases_path,
        [
            {"id": "OK", "group": "X", "description": "d", "command": _passing_command(0)},
            {"id": "BAD", "group": "X", "description": "d", "command": _passing_command(1)},
            {
                "id": "SKIPPED",
                "group": "X",
                "description": "d",
                "command": _passing_command(0),
                "requires": {"cuda": True},
            },
        ],
    )
    results_path = tmp_path / "results.jsonl"
    summary_path = tmp_path / "summary.json"

    summary = run_selected(
        cases_path,
        results_path,
        summary_path=summary_path,
        environment={"devices": {"ctranslate2": {"cuda_device_count": 0}}, "models": {}},
    )

    assert summary.total == 3
    assert summary.passed == 1
    assert summary.failed == 1
    assert summary.skipped == 1
    outcomes = {result["id"]: result["result"] for result in summary.results}
    assert outcomes == {"OK": "pass", "BAD": "fail", "SKIPPED": "skip"}
    recorded = [
        json.loads(line)["id"] for line in results_path.read_text(encoding="utf-8").splitlines()
    ]
    assert set(recorded) == {"OK", "BAD", "SKIPPED"}
    written_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert written_summary["skipped_reasons"]["SKIPPED"].startswith("environment unmet")
    assert written_summary["failed_ids"] == ["BAD"]


def test_run_selected_resume_skips_ids_already_in_results(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    _write_cases(
        cases_path,
        [{"id": "OK", "group": "X", "description": "d", "command": _passing_command(0)}],
    )
    results_path = tmp_path / "results.jsonl"
    results_path.write_text(json.dumps({"id": "OK", "result": "pass"}) + "\n", encoding="utf-8")

    summary = run_selected(cases_path, results_path, resume=True, environment={})

    assert summary.total == 0
