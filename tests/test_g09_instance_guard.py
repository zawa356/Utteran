"""G-09 startup guard units: exact launch flags, development data root, OS instance lock.

Pure ``utteran_gui.instance_guard`` tests on synthetic ``tmp_path`` directories (Linux and
Windows CI). Cross-process exclusion, the launcher and failure paths are in
``test_g09_startup_guard.py``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from utteran_gui import instance_guard as guard
from utteran_gui.instance_guard import (
    DEV_MARKER_CONTENT,
    DEV_MARKER_NAME,
    LOCK_FILE_NAME,
    GuiInstanceLock,
    StartupRefused,
    launch_mode,
    validate_dev_data_root,
)


@pytest.fixture(autouse=True)
def _no_distribution_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UTTERAN_DISTRIBUTION", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)


def dev_root(tmp_path: Path, name: str = "dev-root") -> Path:
    root = tmp_path / name
    root.mkdir()
    return root


def snapshot_tree(root: Path) -> dict[str, bytes]:
    # A held lock file is not readable on Windows (mandatory byte-range lock), and its
    # content is never written anyway: record only that it exists.
    return {
        str(p.relative_to(root)): (
            b"<lock>"
            if p.name == "gui-instance.lock"
            else p.read_bytes()
            if p.is_file()
            else b"<dir>"
        )
        for p in sorted(root.rglob("*"))
    }


# --------------------------------------------------------------------------- 1. flags


@pytest.mark.parametrize(
    ("argv", "mode"),
    [
        ([], "legacy"),
        (["--next-ui"], "next_ui"),
        (["--next-ui-v1"], "next_ui_v1"),
        (["--next-ui-v1", "extra"], "legacy"),
        (["--next-ui", "--next-ui-v1"], "legacy"),
        (["--next-ui-v1", "--next-ui"], "legacy"),
        (["--next-ui-v1="], "legacy"),
        (["--next-ui-v"], "legacy"),
        (["--NEXT-UI-V1"], "legacy"),
        (["--next-ui-v12"], "legacy"),
        ([" --next-ui-v1"], "legacy"),
    ],
)
def test_launch_mode_is_an_exact_match_and_never_rounds_to_v1(argv: list[str], mode: str) -> None:
    assert launch_mode(["utteran-gui", *argv]) == mode


# --------------------------------------------------------------------------- 2. data root


@pytest.mark.parametrize("value", [None, "", "   "])
def test_v1_requires_an_explicit_data_root(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("UTTERAN_DATA_ROOT", raising=False)
    else:
        monkeypatch.setenv("UTTERAN_DATA_ROOT", value)
    with pytest.raises(StartupRefused) as refused:
        validate_dev_data_root()
    assert refused.value.code == "dev_root_required"


def test_relative_missing_or_file_roots_are_refused_and_never_created(tmp_path: Path) -> None:
    with pytest.raises(StartupRefused, match="dev_root_not_absolute"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": "relative-dev-root"})
    missing = tmp_path / "missing"
    with pytest.raises(StartupRefused, match="dev_root_missing"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(missing)})
    assert not missing.exists()
    file_root = tmp_path / "file-root"
    file_root.write_text("synthetic", encoding="utf-8")
    with pytest.raises(StartupRefused, match="dev_root_missing"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(file_root)})


@pytest.mark.parametrize("identity", ["portable", "installer", "anything"])
def test_distribution_identity_fails_closed_without_touching_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: str
) -> None:
    root = dev_root(tmp_path)
    monkeypatch.setenv("UTTERAN_DISTRIBUTION", identity)
    with pytest.raises(StartupRefused, match="dev_root_distribution"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(root)})
    assert list(root.iterdir()) == []


def test_frozen_executable_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = dev_root(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(StartupRefused, match="dev_root_distribution"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(root)})
    assert list(root.iterdir()) == []


def test_empty_directory_gets_the_fixed_marker_and_is_reused(tmp_path: Path) -> None:
    root = dev_root(tmp_path)
    assert validate_dev_data_root({"UTTERAN_DATA_ROOT": str(root)}) == root.resolve()
    marker = root / DEV_MARKER_NAME
    assert marker.read_text(encoding="utf-8") == DEV_MARKER_CONTENT
    # The marker holds no path, user or time value.
    assert json.loads(DEV_MARKER_CONTENT) == {
        "kind": "utteran-dev-data-root",
        "purpose": "next-ui-v1",
        "schema_version": 1,
    }
    (root / "data").mkdir()  # later runs add data next to the marker
    assert validate_dev_data_root({"UTTERAN_DATA_ROOT": str(root)}) == root.resolve()
    assert marker.read_text(encoding="utf-8") == DEV_MARKER_CONTENT


@pytest.mark.parametrize(
    "existing",
    [
        "config/gui-settings.json",  # a real/portable layout
        "data/control-v1/operations.sqlite3",
        "unrelated.txt",
        "gui-instance.lock",  # a root a legacy GUI already used
    ],
)
def test_non_empty_unmarked_directory_is_refused_and_left_untouched(
    tmp_path: Path, existing: str
) -> None:
    root = dev_root(tmp_path)
    target = root / existing
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"synthetic existing data")
    before = snapshot_tree(root)
    with pytest.raises(StartupRefused, match="dev_root_not_marked"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(root)})
    assert snapshot_tree(root) == before


def test_tampered_or_non_file_marker_is_refused(tmp_path: Path) -> None:
    root = dev_root(tmp_path)
    (root / DEV_MARKER_NAME).write_text('{"kind":"other"}\n', encoding="utf-8")
    with pytest.raises(StartupRefused, match="dev_root_not_marked"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(root)})
    other = dev_root(tmp_path, "dir-marker")
    (other / DEV_MARKER_NAME).mkdir()
    with pytest.raises(StartupRefused, match="dev_root_not_marked"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(other)})


def test_reserved_default_locations_home_and_filesystem_root_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = tmp_path / "defaults"
    user_data = defaults / "utteran"
    user_data.mkdir(parents=True)
    monkeypatch.setattr(guard, "reserved_locations", lambda: [user_data])
    inside = user_data / "dev"
    inside.mkdir()
    for candidate in (user_data, inside, defaults):  # equal, inside, around
        with pytest.raises(StartupRefused, match="dev_root_reserved"):
            validate_dev_data_root({"UTTERAN_DATA_ROOT": str(candidate)})
    assert not (inside / DEV_MARKER_NAME).exists()

    home = dev_root(tmp_path, "home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for candidate in (home, tmp_path):  # home itself and an ancestor of home
        with pytest.raises(StartupRefused, match="dev_root_reserved"):
            validate_dev_data_root({"UTTERAN_DATA_ROOT": str(candidate)})
    below_home = home / "scratch"
    below_home.mkdir()
    assert validate_dev_data_root({"UTTERAN_DATA_ROOT": str(below_home)}) == below_home.resolve()

    anchor = Path(tmp_path.anchor)
    with pytest.raises(StartupRefused, match="dev_root_reserved"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(anchor)})


def test_real_default_user_locations_are_reserved() -> None:
    from platformdirs import user_config_dir, user_data_dir

    reserved = guard.reserved_locations()
    assert Path(user_data_dir("utteran")) in reserved
    assert Path(user_config_dir("utteran-gui")) in reserved


def make_alias(link: Path, target: Path) -> None:
    """A directory alias: a symlink on POSIX, an NTFS junction (reparse point) on Windows."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))  # type: ignore[attr-defined]
    else:
        link.symlink_to(target, target_is_directory=True)


