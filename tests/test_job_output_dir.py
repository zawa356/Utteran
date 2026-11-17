"""Per-job output directory and same-stem artifacts (0.1.54).

``<input stem>_<YYYYMMDD-HHMMSS>_<short job id>/<same stem>.<ext>``. Data-loss guards:
no job writes into another job's directory or over the user's own files, retries do not
touch earlier results, and snapshots accepted before this change keep their meaning.
Synthetic fixtures only; inference is replaced, the route B7 -> IPC -> B3 -> core export
is the real code.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

from test_application import SyntheticASR
from test_b7_b3_model_route import B3Runner, _synthetic_core, model_files, offline, over_ipc
from test_b7_jobs import SESSION, Env, FakeRunner, dispatcher, scope
from utteran.exporters import export_all
from utteran.worker_runtime import explicit_config
from utteran_contracts.jobs import JobSnapshot
from utteran_contracts.output_naming import (
    ID_LENGTHS,
    PATH_BUDGET,
    STEM_LIMIT,
    input_stem,
    job_output_name,
    safe_stem,
)
from utteran_contracts.worker import WorkerCompleted, WorkerErrorCode, WorkerRequest
from utteran_contracts.worker_framing import ProtocolError
from utteran_gui.job_admission import AdmissionError
from utteran_gui.job_dispatcher import (
    OUTPUT_MARKER,
    PlanError,
    claim_output_dir,
    job_output,
    plan_request,
    resolve_output,
)
from utteran_gui.worker_client import WorkerClientError, verify_result

__all__ = ["offline"]  # re-exported pytest fixture
JOB = "job_0123456789abcdef0123456789abcdef"
OTHER = "job_fedcba9876543210fedcba9876543210"
STAMP = "20261116-093000"
NAME = re.compile(r"^a_[0-9]{8}-[0-9]{6}_[0-9a-f]{8}$")


# ----------------------------------------------------------------------- pure naming


@pytest.mark.parametrize(
    "path, stem",
    [
        ("/data/in/meeting.wav", "meeting"),
        (
            "C:\\Users\\<user>\\会議 録音.final.m4a",
            "会議 録音.final",
        ),
        ("relative.name.mp3", "relative.name"),
        (".hidden", ".hidden"),
        ("noext", "noext"),
    ],
)
def test_input_stem_handles_posix_and_windows_paths(path: str, stem: str) -> None:
    assert input_stem(path) == stem


@pytest.mark.parametrize(
    "raw, safe",
    [
        ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
        ("tab\there\x00", "tab_here_"),
        ("  spaced. ", "spaced"),
        ("trailing...", "trailing"),
        ("...", "input"),
        ("", "input"),
        ("CON", "CON"),
        ("か\u3099", "が"),  # NFC
    ],
)
def test_safe_stem_is_windows_safe_and_never_empty(raw: str, safe: str) -> None:
    assert safe_stem(raw) == safe


def test_name_is_stem_stamp_and_short_id_and_survives_reserved_names() -> None:
    assert job_output_name("/in/a.wav", STAMP, JOB, parent="/out") == f"a_{STAMP}_01234567"
    # A device name is harmless once the suffix is appended.
    assert job_output_name("/in/NUL.wav", STAMP, JOB, parent="/out").startswith("NUL_2026")
    # Same stem, same second, different jobs: different names (also case-insensitively).
    one = job_output_name("/in/A.wav", STAMP, JOB, parent="/out")
    two = job_output_name("/in/a.wav", STAMP, OTHER, parent="/out")
    assert one.casefold() != two.casefold()


def test_long_stems_are_truncated_inside_the_path_budget() -> None:
    long_input = "/in/" + "x" * 300 + ".wav"
    name = job_output_name(long_input, STAMP, JOB, parent="/out")
    assert name.startswith("x" * STEM_LIMIT + "_")
    parent = "/out/" + "p" * 150
    short = job_output_name(long_input, STAMP, JOB, parent=parent)
    assert len(parent) + 1 + len(short) + 1 + len(short) + len(".json") <= PATH_BUDGET
    assert short.endswith(f"_{STAMP}_01234567") and short != name
    with pytest.raises(ValueError, match="output_path_too_long"):
        job_output_name(long_input, STAMP, JOB, parent="/out/" + "p" * 220)


@pytest.mark.parametrize(
    "stamp, job", [("2026-11-16", JOB), (STAMP, "job_1"), (STAMP + "0", JOB), (STAMP, "../x")]
)
def test_invalid_stamp_or_job_id_is_refused(stamp: str, job: str) -> None:
    with pytest.raises(ValueError, match="output_name_invalid"):
        job_output_name("/in/a.wav", stamp, job, parent="/out")


# ------------------------------------------------------------------ frozen at submission


def test_submission_freezes_one_stamp_and_each_job_gets_its_own_directory(tmp_path: Path) -> None:
    env = Env(tmp_path, None)
    other = tmp_path / "elsewhere"
    other.mkdir()
    same_stem = other / "a.wav"
    same_stem.write_bytes(b"RIFF another synthetic audio")
    jobs = env.submit(env.file("a.wav"), same_stem).batch.jobs
    snapshots = [env.store.get_snapshot(j.id) for j in jobs]
    assert snapshots[0].output_stamp == snapshots[1].output_stamp
    assert snapshots[0].output_stamp == snapshots[0].created_at.astimezone().strftime(
        "%Y%m%d-%H%M%S"
    )
    dirs = [job_output(s, j.id) for s, j in zip(snapshots, jobs, strict=True)]
    assert dirs[0] != dirs[1]
    for (directory, stem), job in zip(dirs, jobs, strict=True):
        assert Path(directory).parent == env.output and Path(directory).name == stem
        assert NAME.fullmatch(stem or "") and (stem or "").endswith(job.id[4:12])
    # Deterministic: every attempt derives the same name from the frozen snapshot.
    assert job_output(snapshots[0], jobs[0].id) == dirs[0]


def test_output_path_that_cannot_fit_is_refused_at_submission(tmp_path: Path) -> None:
    env = Env(tmp_path, None)
    deep = env.output
    for _ in range(3):
        deep = deep / ("d" * 70)
    deep.mkdir(parents=True)
    session = env.session(env.file("a.wav"))
    session["files"][0]["output_dir"] = str(deep)
    token = env.resolution(session).token
    with pytest.raises(AdmissionError) as error:
        env.admission.submit(
            scope("/api/v1/jobs", env.key()), env.submit_body(session, token), session=SESSION
        )
    assert error.value.status == 422
    assert [(i.path, i.code) for i in error.value.issues] == [
        ("/files/0/output_dir", "output_path_too_long")
    ]
    assert env.store.queue().items == ()


def test_snapshot_from_before_this_change_keeps_writing_where_it_was_accepted(
    tmp_path: Path,
) -> None:
    env = Env(tmp_path, None)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    data = env.store.get_snapshot(job.id).to_wire()
    del data["output_stamp"]
    old = JobSnapshot.model_validate(data)
    assert job_output(old, job.id) == (str(env.output), None)
    request = plan_request(old, job_id=job.id, attempt_id="att_" + "1" * 32, job_dir=tmp_path / "j")
    assert request.context.base_config["general"]["output_dir"] == str(env.output)
    assert request.context.output_stem is None


# ------------------------------------------------------------------------- ownership


def test_claim_creates_marks_and_recognises_its_own_directory(tmp_path: Path) -> None:
    target = tmp_path / "a_stamp_01234567"
    claim_output_dir(target, JOB)
    assert (target / OUTPUT_MARKER).read_text(encoding="utf-8") == JOB + "\n"
    (target / "a_stamp_01234567.srt").write_text("partial", encoding="utf-8")
    claim_output_dir(target, JOB)  # the same job again (next attempt): accepted
    with pytest.raises(PlanError, match="output_unavailable"):
        claim_output_dir(target, OTHER)


def test_claim_never_adopts_foreign_content(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("user file", encoding="utf-8")
    with pytest.raises(PlanError, match="output_unavailable"):
        claim_output_dir(foreign, JOB)
    assert sorted(p.name for p in foreign.iterdir()) == ["notes.txt"]
    empty = tmp_path / "empty"  # e.g. interrupted between mkdir and marker
    empty.mkdir()
    claim_output_dir(empty, JOB)
    assert (empty / OUTPUT_MARKER).is_file()
    a_file = tmp_path / "file"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(PlanError, match="output_unavailable"):
        claim_output_dir(a_file, JOB)
    with pytest.raises(PlanError, match="output_unavailable"):
        claim_output_dir(tmp_path / "missing-parent" / "child", JOB)


@pytest.mark.skipif(os.name == "nt", reason="symlink creation needs privileges on Windows")
def test_claim_refuses_a_symlinked_directory(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(PlanError, match="output_unavailable"):
        claim_output_dir(link, JOB)
    assert list(real.iterdir()) == []


def test_dispatcher_never_writes_into_a_foreign_directory_and_uses_a_longer_id(
    tmp_path: Path,
) -> None:
    """A directory with the normal name but foreign content is left alone; the job
    runs in the next longer-id form instead of being stuck (PR #51 review)."""
    env = Env(tmp_path, None)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    snapshot = env.store.get_snapshot(job.id)
    directory, stem = job_output(snapshot, job.id)
    Path(directory).mkdir()
    (Path(directory) / "someone-else.srt").write_text("keep me", encoding="utf-8")
    runner = FakeRunner(env)
    assert dispatcher(env, runner).run_once()
    assert env.store.get_job(job.id).status == "succeeded"
    used = Path(runner.requests[0].context.base_config["general"]["output_dir"])  # type: ignore[arg-type]
    assert used.parent == env.output and used.name == f"{stem}{job.id[12:16]}"
    assert runner.requests[0].context.output_stem == used.name
    assert (used / OUTPUT_MARKER).read_text(encoding="utf-8").strip() == job.id
    assert [p.name for p in Path(directory).iterdir()] == ["someone-else.srt"]
    assert (Path(directory) / "someone-else.srt").read_text(encoding="utf-8") == "keep me"


def test_job_is_blocked_only_when_every_id_form_is_taken(tmp_path: Path) -> None:
    env = Env(tmp_path, None)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    snapshot = env.store.get_snapshot(job.id)
    for length in ID_LENGTHS:
        taken = env.output / job_output_name(
            snapshot.input_identity.path,
            snapshot.output_stamp or "",
            job.id,
            parent=str(env.output),
            id_length=length,
        )
        taken.mkdir()
        (taken / "foreign.txt").write_text("keep", encoding="utf-8")
    runner = FakeRunner(env)
    assert dispatcher(env, runner).run_once()
    blocked = env.store.get_job(job.id)
    assert blocked.status == "blocked" and blocked.block_reason == "output_unavailable"
    assert runner.requests == []
    for directory in env.output.iterdir():
        assert [p.name for p in directory.iterdir()] == ["foreign.txt"]


# ------------------------------------------------------- short job id collision (#51)

SAME_PREFIX = ("job_" + "ab12cd34" + "0" * 24, "job_" + "ab12cd34" + "f" * 24)


def _same_prefix_jobs(env: Env, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Two accepted jobs for the same input and second whose ids share 8 hex digits."""
    from utteran_control import job_repository

    ids = iter(SAME_PREFIX)
    real = job_repository._new_id
    monkeypatch.setattr(
        job_repository, "_new_id", lambda prefix: next(ids) if prefix == "job_" else real(prefix)
    )
    source = env.file("a.wav")  # written once: both jobs freeze the same input identity
    first = env.submit(source).batch.jobs[0]
    second = env.submit(source).batch.jobs[0]
    assert (first.id, second.id) == SAME_PREFIX
    snapshots = [env.store.get_snapshot(i) for i in SAME_PREFIX]
    assert snapshots[0].output_stamp == snapshots[1].output_stamp  # frozen clock
    # The reproduction: before the fix both jobs planned this one directory.
    assert job_output(snapshots[0], first.id) == job_output(snapshots[1], second.id)
    return first.id, second.id


def test_two_jobs_sharing_the_short_id_get_separate_directories(
    tmp_path: Path, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path, None)
    model_files(env.model)
    first, second = _same_prefix_jobs(env, monkeypatch)
    backend = SyntheticASR()
    _synthetic_core(monkeypatch, backend)
    runner = B3Runner(env, backend)
    worker = dispatcher(env, runner)
    assert worker.run_once()
    assert env.store.get_job(first).status == "succeeded", (
        runner.codes,
        env.store.get_job(first).block_reason,
    )
    short = Path(job_output(env.store.get_snapshot(first), first)[0])
    before = {p.name: p.read_bytes() for p in short.iterdir()}
    assert worker.run_once()
    assert env.store.get_job(second).status == "succeeded", runner.codes
    # The first job's directory and files are byte-identical; the second job wrote into
    # its own directory, named with a longer part of its id, with matching artifact names.
    assert {p.name: p.read_bytes() for p in short.iterdir()} == before
    assert (short / OUTPUT_MARKER).read_text(encoding="utf-8").strip() == first
    longer = short.with_name(short.name + second[12:16])
    assert sorted(p.name for p in env.output.iterdir()) == sorted([short.name, longer.name])
    assert (longer / OUTPUT_MARKER).read_text(encoding="utf-8").strip() == second
    produced = [p for p in longer.iterdir() if p.name != OUTPUT_MARKER]
    assert produced and {p.stem for p in produced} == {longer.name}
    assert runner.requests[1].context.output_stem == longer.name


def test_resolution_is_stable_across_attempts_and_prefers_the_owned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path, None)
    first, second = _same_prefix_jobs(env, monkeypatch)
    one, two = (env.store.get_snapshot(i) for i in (first, second))
    a = resolve_output(one, first)
    b = resolve_output(two, second)
    assert a != b and Path(b[0]).name == Path(a[0]).name + second[12:16]
    assert resolve_output(one, first) == a and resolve_output(two, second) == b
    # Even when the shorter directory disappears later, the second job keeps the
    # directory it already owns instead of starting a new one.
    (Path(a[0]) / OUTPUT_MARKER).unlink()
    Path(a[0]).rmdir()
    assert resolve_output(two, second) == b
    assert not Path(a[0]).exists()


def test_longer_id_forms_respect_the_path_budget(tmp_path: Path) -> None:
    parent = "/out/" + "p" * 150
    names = []
    for length in ID_LENGTHS:
        try:
            names.append(
                job_output_name(
                    "/in/" + "x" * 90 + ".wav", STAMP, JOB, parent=parent, id_length=length
                )
            )
        except ValueError as error:
            assert str(error) == "output_path_too_long"
            break
    assert names and len(set(names)) == len(names)
    for name in names:
        assert len(parent) + 1 + len(name) + 1 + len(name) + len(".json") <= PATH_BUDGET
    with pytest.raises(ValueError, match="output_name_invalid"):
        job_output_name("/in/a.wav", STAMP, JOB, parent="/out", id_length=10)


# --------------------------------------------------------------- real B3 + core export


def _run(env: Env, monkeypatch: pytest.MonkeyPatch, job_id: str) -> B3Runner:
    backend = SyntheticASR()
    _synthetic_core(monkeypatch, backend)
    runner = B3Runner(env, backend)
    assert dispatcher(env, runner).run_once()
    assert env.store.get_job(job_id).status == "succeeded", runner.codes
    return runner


def test_artifacts_share_the_directory_stem_and_user_files_are_untouched(
    tmp_path: Path, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def formats(settings: dict[str, Any]) -> None:
        settings["output"]["formats"] = ["srt", "vtt", "json", "txt", "md"]

    env = Env(tmp_path, formats)
    model_files(env.model)
    # A file the legacy naming would have collided with, and an older result.
    (env.output / "a.srt").write_text("the user's own subtitle", encoding="utf-8")
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    _run(env, monkeypatch, job.id)
    directory, stem = job_output(env.store.get_snapshot(job.id), job.id)
    assert sorted(p.name for p in Path(directory).iterdir()) == sorted(
        [OUTPUT_MARKER, *(f"{stem}.{ext}" for ext in ("srt", "vtt", "json", "txt", "md"))]
    )
    assert (Path(directory) / OUTPUT_MARKER).read_text(encoding="utf-8").strip() == job.id
    assert sorted(p.name for p in env.output.iterdir()) == sorted(["a.srt", stem or ""])
    assert (env.output / "a.srt").read_text(encoding="utf-8") == "the user's own subtitle"
    assert env.store.get_job(job.id).result.artifact_count == 5  # type: ignore[union-attr]


def test_retry_gets_a_new_directory_and_leaves_the_first_result_alone(
    tmp_path: Path, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path, None)
    model_files(env.model)
    first = env.submit(env.file("a.wav")).batch.jobs[0]
    backend = SyntheticASR()
    _synthetic_core(monkeypatch, backend)

    class Failing(B3Runner):
        def execute(self, request: WorkerRequest, **kwargs: Any) -> WorkerCompleted:
            Path(request.context.base_config["general"]["output_dir"]).joinpath(  # type: ignore[arg-type]
                f"{request.context.output_stem}.srt"
            ).write_text("partial", encoding="utf-8")
            raise WorkerClientError(WorkerErrorCode.CORE_FAILURE)

    assert dispatcher(env, Failing(env, backend)).run_once()
    failed = env.store.get_job(first.id)
    assert failed.status == "failed"
    first_dir, first_stem = job_output(env.store.get_snapshot(first.id), first.id)
    retry = env.admission.retry(
        first.id, failed.revision, scope(f"/api/v1/jobs/{first.id}/retry", env.key())
    )
    second_id = retry.job.id
    assert second_id != first.id
    assert dispatcher(env, B3Runner(env, backend)).run_once()
    assert env.store.get_job(second_id).status == "succeeded"
    second_dir, second_stem = job_output(env.store.get_snapshot(second_id), second_id)
    # Same frozen snapshot (same stamp), its own job id, its own directory.
    assert env.store.get_snapshot(second_id).output_stamp == (
        env.store.get_snapshot(first.id).output_stamp
    )
    assert second_dir != first_dir
    assert (Path(first_dir) / f"{first_stem}.srt").read_text(encoding="utf-8") == "partial"
    assert (Path(second_dir) / f"{second_stem}.srt").is_file()


def test_worker_refuses_a_stem_that_is_not_its_own_directory(
    tmp_path: Path, offline: list[Any]
) -> None:
    env = Env(tmp_path, None)
    model_files(env.model)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    request = over_ipc(
        plan_request(
            env.store.get_snapshot(job.id),
            job_id=job.id,
            attempt_id="att_" + "1" * 32,
            job_dir=tmp_path / "jobs",
        )
    )
    assert explicit_config(request).general.output_dir.name == request.context.output_stem
    data = request.model_dump(mode="json")
    data["context"]["base_config"]["general"]["output_dir"] = str(env.output)  # shared dir
    with pytest.raises(ProtocolError) as error:
        explicit_config(WorkerRequest.model_validate(data))
    assert error.value.code is WorkerErrorCode.INVALID_CONTEXT
    for bad in ("a/b", "..\\x", "trailing.", "a:b", ""):
        data["context"]["output_stem"] = bad
        with pytest.raises((ValueError, ProtocolError)):
            explicit_config(WorkerRequest.model_validate(data))


def test_completion_with_a_differently_named_artifact_is_rejected(
    tmp_path: Path, offline: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    env = Env(tmp_path, None)
    model_files(env.model)
    job = env.submit(env.file("a.wav")).batch.jobs[0]
    runner = _run(env, monkeypatch, job.id)
    request = runner.requests[0]
    assert request.context.output_stem is not None
    data = request.model_dump(mode="json")
    data["context"]["output_stem"] = "another_name"
    renamed = WorkerRequest.model_validate(data)
    directory, stem = job_output(env.store.get_snapshot(job.id), job.id)
    assert stem == request.context.output_stem and Path(directory).is_dir()
    from utteran import worker_runtime as runtime

    manifest = next((tmp_path / "jobs").glob("*/manifest.json"))
    done = WorkerCompleted(
        sequence=3,
        request_id=request.request_id,
        job_id=request.job_id,
        attempt_id=request.attempt_id,
        core_job_id=manifest.parent.name,
        artifacts=tuple(
            runtime.artifact(p) for p in Path(directory).iterdir() if p.name != OUTPUT_MARKER
        ),
        manifest=runtime.artifact(manifest),
    )
    verify_result(request, done)
    with pytest.raises(WorkerClientError):
        verify_result(renamed, done)


# ----------------------------------------------------------------- core export contract


def test_core_export_default_numbering_is_unchanged_and_explicit_stem_is_exact(
    tmp_path: Path,
) -> None:
    from test_exporters import sample_result
    from utteran.exporters import ExportOptions

    result = sample_result(tmp_path)
    options = ExportOptions()
    first = export_all(result, tmp_path / "out", ["srt"], options)
    second = export_all(result, tmp_path / "out", ["srt"], options)
    assert [p.name for p in (*first, *second)] == ["meeting.srt", "meeting_1.srt"]  # CLI
    own = tmp_path / "out" / "talk_stamp_id"
    named = export_all(result, own, ["srt", "json"], options, stem="talk_stamp_id")
    again = export_all(result, own, ["srt", "json"], options, stem="talk_stamp_id")
    assert (
        [p.name for p in named]
        == ["talk_stamp_id.srt", "talk_stamp_id.json"]
        == [p.name for p in again]
    )
    assert sorted(p.name for p in own.iterdir()) == ["talk_stamp_id.json", "talk_stamp_id.srt"]
