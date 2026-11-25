"""E-C: re-export from the verified JSON without ASR (0.1.69). Synthetic data only.

What is checked is as much "what never happens" as "what happens": the job's own files,
marker, directory, input, snapshot, queue and stores are byte-identical afterwards; nothing
is overwritten, followed through a link, re-run or deleted; refusals carry fixed codes only.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from test_b7_api import AUTHORITY, ROOT
from test_b7_jobs import SENTINEL, SESSION, Env, FakeRunner, dispatcher
from test_history_visibility import _setup
from utteran.exporters import create_exporter, export_all
from utteran.types import (
    DiarizationResult,
    ExportOptions,
    PipelineResult,
    Segment,
    TranscriptionResult,
    Word,
)
from utteran_contracts.output_naming import reexport_output_name
from utteran_contracts.transcript_export import (
    FORMATS,
    RenderOptions,
    SourceError,
    parse_json_v1,
    render_bytes,
)
from utteran_contracts.worker import WorkerArtifact, WorkerCompleted
from utteran_gui import reexport_files
from utteran_gui.api import SESSION_HEADER, create_app
from utteran_gui.job_dispatcher import OUTPUT_MARKER
from utteran_gui.job_runtime import JobRuntime
from utteran_gui.operation_queue import OperationQueue
from utteran_gui.reexport_files import FileError, PartialOutput, create_output
from utteran_gui.reexport_service import ReexportService
from utteran_gui.reexport_store import ReexportError, ReexportStore

POSIX = pytest.mark.skipif(
    not reexport_files.supported(), reason="descriptor-relative I/O is POSIX-only (E-C §4)"
)
LABEL = "話者_秘密_LABEL"


# ---- synthetic transcripts ------------------------------------------------------------------


def result(
    path: str = "/synthetic/会議 2026.wav",
    *,
    diarized: bool = True,
    segments: list[Segment] | None = None,
) -> PipelineResult:
    if segments is None:
        segments = [
            Segment(
                0,
                1.5,
                "  こんにちは ",
                [Word(0, 0.7, "こん", None), Word(0.7, 1.5, "にちは", 0.5)],
                "SPEAKER_00",
                "high",
            ),
            Segment(1.5004999, 3661.2345, "two\nlines", [], "SPEAKER_01", "low"),
            Segment(3700, 3700.5, "", [], None),
            Segment(3701, 3702, " no speaker ", [Word(3701, 3702, "x", 1.0)], None),
            Segment(3703, 3704.0005, "空ラベル", [], "", "low"),
            Segment(3705, 3706, "back", [], "SPEAKER_00"),
        ]
    transcription = TranscriptionResult(segments, "ja", 3706.25, "faster-whisper", "m-1", "cpu")
    diarization = (
        DiarizationResult([], None, 2, "pyannote", "p-1", "cpu", {"peak": 1.25, "x": [1, None]})
        if diarized
        else None
    )
    return PipelineResult(
        Path(path), transcription, diarization, segments, "2026-11-25T09:00:00+09:00"
    )


def core_bytes(result_: PipelineResult, fmt: str, options: ExportOptions, tmp: Path) -> bytes:
    target = tmp / f"core.{fmt}"
    create_exporter(fmt).write(result_, target, options)
    data = target.read_bytes()
    target.unlink()
    return data


OPTIONS = [
    ExportOptions(),
    ExportOptions(show_speaker=False),
    ExportOptions(srt_bom=True, newline="crlf"),
    ExportOptions(speaker_labels={"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}),
    ExportOptions(speaker_labels={"SPEAKER_00": "", "SPEAKER_01": "同名"}, newline="crlf"),
]


# ---- renderer parity (no GUI, no model) -----------------------------------------------------


@pytest.mark.parametrize("options", OPTIONS)
@pytest.mark.parametrize("diarized", [True, False])
def test_shared_renderer_matches_core_exporters_through_the_json(
    tmp_path: Path, options: ExportOptions, diarized: bool
) -> None:
    """Core JSON (no labels) -> read model -> each format == core exporter, byte for byte."""
    source = result(diarized=diarized)
    view, newline = parse_json_v1(core_bytes(source, "json", ExportOptions(), tmp_path))
    assert newline == "lf"
    render = RenderOptions(
        speaker_labels=options.speaker_labels,
        show_speaker=options.show_speaker,
        srt_bom=options.srt_bom,
        newline=options.newline,
    )
    for fmt in FORMATS:
        assert render_bytes(fmt, view, render) == core_bytes(source, fmt, options, tmp_path), fmt


@pytest.mark.parametrize(
    "segments",
    [[], [Segment(0, 1, "wordless", [], None)], [Segment(0, 0, " ", [], "UNKNOWN")]],
)
def test_parity_for_empty_wordless_and_speakerless_transcripts(
    tmp_path: Path, segments: list[Segment]
) -> None:
    source = result(segments=segments, diarized=False)
    for newline in ("lf", "crlf"):
        options = ExportOptions(newline=newline, srt_bom=True)
        view, found = parse_json_v1(core_bytes(source, "json", options, tmp_path))
        assert found == newline
        render = RenderOptions(newline=newline, srt_bom=True)
        for fmt in FORMATS:
            assert render_bytes(fmt, view, render) == core_bytes(source, fmt, options, tmp_path)


def test_relabeling_displayed_labels_equals_core_with_the_composed_mapping(tmp_path: Path) -> None:
    """The JSON already carries labels after the job's mapping. A new mapping applies to the
    displayed labels once (never the original mapping a second time)."""
    source = result()
    first = ExportOptions(speaker_labels={"SPEAKER_00": "Alice", "SPEAKER_01": "SPEAKER_00"})
    view, _ = parse_json_v1(core_bytes(source, "json", first, tmp_path))
    assert [s.speaker for s in view.segments][:2] == ["Alice", "SPEAKER_00"]
    again = RenderOptions(speaker_labels={"Alice": "Carol"})
    composed = ExportOptions(speaker_labels={"SPEAKER_00": "Carol", "SPEAKER_01": "SPEAKER_00"})
    for fmt in FORMATS:
        assert render_bytes(fmt, view, again) == core_bytes(source, fmt, composed, tmp_path)


def test_merged_labels_cannot_be_separated(tmp_path: Path) -> None:
    source = result()
    merged = ExportOptions(speaker_labels={"SPEAKER_00": "同名", "SPEAKER_01": "同名"})
    view, _ = parse_json_v1(core_bytes(source, "json", merged, tmp_path))
    text = render_bytes("txt", view, RenderOptions(speaker_labels={"同名": "X"})).decode()
    assert text.count("X: ") == 3  # both original speakers become X: no reverse mapping


def test_b2_golden_bytes_are_reproduced_from_the_golden_json() -> None:
    """The pre-B2 golden JSON (labels already applied, CRLF) re-rendered with no new label and
    the same BOM/CRLF options gives the other four golden files byte for byte."""
    golden = json.loads((ROOT / "tests/fixtures/b2/exports.json").read_text(encoding="utf-8"))
    files = {k.lstrip("."): bytes.fromhex(v) for k, v in golden["formats"].items()}
    view, newline = parse_json_v1(files["json"])
    assert newline == "crlf" and view.segments[0].speaker == "Synthetic"
    options = RenderOptions(srt_bom=True, newline="crlf")
    for fmt in FORMATS:
        assert render_bytes(fmt, view, options) == files[fmt], fmt


# ---- strict source reading ------------------------------------------------------------------


def _good(tmp_path: Path) -> dict[str, Any]:
    return json.loads(core_bytes(result(), "json", ExportOptions(), tmp_path))


def _encode(document: Any) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda d: d.__setitem__("schema_version", 2), "source_schema_unsupported"),
        (lambda d: d.__setitem__("schema_version", True), "source_invalid"),
        (lambda d: d.__setitem__("extra", 1), "source_invalid"),
        (lambda d: d.pop("speakers"), "source_invalid"),
        (lambda d: d["segments"][0].__setitem__("start", True), "source_invalid"),
        (lambda d: d["segments"][0].__setitem__("start", "0"), "source_invalid"),
        (lambda d: d["segments"][0].__setitem__("speaker_confidence", "mid"), "source_invalid"),
        (lambda d: d["segments"][0]["words"][0].pop("probability"), "source_invalid"),
        (lambda d: d["input"].__setitem__("duration", None), "source_invalid"),
        (lambda d: d["processing"]["diarization"].__setitem__("memory", [1]), "source_invalid"),
        (lambda d: d["speakers"].append("extra"), "source_noncanonical"),
        (lambda d: d["speakers"].reverse(), "source_noncanonical"),
    ],
)
def test_invalid_or_inconsistent_documents_are_refused(
    tmp_path: Path, mutate: Any, code: str
) -> None:
    document = _good(tmp_path)
    mutate(document)
    with pytest.raises(SourceError) as caught:
        parse_json_v1(_encode(document))
    assert caught.value.code == code


@pytest.mark.parametrize(
    "transform,code",
    [
        (lambda b: b"\xef\xbb\xbf" + b, "source_noncanonical"),
        (lambda b: b.replace(b'  "segments"', b'   "segments"'), "source_noncanonical"),
        (lambda b: b.rstrip(b"\n"), "source_noncanonical"),
        (lambda b: b.replace(b"\n", b"\r\n", 3), "source_noncanonical"),
        (lambda b: b.replace(b'"duration": 3706.25', b'"duration": NaN'), "source_invalid"),
        (lambda b: b.replace(b'"duration": 3706.25', b'"duration": Infinity'), "source_invalid"),
        (
            lambda b: b.replace(
                b'"schema_version": 1,', b'"schema_version": 1, "schema_version": 1,'
            ),
            "source_invalid",
        ),
        (lambda b: b[:-20], "source_invalid"),
        (lambda b: b"\xff" + b, "source_invalid"),
        (lambda b: b"[]", "source_invalid"),
    ],
)
def test_byte_level_tampering_is_refused(tmp_path: Path, transform: Any, code: str) -> None:
    data = core_bytes(result(), "json", ExportOptions(), tmp_path)
    parse_json_v1(data)
    with pytest.raises(SourceError) as caught:
        parse_json_v1(transform(data))
    assert caught.value.code == code


def test_structural_limits_refuse_oversized_documents(tmp_path: Path, monkeypatch: Any) -> None:
    import utteran_contracts.transcript_export as module

    data = core_bytes(result(), "json", ExportOptions(), tmp_path)
    for name, value in (("MAX_SEGMENTS", 5), ("MAX_WORDS", 2), ("MAX_SPEAKERS", 1)):
        with monkeypatch.context() as patch:
            patch.setattr(module, name, value)
            with pytest.raises(SourceError) as caught:
                parse_json_v1(data)
            assert caught.value.code == "source_too_large", name
    deep: Any = None
    for _ in range(40):
        deep = {"d": deep}
    document = _good(tmp_path)
    document["processing"]["diarization"]["memory"] = deep
    with pytest.raises(SourceError) as caught:
        parse_json_v1(_encode(document))
    assert caught.value.code in {"source_too_large", "source_invalid"}


# ---- naming ---------------------------------------------------------------------------------


def test_reexport_directory_name_rules() -> None:
    rid = "rex_" + "a" * 32
    name = reexport_output_name("C:\\x\\CON.wav", "20261125-090000", rid, parent="/out")
    assert name == "CON_reexport_20261125-090000_aaaaaaaa"
    assert reexport_output_name(
        "/x/a:b?.wav", "20261125-090000", rid, parent="/o", id_length=32
    ).endswith("_" + "a" * 32)
    with pytest.raises(ValueError, match="output_path_too_long"):
        reexport_output_name("/x/a.wav", "20261125-090000", rid, parent="/" + "p" * 200)
    with pytest.raises(ValueError, match="output_name_invalid"):
        reexport_output_name("/x/a.wav", "2026", rid, parent="/o")


# ---- the job environment --------------------------------------------------------------------


class ExportingRunner(FakeRunner):
    """Writes the *core exporters'* real files for the frozen output settings of the job."""

    def __init__(self, env: Env, source: PipelineResult | None = None) -> None:
        super().__init__(env)
        self.source = source or result()

    def execute(self, request: Any, **kwargs: Any) -> WorkerCompleted:
        super().execute(request, **kwargs)
        directory = Path(str(request.context.base_config["general"]["output_dir"]))
        output = self.env.store.get_snapshot(request.job_id).resolved.settings.output
        options = ExportOptions(
            speaker_labels=dict(output.speaker_labels),
            show_speaker=output.show_speaker,
            srt_bom=output.srt_bom,
            newline=output.newline,
        )
        paths = export_all(
            self.source, directory, output.formats, options, stem=request.context.output_stem
        )
        artifacts = tuple(
            WorkerArtifact(
                path=str(p),
                size=p.stat().st_size,
                sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            )
            for p in paths
        )
        manifest = WorkerArtifact(path=str(self.env.tmp / "manifest.json"), size=1, sha256="b" * 64)
        return WorkerCompleted(
            sequence=5,
            request_id=request.request_id,
            job_id=request.job_id,
            attempt_id=request.attempt_id,
            core_job_id="0123456789abcdef",
            artifacts=artifacts,
            manifest=manifest,
        )


