"""E-C2a PoC: binding tests, run against an *installed* wheel in a fresh venv.

Run from a directory outside the checkout so that nothing is imported from source:
    python -m pytest -p no:cacheprovider <checkout>/native/safe-fs-py/tests
Only synthetic data inside per-test temp directories is used.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from pathlib import Path

import pytest
import utteran_safe_fs_poc as sf


def chain_of(p: Path) -> sf.DirChain:
    return sf.DirChain.open(str(p.resolve()))


def err_code(exc: pytest.ExceptionInfo[sf.SafeFsError]) -> str:
    return str(exc.value.args[0])


def test_installed_not_from_source() -> None:
    mod = Path(sf.__file__).resolve()
    assert "site-packages" in mod.parts, "must import the installed wheel"


def test_capabilities() -> None:
    c = sf.capabilities()
    assert c["handle_relative_lookup"] is True and c["no_follow"] is True
    if sys.platform == "win32":
        assert c["ancestors_pinned"] == "enforced"
        assert c["dir_creation"] == "atomic"
    else:
        assert c["ancestors_pinned"] == "detected_only"
        assert c["dir_creation"] == "reopened_by_name"


def test_round_trip_and_fixed_errors(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "existing.txt").write_bytes(b"keep")
    with chain_of(tmp_path) as ch:
        assert ch.depth >= 2
        ch.push_existing("out")
        how = ch.push_new("job_reexport_x")
        assert how in ("atomic", "reopened_by_name")
        meta = ch.write_new_file("a.srt", b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
        assert meta["size"] == 35 and meta["link_count"] == 1
        assert ch.read_file("a.srt", 1024).startswith(b"1\n")
        with pytest.raises(sf.SafeFsError) as e:
            ch.write_new_file("a.srt", b"x")
        assert err_code(e) == "already_exists"
        with pytest.raises(sf.SafeFsError) as e:
            ch.read_file("a.srt", 3)
        assert err_code(e) == "too_large"
        with pytest.raises(sf.SafeFsError) as e:
            ch.push_existing("../out")
        assert err_code(e) == "invalid_name"
        # Messages carry only the fixed code: no names or paths.
        assert "job_reexport" not in str(e.value) and str(tmp_path) not in str(e.value)
        ch.verify_reachable()
    assert ch.closed
    with pytest.raises(sf.SafeFsError) as e:
        ch.identities()
    assert err_code(e) == "closed"
    assert (tmp_path / "existing.txt").read_bytes() == b"keep"


def test_git_marker_probe(tmp_path: Path) -> None:
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "o").mkdir()
    with chain_of(tmp_path / "repo" / "o") as ch:
        found = ch.find_in_ancestors(".git")
        assert found is not None and found[1] == "directory"
        assert ch.probe(".git") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_symlink_refused(tmp_path: Path) -> None:
    (tmp_path / "t").write_bytes(b"t")
    os.symlink(tmp_path / "t", tmp_path / "l")
    with chain_of(tmp_path) as ch, pytest.raises(sf.SafeFsError) as e:
        ch.read_file("l", 10)
    assert err_code(e) == "link_or_reparse"


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="Linux fd table")
def test_close_releases_every_handle(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    before = len(os.listdir("/proc/self/fd"))
    ch = chain_of(tmp_path / "a" / "b")
    held = len(os.listdir("/proc/self/fd")) - before
    assert held >= 3
    ch.close()
    assert len(os.listdir("/proc/self/fd")) == before
    ch2 = chain_of(tmp_path / "a" / "b")
    del ch2  # refcount drop releases handles too
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share-mode pinning")
def test_windows_held_ancestor_rename_refused(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    ch = chain_of(tmp_path / "a" / "b")
    with pytest.raises(PermissionError):
        (tmp_path / "a").rename(tmp_path / "a2")
    ch.close()
    (tmp_path / "a").rename(tmp_path / "a2")


def test_gil_released_during_large_write(tmp_path: Path) -> None:
    data = os.urandom(1) * (128 << 20)
    ticks = 0
    stop = threading.Event()

    def spin() -> None:
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            time.sleep(0)

    t = threading.Thread(target=spin)
    t.start()
    with chain_of(tmp_path) as ch:
        ticks_before = ticks
        ch.write_new_file("big.bin", data)
        progressed = ticks - ticks_before
    stop.set()
    t.join()
    assert progressed > 0, "another Python thread ran while Rust wrote 128 MiB"
    digest = hashlib.sha256((tmp_path / "big.bin").read_bytes()).hexdigest()
    assert digest == hashlib.sha256(data).hexdigest()