def test_aliases_resolve_to_one_root_and_one_lock(tmp_path: Path) -> None:
    real = dev_root(tmp_path, "real-root")
    alias = tmp_path / "alias-root"
    make_alias(alias, real)
    canonical = validate_dev_data_root({"UTTERAN_DATA_ROOT": str(alias)})
    assert canonical == real.resolve()
    assert (real / DEV_MARKER_NAME).is_file()
    assert validate_dev_data_root({"UTTERAN_DATA_ROOT": str(real)}) == canonical
    for held, other in ((alias, real), (real, alias)):
        lock = GuiInstanceLock(held).acquire()
        try:
            with pytest.raises(StartupRefused, match="gui_already_running"):
                GuiInstanceLock(other).acquire()
        finally:
            lock.release()


def test_symlinked_marker_is_not_trusted(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("file symlinks need a privilege on Windows; junction alias covered above")
    elsewhere = tmp_path / "elsewhere-marker"
    elsewhere.write_text(DEV_MARKER_CONTENT, encoding="utf-8")
    root = dev_root(tmp_path)
    (root / DEV_MARKER_NAME).symlink_to(elsewhere)
    with pytest.raises(StartupRefused, match="dev_root_not_marked"):
        validate_dev_data_root({"UTTERAN_DATA_ROOT": str(root)})


def test_refusal_messages_are_fixed_and_value_free(tmp_path: Path) -> None:
    for code, message in guard.REFUSAL_MESSAGES.items():
        assert StartupRefused(code).message == message  # type: ignore[arg-type]
        assert str(tmp_path) not in message and os.sep + "home" not in message


# --------------------------------------------------------------------------- 3. lock


def test_lock_excludes_a_second_holder_and_is_reacquirable(tmp_path: Path) -> None:
    first = GuiInstanceLock(tmp_path).acquire()
    try:
        with pytest.raises(StartupRefused) as refused:
            GuiInstanceLock(tmp_path).acquire()
        assert refused.value.code == "gui_already_running"
        assert first._fd is not None and not os.get_inheritable(first._fd)
    finally:
        first.release()
    first.release()  # idempotent
    assert (tmp_path / LOCK_FILE_NAME).exists()  # never unlinked
    with GuiInstanceLock(tmp_path):
        pass


def test_different_roots_lock_independently(tmp_path: Path) -> None:
    a, b = dev_root(tmp_path, "a"), dev_root(tmp_path, "b")
    with GuiInstanceLock(a), GuiInstanceLock(b):
        pass


def test_missing_lock_directory_is_an_open_failure_not_contention(tmp_path: Path) -> None:
    with pytest.raises(StartupRefused, match="gui_lock_unavailable"):
        GuiInstanceLock(tmp_path / "absent").acquire()
    lock = GuiInstanceLock(tmp_path / "absent").acquire(create_parent=True)
    lock.release()


def test_default_layout_lock_lives_in_the_default_user_data_directory() -> None:
    from platformdirs import user_data_dir

    assert guard.lock_directory(None) == Path(user_data_dir("utteran"))
    assert guard.lock_directory(Path("x")) == Path("x")