def _output(**changes: Any) -> Any:
    def update(settings: dict[str, Any]) -> None:
        settings["output"].update(changes)

    return update


class World(Env):
    def __init__(self, tmp: Path, **output: Any) -> None:
        super().__init__(tmp, _output(**({"formats": ["srt", "json", "md"]} | output)))
        self.reexports = ReexportStore(tmp / "reexport" / "reexports.sqlite3")
        self.service = ReexportService(self.reexports, lambda: self.store)
        self.runs = 0

    def finished(self, name: str = "会議.wav", source: PipelineResult | None = None) -> str:
        job = self.submit(self.file(name)).batch.jobs[0]
        assert dispatcher(self, ExportingRunner(self, source)).run_once()
        assert self.store.get_job(job.id).status == "succeeded"
        return job.id

    def directory(self, job_id: str) -> Path:
        owned = [
            p
            for p in self.output.iterdir()
            if (p / OUTPUT_MARKER).is_file()
            and (p / OUTPUT_MARKER).read_text(encoding="utf-8").strip() == job_id
        ]
        assert len(owned) == 1
        return owned[0]

    def body(self, job_id: str, **changes: Any) -> dict[str, Any]:
        plan = self.service.plan(job_id)
        assert plan["available"], plan["refusal"]
        return {
            "schema_version": 1,
            "source_sha256": plan["source"]["sha256"],
            "formats": ["srt", "vtt", "json", "txt", "md"],
            "show_speaker": True,
            "srt_bom": False,
            "newline": "lf",
            "speaker_labels": {},
            "output_parent": None,
        } | changes

    def run(self, job_id: str, body: dict[str, Any], key: str | None = None) -> tuple[int, Any]:
        self.runs += 1
        revision = self.store.get_job(job_id).revision
        return self.service.execute(job_id, revision, key or f"reexport-key-{self.runs:08d}", body)


