"""E-C2b (#87): the Rust-core file layer (`reexport_files_native`) against the E-C1 contract.

Runs wherever the internal `utteran_native_fs` wheel is installed (the native CI jobs on
Linux, macOS and Windows; the product jobs do not install it and skip this module). On POSIX
the service is pointed at the native layer for the test; on Windows it uses it already.
Synthetic data only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("utteran_native_fs")

from test_ec_reexport import World, tree
from utteran_contracts.transcript_export import FORMATS
from utteran_gui import reexport_files_native as native
from utteran_gui import reexport_service
from utteran_gui.job_dispatcher import OUTPUT_MARKER
from utteran_gui.reexport_files import FileError
from utteran_gui.reexport_store import ReexportError

WINDOWS = sys.platform == "win32"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: Any) -> World:
    monkeypatch.setattr(reexport_service, "reexport_files", native)
    w = World(tmp_path)
    w.service._supported = native.supported
    return w


def _json_of(world: World, job_id: str) -> Path:
    directory = world.directory(job_id)
    return directory / f"{directory.name}.json"


def test_native_layer_is_supported_and_selected_on_windows() -> None:
    assert native.supported() is True
    if WINDOWS:
        assert importlib.util.find_spec("utteran_native_fs") is not None
        assert reexport_service.reexport_files is native
        caps = __import__("utteran_native_fs").capabilities()
        assert caps["dir_creation"] == "atomic" and caps["ancestors_pinned"] == "enforced"


def test_reexport_writes_a_new_directory_and_leaves_everything_else_unchanged(
    world: World,
) -> None:
    job_id = world.finished()
    original = world.directory(job_id)
    before_out = tree(world.output)
    before_in = tree(world.inputs)
    status, record = world.run(job_id, world.body(job_id))
    assert status == 201 and record["state"] == "completed", record
    assert record["location_verified"] is True and record["partial"] is False
    target = Path(record["directory"])
    assert target.parent == world.output
    assert sorted(p.name for p in target.iterdir()) == sorted(
        [".utteran-reexport"] + [f"{target.name}.{f}" for f in FORMATS]
    )
    assert (target / ".utteran-reexport").read_text() == record["id"] + "\n"
    for item in record["files"]:
        data = (target / f"{target.name}.{item['format']}").read_bytes()
        assert item["sha256"] == hashlib.sha256(data).hexdigest() and item["size"] == len(data)
    after = tree(world.output)
    assert {k: v for k, v in after.items() if not k.startswith(target.name)} == before_out
    assert tree(world.inputs) == before_in
    assert (original / OUTPUT_MARKER).read_text().strip() == job_id
    # Same key: replayed from the record, nothing written twice.
    snapshot = tree(world.output)
    key = "native-replay-key-01"
    _, first = world.run(job_id, world.body(job_id), key=key)
    snapshot = tree(world.output)
    status2, second = world.run(job_id, world.body(job_id), key=key)
    assert status2 == 200 and second == first and tree(world.output) == snapshot


@pytest.mark.parametrize(
    "tamper,code",
    [
        (lambda p: p.write_bytes(p.read_bytes() + b" "), "source_changed"),
        (
            lambda p: p.write_bytes(p.read_bytes().replace(b"SPEAKER_00", b"SPEAKER_X0")),
            "source_changed",
        ),
        (lambda p: p.unlink(), "source_missing"),
        (lambda p: os.link(p, p.with_suffix(".hard")), "source_link"),
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
    assert plan["available"] is False and plan["refusal"] == code
    assert tree(world.output) == before


@pytest.mark.skipif(WINDOWS, reason="POSIX symlink/FIFO fixtures; Windows links in native tests")
def test_symlinked_json_or_job_directory_and_fifo_are_refused(world: World) -> None:
    job_id = world.finished()
    path = _json_of(world, job_id)
    path.rename(path.with_suffix(".bak"))
    path.symlink_to(path.with_suffix(".bak"))
    assert world.service.plan(job_id)["refusal"] == "source_link"
    path.unlink()
    os.mkfifo(path)
    assert world.service.plan(job_id)["refusal"] == "source_special"
    directory = world.directory(job_id)
    directory.rename(world.tmp / "moved")
    directory.symlink_to(world.tmp / "moved", target_is_directory=True)
    assert world.service.plan(job_id)["refusal"] == "source_missing"


def test_job_directory_and_descendants_are_refused_as_destination(world: World) -> None:
    job_id = world.finished()
    original = world.directory(job_id)
    (original / "user-made").mkdir()
    before = tree(world.output)
    for parent in (original, original / "user-made"):
        with pytest.raises(ReexportError) as caught:
            world.run(job_id, world.body(job_id, output_parent=str(parent)))
        assert (caught.value.code, caught.value.status) == ("output_inside_source", 409)
    assert tree(world.output) == before


def test_taken_names_are_never_reused(tmp_path: Path) -> None:
    parent = tmp_path / "out"
    (parent / "a").mkdir(parents=True)
    (parent / "a" / "user.txt").write_text("user")
    (parent / "b").write_text("file")
    claimed: list[str] = []
    written = native.create_output(
        str(parent), ["a", "b", "c"], "rex", {"txt": b"t"}, on_claimed=claimed.append
    )
    assert written.name == "c" and claimed == ["c"] and written.location_verified
    assert (parent / "a" / "user.txt").read_text() == "user"
    assert (parent / "b").read_text() == "file"
    assert sorted(p.name for p in (parent / "c").iterdir()) == [".utteran-reexport", "c.txt"]
    with pytest.raises(FileError) as caught:
        native.create_output(
            str(parent), ["a", "b", "c"], "rex", {"txt": b"t"}, on_claimed=claimed.append
        )
    assert caught.value.code == "output_name_unavailable"


def test_any_git_checkout_is_refused_even_when_ignored(tmp_path: Path) -> None:
    """Native-path Git policy (owner decision): a `.git` anywhere above refuses, also when
    `.gitignore` would exclude the output (the POSIX path keeps the ignore exception)."""
    repo = tmp_path / "repo"
    (repo / "output").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("output/\n")
    with pytest.raises(FileError) as caught:
        native.create_output(str(repo / "output"), ["n"], "rex", {"txt": b""}, on_claimed=print)
    assert caught.value.code == "output_in_repository"
    assert list((repo / "output").iterdir()) == []
    # A `.git` *file* (worktree / submodule) counts too.
    other = tmp_path / "wt"
    (other / "o").mkdir(parents=True)
    (other / ".git").write_text("gitdir: elsewhere\n")
    with pytest.raises(FileError) as caught:
        native.Destination(str(other / "o")).check_git(["n"], ["txt"], native.git_check)
    assert caught.value.code == "output_in_repository"


def test_parent_path_refusals(tmp_path: Path) -> None:
    for bad in ("relative", "", "\\\\server\\share", "//server/share"):
        with pytest.raises(FileError) as caught:
            native.Destination(bad)
        assert caught.value.code == "output_parent_invalid"
    with pytest.raises(FileError) as caught:
        native.Destination(str(tmp_path / "missing"))
    assert caught.value.code == "output_parent_missing"


@pytest.mark.skipif(not WINDOWS, reason="Windows junction / share-mode behaviour")
def test_windows_junction_parent_and_held_parent_rename(tmp_path: Path) -> None:
    (tmp_path / "target").mkdir()
    junction = tmp_path / "junction"
    plain = str(tmp_path).removeprefix("\\\\?\\")
    made = subprocess.run(
        ["cmd", "/C", "mklink", "/J", plain + "\\junction", plain + "\\target"],
        capture_output=True,
        check=False,
    )
    assert made.returncode == 0
    with pytest.raises(FileError) as caught:
        native.Destination(str(junction))
    assert caught.value.code == "output_parent_link"
    (tmp_path / "out" / "sub").mkdir(parents=True)
    destination = native.Destination(str(tmp_path / "out" / "sub"))
    try:
        with pytest.raises(PermissionError):
            (tmp_path / "out").rename(tmp_path / "out2")
    finally:
        destination.close()
    (tmp_path / "out").rename(tmp_path / "out2")