def tree(root: Path) -> dict[str, str]:
    """Every path below ``root`` -> sha256 of its bytes / kind (links not followed)."""
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            found[rel] = "link:" + os.readlink(path)
        elif path.is_dir():
            found[rel] = "dir"
        elif not path.is_file():
            found[rel] = "special"  # never opened (a FIFO would block)
        else:
            found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def db_bytes(*paths: Path) -> list[str]:
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# ---- end-to-end through the service ----------------------------------------------------------


@POSIX
def test_reexport_writes_a_new_directory_and_leaves_everything_else_byte_identical(
    world: World,
) -> None:
    job_id = world.finished()
    original = world.directory(job_id)
    before_out = tree(world.output)
    before_in = tree(world.inputs)
    stores = db_bytes(world.store.path, world.settings_path)
    jobs_before = world.count("operations")
    plan = world.service.plan(job_id)
    assert plan["available"] and plan["refusal"] is None
    assert plan["defaults"] == {
        "formats": ["srt", "json", "md"],
        "show_speaker": True,
        "srt_bom": False,
        "newline": "lf",
    }
    assert plan["source"]["speakers"] == ["SPEAKER_00", "SPEAKER_01", ""]
    assert plan["original_labels_applied"] is False
    assert not world.reexports.exists()  # the plan reads only
    body = world.body(job_id, speaker_labels={"SPEAKER_00": "Alice"}, srt_bom=True, newline="crlf")
    status, record = world.run(job_id, body)
    assert status == 201 and record["state"] == "completed" and record["error_code"] is None
    assert record["location_verified"] is True and record["partial"] is False
    target = Path(record["directory"])
    assert target.parent == world.output and target.name.startswith("会議_reexport_")
    assert sorted(p.name for p in target.iterdir()) == sorted(
        [".utteran-reexport"] + [f"{target.name}.{f}" for f in FORMATS]
    )
    assert (target / ".utteran-reexport").read_text() == record["id"] + "\n"
    expected = ExportOptions(speaker_labels={"SPEAKER_00": "Alice"}, srt_bom=True, newline="crlf")
    for item in record["files"]:
        data = (target / f"{target.name}.{item['format']}").read_bytes()
        assert data == core_bytes(result(), item["format"], expected, world.tmp)
        assert item == {
            "format": item["format"],
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    # The original job directory, marker, files, input, stores and jobs are unchanged.
    after = tree(world.output)
    assert {k: v for k, v in after.items() if not k.startswith(target.name)} == before_out
    assert tree(world.inputs) == before_in
    assert db_bytes(world.store.path, world.settings_path) == stores
    assert world.count("operations") == jobs_before
    assert original.is_dir() and (original / OUTPUT_MARKER).read_text().strip() == job_id
    listing = world.service.list_for_job(job_id)["items"]
    assert [item["id"] for item in listing] == [record["id"]]
    # The new directory is not a job directory and is never re-used as a source.
    assert not (target / OUTPUT_MARKER).exists()


@POSIX
def test_same_key_replays_the_record_and_never_writes_twice(world: World) -> None:
    job_id = world.finished()
    body = world.body(job_id)
    status, first = world.run(job_id, body, key="replay-key-000001")
    snapshot = tree(world.output)
    status2, second = world.run(job_id, body, key="replay-key-000001")
    assert (status, status2) == (201, 200) and second == first
    assert tree(world.output) == snapshot  # no second directory
    with pytest.raises(ReexportError) as caught:
        world.run(job_id, body | {"show_speaker": False}, key="replay-key-000001")
    assert (caught.value.code, caught.value.status) == ("idempotency_conflict", 409)
    # Same values, new key: an explicit new re-export (new directory), not a replay (ABA).
    status3, third = world.run(job_id, body, key="replay-key-000002")
    assert status3 == 201 and third["id"] != first["id"]
    assert third["directory"] != first["directory"]


@POSIX
def test_revision_digest_label_and_option_checks(world: World) -> None:
    job_id = world.finished()
    body = world.body(job_id)
    revision = world.store.get_job(job_id).revision
    with pytest.raises(ReexportError) as caught:
        world.service.execute(job_id, revision + 1, "revision-key-00001", body)
    assert (caught.value.code, caught.value.status) == ("revision_conflict", 412)
    cases = [
        (body | {"source_sha256": "0" * 64}, "source_changed", 409),
        (body | {"speaker_labels": {"SPEAKER_99": "x"}}, "speaker_label_unknown", 422),
        (body | {"speaker_labels": {"SPEAKER_00": "a\nb"}}, "speaker_labels_invalid", 422),
        (body | {"speaker_labels": {"SPEAKER_00": "x" * 257}}, "speaker_labels_invalid", 422),
        (body | {"formats": ["json"], "srt_bom": True}, "option_not_applicable", 422),
        (body | {"formats": []}, "formats_invalid", 422),
        (body | {"formats": ["srt", "srt"]}, "formats_invalid", 422),
        (body | {"formats": ["pdf"]}, "formats_invalid", 422),
        (body | {"newline": "cr"}, "contract_validation_failed", 422),
        (body | {"extra": 1}, "contract_validation_failed", 422),
        (
            {k: v for k, v in body.items() if k != "output_parent"},
            "contract_validation_failed",
            422,
        ),
        (body | {"output_parent": "relative/dir"}, "output_parent_invalid", 409),
        (body | {"output_parent": str(world.tmp / "absent")}, "output_parent_missing", 409),
    ]
    before = tree(world.output)
    for index, (case, code, status) in enumerate(cases):
        with pytest.raises(ReexportError) as caught:
            world.run(job_id, case, key=f"case-key-{index:08d}")
        assert (caught.value.code, caught.value.status) == (code, status), index
    assert tree(world.output) == before
    assert world.service.list_for_job(job_id)["items"] == []  # refusals write no record


@POSIX
def test_explicit_parent_and_original_parent_both_get_a_new_directory(world: World) -> None:
    job_id = world.finished()
    elsewhere = world.tmp / "elsewhere"
    elsewhere.mkdir()
    _, record = world.run(job_id, world.body(job_id, output_parent=str(elsewhere)))
    assert Path(record["directory"]).parent == elsewhere and record["options"]["explicit_parent"]
    # The job's own directory as an explicit parent: a new sub-directory, the job files stay.
    original = world.directory(job_id)
    before = tree(original)
    _, inner = world.run(job_id, world.body(job_id, output_parent=str(original)))
    assert Path(inner["directory"]).parent == original
    assert {k: v for k, v in tree(original).items() if "_reexport_" not in k} == before


# ---- refusals of the source -----------------------------------------------------------------


def _json_of(world: World, job_id: str) -> Path:
    directory = world.directory(job_id)
    return directory / f"{directory.name}.json"


@POSIX
@pytest.mark.parametrize(
    "tamper,code",
    [
        (
            lambda p: p.write_bytes(p.read_bytes().replace(b"SPEAKER_00", b"SPEAKER_X0")),
            "source_changed",
        ),
        (lambda p: p.write_bytes(p.read_bytes() + b" "), "source_changed"),
        (lambda p: p.unlink(), "source_missing"),
        (
            lambda p: (p.rename(p.with_suffix(".bak")), p.symlink_to(p.with_suffix(".bak"))),
            "source_link",
        ),
        (lambda p: os.link(p, p.with_suffix(".hard")), "source_link"),
        (lambda p: (p.unlink(), os.mkfifo(p)), "source_special"),
        (
            lambda p: (p.parent / OUTPUT_MARKER).write_text("job_" + "0" * 32 + "\n"),
            "source_missing",
        ),
    ],
)
def test_changed_missing_linked_or_foreign_sources_are_refused(
    world: World, tamper: Any, code: str
) -> None:
    job_id = world.finished()
    tamper(_json_of(world, job_id))
    before = tree(world.output)
    plan = world.service.plan(job_id)
    assert plan["available"] is False and plan["refusal"] == code and plan["source"] is None
    body = {
        "schema_version": 1,
        "source_sha256": "a" * 64,
        "formats": ["txt"],
        "show_speaker": True,
        "srt_bom": False,
        "newline": "lf",
        "speaker_labels": {},
        "output_parent": None,
    }
    with pytest.raises(ReexportError) as caught:
        world.run(job_id, body)
    assert caught.value.code == code
    assert tree(world.output) == before


@POSIX
def test_replaced_job_directory_symlink_is_not_followed(world: World) -> None:
    job_id = world.finished()
    directory = world.directory(job_id)
    moved = world.tmp / "moved"
    directory.rename(moved)
    directory.symlink_to(moved, target_is_directory=True)
    assert world.service.plan(job_id)["refusal"] == "source_missing"


def test_jobs_without_json_evidence_or_success_are_refused(tmp_path: Path) -> None:
    world = World(tmp_path, formats=["srt", "txt"])
    job_id = world.finished()
    assert world.service.plan(job_id)["refusal"] in {
        "source_json_not_recorded",
        "reexport_platform_unsupported",
    }
    failing = world.submit(world.file("f.wav")).batch.jobs[0]

    class Failing(FakeRunner):
        def execute(self, request: Any, **kwargs: Any) -> Any:
            raise RuntimeError("synthetic failure")

    assert dispatcher(world, Failing(world)).run_once()
    assert world.service.plan(failing.id)["refusal"] == "job_not_succeeded"


def test_legacy_and_unverified_jobs_and_unsupported_platform_are_refused(world: World) -> None:
    job_id = world.finished()
    snapshot = world.store.get_snapshot(job_id)

    class Store:
        def __init__(self, snap: Any, evidence: Any) -> None:
            self.snap, self.evidence = snap, evidence

        def get_job(self, _: str) -> Any:
            return world.store.get_job(job_id)

        def get_snapshot(self, _: str) -> Any:
            return self.snap

        def artifact_evidence(self, _: str) -> Any:
            return self.evidence

    class Legacy:  # a pre-0.1.54 snapshot: no output_stamp, the user's directory itself
        output_stamp = None

        def __getattr__(self, name: str) -> Any:
            return getattr(snapshot, name)

    legacy = Legacy()
    evidence = world.store.artifact_evidence(job_id)
    for snap, ev, code in (
        (legacy, evidence, "source_legacy"),
        (snapshot, None, "source_unverified"),
        (snapshot, {}, "source_unverified"),
    ):
        service = ReexportService(world.reexports, lambda s=snap, e=ev: Store(s, e))
        if reexport_files.supported():
            assert service.plan(job_id)["refusal"] == code
    blocked = ReexportService(world.reexports, lambda: world.store, supported=lambda: False)
    assert blocked.plan(job_id)["refusal"] == "reexport_platform_unsupported"


def test_windows_and_other_platforms_fail_closed() -> None:
    if os.name == "nt":
        assert reexport_files.supported() is False
        with pytest.raises(FileError) as caught:
            create_output("C:\\out", ["x"], "rex", {"txt": b""}, on_claimed=lambda _: None)
        assert caught.value.code == "reexport_platform_unsupported"
    else:
        assert reexport_files.supported() is (os.name == "posix")


# ---- output safety (race injection) ---------------------------------------------------------


@POSIX
def test_taken_names_are_never_reused_and_links_are_not_followed(tmp_path: Path) -> None:
    parent = tmp_path / "out"
    parent.mkdir()
    (parent / "a").mkdir()
    (parent / "a" / "user.txt").write_text("user")
    (parent / "b").symlink_to(tmp_path)  # a dangling-or-not link at the next name
    claimed: list[str] = []
    written = create_output(
        str(parent),
        ["a", "b", "c"],
        "rex",
        {"txt": b"t"},
        on_claimed=claimed.append,
        git=lambda *_: None,
    )
    assert written.name == "c" and claimed == ["c"]
    assert (parent / "a" / "user.txt").read_text() == "user" and not (
        parent / "a" / "a.txt"
    ).exists()
    assert not (tmp_path / "b.txt").exists()
    with pytest.raises(FileError) as caught:
        create_output(
            str(parent),
            ["a", "b", "c"],
            "rex",
            {"txt": b"t"},
            on_claimed=claimed.append,
            git=lambda *_: None,
        )
    assert caught.value.code == "output_name_unavailable"


@POSIX
def test_directory_replaced_between_mkdir_and_open_gets_no_file(
    tmp_path: Path, monkeypatch: Any
) -> None:
    parent = tmp_path / "out"
    parent.mkdir()
    decoy = tmp_path / "decoy"
    real_mkdir = os.mkdir

    def racing_mkdir(name: Any, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        real_mkdir(name, mode, dir_fd=dir_fd)
        # Another process of the same user swaps the new directory for its own (non-empty) one.
        os.rename(parent / name, decoy)
        real_mkdir(parent / name)
        (parent / name / "theirs.txt").write_text("theirs")

    monkeypatch.setattr(os, "mkdir", racing_mkdir)
    with pytest.raises(PartialOutput) as caught:
        create_output(
            str(parent),
            ["n"],
            "rex",
            {"txt": b"secret"},
            on_claimed=lambda _: None,
            git=lambda *_: None,
        )
    assert caught.value.code == "output_claim_lost" and caught.value.name == "n"
    assert sorted(p.name for p in (parent / "n").iterdir()) == ["theirs.txt"]
    assert list(decoy.iterdir()) == []


@POSIX
def test_symlink_planted_at_a_file_name_is_not_written_through(
    tmp_path: Path, monkeypatch: Any
) -> None:
    parent = tmp_path / "out"
    parent.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    real_open = os.open

    def planting_open(
        path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if path == "n.txt":
            os.symlink(victim, parent / "n" / "n.txt")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", planting_open)
    with pytest.raises(PartialOutput) as caught:
        create_output(
            str(parent),
            ["n"],
            "rex",
            {"txt": b"secret"},
            on_claimed=lambda _: None,
            git=lambda *_: None,
        )
    assert caught.value.code == "output_claim_lost"
    assert victim.read_text() == "keep"


@POSIX
def test_parent_symlink_component_and_git_checkout_are_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    with pytest.raises(FileError) as caught:
        create_output(str(tmp_path / "link"), ["n"], "rex", {"txt": b""}, on_claimed=lambda _: None)
    assert caught.value.code == "output_parent_link"
    repo = tmp_path / "repo"
    (repo / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with pytest.raises(FileError) as caught:
        create_output(str(repo / "notes"), ["n"], "rex", {"txt": b""}, on_claimed=lambda _: None)
    assert caught.value.code == "output_in_repository"
    assert list((repo / "notes").iterdir()) == []  # refused before anything was created
    (repo / ".gitignore").write_text("output/\n")
    (repo / "output").mkdir()
    written = create_output(
        str(repo / "output"), ["n"], "rex", {"txt": b"x"}, on_claimed=lambda _: None
    )
    assert written.name == "n"


@POSIX
@pytest.mark.parametrize(
    "number,code",
    [
        (errno.ENOSPC, "output_full"),
        (errno.EROFS, "output_readonly"),
        (errno.EIO, "output_io_error"),
    ],
)
def test_write_failures_are_recorded_and_partial_files_are_kept(
    world: World, monkeypatch: Any, number: int, code: str
) -> None:
    job_id = world.finished()
    body = world.body(job_id)
    before = tree(world.output)
    real_write = os.write
    calls = {"n": 0}

    def failing_write(fd: int, data: Any) -> int:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(number, "synthetic")
        return real_write(fd, data)

    monkeypatch.setattr(os, "write", failing_write)
    status, record = world.run(job_id, body)
    assert status == 201 and record["state"] == "failed" and record["error_code"] == code
    assert record["partial"] is True and record["files"] == []
    target = Path(record["directory"])
    assert target.is_dir() and len(list(target.iterdir())) >= 1  # nothing deleted
    after = tree(world.output)
    assert {k: v for k, v in after.items() if not k.startswith(target.name)} == before


@POSIX
def test_mkdir_failure_records_a_failure_without_partial_files(
    world: World, monkeypatch: Any
) -> None:
    job_id = world.finished()
    body = world.body(job_id)

    real_mkdir = os.mkdir

    def readonly(name: Any, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        if dir_fd is not None:  # the re-export directory only (not the store's own)
            raise OSError(errno.EROFS, "synthetic")
        real_mkdir(name, mode)

    monkeypatch.setattr(os, "mkdir", readonly)
    status, record = world.run(job_id, body)
    assert (
        status == 201 and record["state"] == "failed" and record["error_code"] == "output_readonly"
    )
    assert record["partial"] is False and record["directory"] is None


# ---- crash, restart, concurrency, limits ----------------------------------------------------


@POSIX
def test_a_crashed_pending_record_reads_interrupted_and_is_never_redone(world: World) -> None:
    job_id = world.finished()
    body = world.body(job_id)
    revision = world.store.get_job(job_id).revision
    key = "crash-key-00000001"

    class Crash(BaseException):
        pass

    def crash(name: str) -> None:
        world.reexports.claim(record_id[0], name)
        raise Crash  # killed after the directory exists, before any file

    record_id: list[str] = []
    real_begin = world.reexports.begin

    def begin(**kwargs: Any) -> Any:
        record, created = real_begin(**kwargs)
        record_id.append(record.id)
        return record, created

    world.reexports.begin = begin  # type: ignore[method-assign]
    import utteran_gui.reexport_service as service_module

    real_create = service_module.reexport_files.create_output

    def create(parent: str, names: Any, marker: str, files: Any, *, on_claimed: Any) -> Any:
        return real_create(parent, names, marker, files, on_claimed=crash)

    service_module.reexport_files.create_output = create  # type: ignore[assignment]
    try:
        with pytest.raises(Crash):
            world.service.execute(job_id, revision, key, body)
    finally:
        service_module.reexport_files.create_output = real_create  # type: ignore[assignment]
    # "Restart": a new process opens the same store.
    restarted = ReexportService(ReexportStore(world.reexports.path), lambda: world.store)
    snapshot = tree(world.output)
    status, again = restarted.execute(job_id, revision, key, body)
    assert status == 200 and again["state"] == "interrupted" and again["partial"] is True
    assert again["directory"] is not None and Path(again["directory"]).is_dir()
    assert tree(world.output) == snapshot  # the same request is never executed again
    status, fresh = restarted.execute(job_id, revision, "crash-key-00000002", body)
    assert status == 201 and fresh["state"] == "completed"


@POSIX
def test_one_reexport_at_a_time_and_concurrent_same_key(world: World) -> None:
    job_id = world.finished()
    body = world.body(job_id)
    world.service._lock.acquire()
    try:
        with pytest.raises(ReexportError) as caught:
            world.run(job_id, body)
        assert (caught.value.code, caught.value.status, caught.value.retryable) == (
            "reexport_busy",
            503,
            True,
        )
    finally:
        world.service._lock.release()
    results: list[Any] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        try:
            results.append(
                world.service.execute(
                    job_id, world.store.get_job(job_id).revision, "same-key-00000001", body
                )
            )
        except ReexportError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    created = [r for r in results if isinstance(r, tuple) and r[0] == 201]
    assert len(created) == 1
    assert all(
        r == "reexport_busy" or (isinstance(r, tuple) and r[1]["id"] == created[0][1]["id"])
        for r in results
    )
    directories = [p for p in world.output.iterdir() if "_reexport_" in p.name]
    assert len(directories) == 1


@POSIX
def test_record_limits_refuse_without_pruning(world: World, monkeypatch: Any) -> None:
    import utteran_gui.reexport_store as store_module

    job_id = world.finished()
    body = world.body(job_id, formats=["txt"])
    monkeypatch.setattr(store_module, "MAX_RECORDS_PER_JOB", 2)
    world.run(job_id, body)
    world.run(job_id, body)
    before = (tree(world.output), world.service.list_for_job(job_id))
    with pytest.raises(ReexportError) as caught:
        world.run(job_id, body)
    assert (caught.value.code, caught.value.status) == ("reexport_limit", 409)
    assert (tree(world.output), world.service.list_for_job(job_id)) == before
    monkeypatch.setattr(store_module, "MAX_RECORDS_PER_JOB", 100)
    monkeypatch.setattr(store_module, "MAX_STORE_REQUEST_BYTES", 10)
    with pytest.raises(ReexportError) as caught:
        world.run(job_id, body)
    assert (caught.value.code, caught.value.status) == ("store_quota_exceeded", 413)


@POSIX
def test_output_size_limit_is_checked_before_anything_exists(
    world: World, monkeypatch: Any
) -> None:
    job_id = world.finished()
    monkeypatch.setattr(reexport_files, "MAX_OUTPUT_BYTES", 100)
    before = tree(world.output)
    with pytest.raises(ReexportError) as caught:
        world.run(job_id, world.body(job_id))
    assert (caught.value.code, caught.value.status) == ("output_too_large", 413)
    assert tree(world.output) == before and not world.reexports.exists()


@POSIX
def test_source_size_limit(world: World, monkeypatch: Any) -> None:
    job_id = world.finished()
    monkeypatch.setattr(reexport_files, "MAX_SOURCE_BYTES", 100)
    assert world.service.plan(job_id)["refusal"] == "source_too_large"


@POSIX
def test_store_physical_size_stays_bounded(world: World) -> None:
    job_id = world.finished()
    body = world.body(job_id, formats=["txt"], speaker_labels={"SPEAKER_00": "長" * 256})
    for _ in range(60):
        world.run(job_id, body)
    physical = world.reexports.physical()
    size = physical["pages"] * physical["page_size"]
    assert size <= 256 * 1024, physical  # ~2 KiB per record incl. a 256-character label
    assert physical["free_pages"] == 0


def test_store_refuses_unknown_schema_and_never_reinitializes(tmp_path: Path) -> None:
    path = tmp_path / "r" / "reexports.sqlite3"
    store = ReexportStore(path)
    store.create_if_missing()
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=9")
    before = path.read_bytes()
    with pytest.raises(ReexportError) as caught:
        ReexportStore(path).for_job("job_" + "0" * 32)
    assert caught.value.code == "store_schema_unsupported"
    assert path.read_bytes() == before
    assert ReexportStore(tmp_path / "absent" / "x.sqlite3").for_job("job_" + "0" * 32) == []
    assert not (tmp_path / "absent").exists()


# ---- HTTP boundary, privacy, isolation ------------------------------------------------------


def _app(world: World) -> Any:
    queue = OperationQueue()
    runtime = JobRuntime(
        store_path=world.store.path,
        settings_path=world.settings_path,
        legacy_queue=queue,
        provider=world.provider,
        runner=ExportingRunner(world),
        job_dir=world.tmp / "jobs",
        autostart_dispatcher=False,
        setup=_setup(world.tmp),
    )
    return create_app(
        SESSION,
        repo_root=ROOT,
        operation_queue=queue,
        enable_v1_settings=True,
        v1_authority=AUTHORITY,
        v1_store_path=world.settings_path,
        v1_reexport_path=world.reexports.path,
        enable_v1_jobs=True,
        v1_jobs_runtime=runtime,
    )


@POSIX
def test_http_roundtrip_headers_boundary_and_value_free_errors(world: World, caplog: Any) -> None:
    job_id = world.finished("秘密の会議.wav")
    origin = {"Origin": "http://" + AUTHORITY}
    with TestClient(
        _app(world), base_url="http://" + AUTHORITY, headers={SESSION_HEADER: SESSION}
    ) as client:
        plan = client.get(f"/api/v1/jobs/{job_id}/reexport-source")
        assert plan.status_code == 200 and plan.headers["cache-control"] == "no-store"
        body = world.body(job_id, speaker_labels={"SPEAKER_00": LABEL})
        path = f"/api/v1/jobs/{job_id}/reexports"
        assert (
            client.post(
                path, json=body, headers=origin | {"Idempotency-Key": "http-key-00000001"}
            ).status_code
            == 428
        )
        bad = client.post(
            path, json=body, headers=origin | {"Idempotency-Key": "short", "If-Match": '"1"'}
        )
        assert bad.status_code == 400 and bad.json()["error"]["code"] == "idempotency_key_invalid"
        revision = world.store.get_job(job_id).revision
        headers = origin | {"Idempotency-Key": "http-key-00000001", "If-Match": f'"{revision}"'}
        created = client.post(path, json=body, headers=headers)
        assert created.status_code == 201, created.text
        record = created.json()
        assert client.post(path, json=body, headers=headers).json() == record
        assert client.get(path).json()["items"][0]["id"] == record["id"]
        assert client.get(f"/api/v1/reexports/{record['id']}").json() == record
        assert client.get("/api/v1/reexports/rex_" + "0" * 32).status_code == 404
        assert client.get("/api/v1/reexports/../x").status_code == 404
        errors = [
            client.post(
                path,
                json=body | {"speaker_labels": {LABEL: "x"}},
                headers=origin
                | {"Idempotency-Key": "http-key-00000002", "If-Match": f'"{revision}"'},
            ),
            client.post(
                path,
                json=body | {"output_parent": str(world.tmp / SENTINEL)},
                headers=origin
                | {"Idempotency-Key": "http-key-00000003", "If-Match": f'"{revision}"'},
            ),
            client.post(
                path,
                content=b"{",
                headers=origin
                | {
                    "Idempotency-Key": "http-key-00000004",
                    "If-Match": f'"{revision}"',
                    "Content-Type": "application/json",
                },
            ),
            client.get("/api/v1/jobs/job_" + "f" * 32 + "/reexport-source"),
        ]
        for response in errors:
            assert response.status_code >= 400
            text = response.text
            assert LABEL not in text and SENTINEL not in text and str(world.tmp) not in text
            assert response.json()["error"]["message_key"].startswith("reexport.")
        # Session and Host boundary of the shared v1 sub-app.
        assert (
            client.get(
                f"/api/v1/jobs/{job_id}/reexport-source", headers={SESSION_HEADER: "wrong"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                path,
                json=body,
                headers={
                    "Idempotency-Key": "http-key-00000005",
                    "If-Match": f'"{revision}"',
                    "Origin": "http://evil.example",
                },
            ).status_code
            == 403
        )
    for message in caplog.messages:
        assert LABEL not in message and SENTINEL not in message


def test_reexport_modules_do_not_log_or_import_core() -> None:
    for name in ("reexport_api", "reexport_files", "reexport_service", "reexport_store"):
        source = (ROOT / f"src/utteran_gui/{name}.py").read_text(encoding="utf-8")
        assert "import logging" not in source and "from utteran." not in source
        assert "import utteran\n" not in source
    code = (
        "import sys; import utteran_gui.reexport_api, utteran_gui.reexport_service;"
        "heavy={'utteran','torch','faster_whisper','pyannote'};"
        "bad=[m for m in sys.modules if m.split('.')[0] in heavy];"
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_default_gui_has_no_reexport_routes_or_imports() -> None:
    app = create_app(SESSION, repo_root=ROOT)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any("reexport" in path for path in paths)
    code = (
        "import sys; from pathlib import Path; from utteran_gui.api import create_app;"
        "create_app('s'*32, repo_root=Path('.'));"
        "print([m for m in sys.modules if 'reexport' in m])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.strip() == "[]"
