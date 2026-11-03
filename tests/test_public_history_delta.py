"""New-history privacy gate: isolated tmp Git repositories with synthetic values only.

Every synthetic value is assembled at runtime so this file itself carries no
address-, token- or path-shaped literal for the tracked-file scanner.
"""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
TOOL = ROOT / "tools" / "public_history_delta.py"
DELTA = runpy.run_path(str(TOOL))
SCANNER = runpy.run_path(str(ROOT / "tools" / "public_history_scan.py"))

TOKEN = "hf_" + "Q7zLmN4pRs8TuVw2XyZa"  # hugging-face-token shape, synthetic
EMAIL = "private.account" + "@" + "corp.invalid"
OTHER_EMAIL = "second.account" + "@" + "corp.invalid"
USER_PATH = "/".join(("", "h" + "ome", "alice", "notes"))
BOT = "noreply" + "@" + "anthropic.com"
SECRETS = (TOKEN, EMAIL, OTHER_EMAIL, USER_PATH, "alice")
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Synthetic",
    "GIT_AUTHOR_EMAIL": "test" + "@" + "example.com",
    "GIT_COMMITTER_NAME": "Synthetic",
    "GIT_COMMITTER_EMAIL": "test" + "@" + "example.com",
    # Isolate from the developer's/runner's git configuration on every platform.
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
}


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.path, env=ENV, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, f"git {args[0]} failed"  # never echo output
        return result.stdout.strip()

    def write(self, name: str, content: str | bytes) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")

    def commit(self, message: str = "change", **files: str | bytes | None) -> str:
        for name, content in files.items():
            name = name.replace("__", "/").replace("_DOT_", ".")
            if content is None:
                self.git("rm", "-q", name)
            else:
                self.write(name, content)
                self.git("add", name)
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


def baseline(tmp_path: Path, cutover: str, **overrides: Any) -> Path:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "reviewed_cutover": {"sha": cutover, "reason": "synthetic test cutover"},
        "trusted_bot_coauthor_addresses": [
            {"local": "noreply", "domain": "anthropic.com", "reason": "test"}
        ],
    }
    payload.update(overrides)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def gate(
    repo: Repo, base: str, candidate: str, cutover: str, tmp_path: Path, capsys: Any
) -> tuple[int, str, dict[str, Any] | None]:
    report = tmp_path / "delta.json"
    report.unlink(missing_ok=True)
    code = DELTA["main"](
        [
            "--repo",
            str(repo.path),
            "--baseline",
            str(baseline(tmp_path, cutover)),
            "--candidate",
            candidate,
            "--trusted-base",
            base,
            "--json",
            str(report),
        ]
    )
    out = capsys.readouterr()
    text = out.out + out.err
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else None
    assert_redacted(text, report)
    return code, text, data


def assert_redacted(text: str, report: Path) -> None:
    stored = report.read_text(encoding="utf-8") if report.exists() else ""
    for value in (*SECRETS, BOT):
        assert value not in text and value not in stored


@pytest.fixture
def history(tmp_path: Path) -> tuple[Repo, str, str]:
    """main: a clean root plus a reviewed commit that still holds a known finding."""
    repo = Repo(tmp_path / "repo")
    root = repo.commit("root", README_DOT_md="hello\n")
    cutover = repo.commit("known finding before cutover", known_DOT_txt=f"contact {EMAIL}\n")
    return repo, root, cutover


# 1 ------------------------------------------------------------------------------------


def test_known_history_only_passes(history, tmp_path, capsys) -> None:
    repo, root, cutover = history
    head = repo.commit("docs", README_DOT_md="hello again\n")
    code, text, data = gate(repo, root, head, cutover, tmp_path, capsys)
    assert code == 0 and data is not None
    assert data["new_commits"] == 1 and data["new_blocking_findings"] == 0
    assert "new_blocking=0" in text


# 2 ------------------------------------------------------------------------------------


def test_secret_added_then_deleted_fails_although_worktree_is_clean(
    history, tmp_path, capsys
) -> None:
    repo, root, cutover = history
    repo.commit("add token", config_DOT_txt=f"token={TOKEN}\n")
    head = repo.commit("remove token", config_DOT_txt=None)
    repo.commit("restore known file clean", known_DOT_txt="contact removed\n")
    head = repo.git("rev-parse", "HEAD")
    worktree = SCANNER["scan_worktree"](repo.path)
    assert worktree["summary"]["blocking_findings"] == 0
    code, _, data = gate(repo, root, head, cutover, tmp_path, capsys)
    assert code == 1 and data is not None
    hits = [(f["category"], f["detector"], f["scope"]) for f in data["findings"]]
    assert ("secret", "hugging-face-token", "blob") in hits
    assert data["new_commits"] == 3  # the intermediate commit was scanned


# 3 ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "files, detector",
    [
        ({"notes_DOT_txt": f"mail {OTHER_EMAIL}\n"}, "email-address"),
        ({"notes_DOT_txt": f"see {USER_PATH}\n"}, "absolute-user-path"),
        ({"_DOT_env": "A=1\n"}, "environment-file"),
        ({"input__rec_DOT_wav": b"RIFF\x00\x01synthetic"}, "media-file-path"),
    ],
)
def test_new_private_data_fails_even_after_deletion(
    history, tmp_path, capsys, files, detector
) -> None:
    repo, root, cutover = history
    repo.commit("add", **files)
    head = repo.commit("delete", **{name: None for name in files})
    code, _, data = gate(repo, root, head, cutover, tmp_path, capsys)
    assert code == 1 and data is not None
    assert detector in {f["detector"] for f in data["findings"]}


# 4 ------------------------------------------------------------------------------------


def test_same_total_with_one_known_removed_and_one_new_added_fails(
    history, tmp_path, capsys
) -> None:
    repo, root, cutover = history
    full_before = SCANNER["scan"](repo.path)["summary"]["blocking_findings"]
    head = repo.commit("swap", known_DOT_txt=f"contact {OTHER_EMAIL}\n")
    full_after = SCANNER["scan"](repo.path)["summary"]["blocking_findings"]
    assert full_after >= full_before  # a count comparison could not tell these apart
    code, _, data = gate(repo, root, head, cutover, tmp_path, capsys)
    assert code == 1 and data is not None
    assert data["new_blocking_findings"] == 1


# 5 ------------------------------------------------------------------------------------


def test_known_value_at_a_new_path_is_new_but_untouched_lines_are_not(
    history, tmp_path, capsys
) -> None:
    repo, root, cutover = history
    edited = repo.commit("edit unrelated line", known_DOT_txt=f"contact {EMAIL}\nmore\n")
    code, _, data = gate(repo, root, edited, cutover, tmp_path, capsys)
    assert code == 0 and data is not None and data["new_blocking_findings"] == 0
    copied = repo.commit("copy elsewhere", docs__copy_DOT_txt=f"contact {EMAIL}\n")
    code, _, data = gate(repo, root, copied, cutover, tmp_path, capsys)
    assert code == 1 and data is not None
    assert {f["scope"] for f in data["findings"]} == {"blob"}


# 6 ------------------------------------------------------------------------------------


def test_exact_bot_trailer_passes_but_addresses_elsewhere_fail(history, tmp_path, capsys) -> None:
    repo, root, cutover = history
    trailer = repo.commit(f"feature\n\nbody text\n\nCo-Authored-By: Claude Opus 5.5 <{BOT}>")
    code, _, data = gate(repo, root, trailer, cutover, tmp_path, capsys)
    assert code == 0 and data is not None
    in_body = repo.commit(f"feature\n\nwrite to {BOT} for help\n\nCo-Authored-By: Claude <{BOT}>")
    code, _, data = gate(repo, root, in_body, cutover, tmp_path, capsys)
    assert code == 1 and data is not None
    assert data["findings"][0]["scope"] == "commit-message"
    person = repo.commit(f"feature\n\nCo-Authored-By: Someone <{OTHER_EMAIL}>")
    code, _, data = gate(repo, root, person, cutover, tmp_path, capsys)
    assert code == 1  # an unlisted co-author is a real address


# 7 ------------------------------------------------------------------------------------


def test_clean_series_and_merge_of_trusted_main_pass(history, tmp_path, capsys) -> None:
    repo, _root, cutover = history
    repo.git("checkout", "-q", "-b", "feature")
    repo.commit("one", a_DOT_txt="1\n")
    repo.commit("two", a_DOT_txt="2\n")
    repo.git("checkout", "-q", "main")
    # Someone else's main commit with a finding that main already accepted.
    main_tip = repo.commit("main work", main_DOT_txt=f"owner {OTHER_EMAIL}\n")
    repo.git("checkout", "-q", "feature")
    repo.git("merge", "-q", "--no-edit", "main")
    head = repo.git("rev-parse", "HEAD")
    code, _, data = gate(repo, main_tip, head, cutover, tmp_path, capsys)
    assert code == 0 and data is not None
    assert data["new_commits"] == 3 and data["new_blocking_findings"] == 0


# 8 ------------------------------------------------------------------------------------


def test_cutover_never_moves_with_new_commits(history, tmp_path, capsys) -> None:
    repo, root, cutover = history
    repo.commit("fine", a_DOT_txt="1\n")
    leak = repo.commit("leak after cutover", b_DOT_txt=f"{TOKEN}\n")
    head = repo.commit("more", c_DOT_txt="3\n")
    code, _, data = gate(repo, root, head, cutover, tmp_path, capsys)
    assert code == 1 and data is not None
    assert data["reviewed_cutover"] == cutover
    assert leak[:12] in {f["commit"] for f in data["findings"]}
    # The CLI offers no way to pass a cutover; only the reviewed baseline file sets it.
    help_text = subprocess.run(
        [sys.executable, str(TOOL), "--help"], capture_output=True, text=True, check=True
    ).stdout
    assert "cutover" not in help_text.split("options:")[-1]


# 9 ------------------------------------------------------------------------------------


def test_missing_refs_shallow_history_and_bad_baseline_fail_closed(
    history, tmp_path, capsys
) -> None:
    repo, root, cutover = history
    head = repo.commit("x", a_DOT_txt="1\n")
    missing = "1" * 40
    assert gate(repo, missing, head, cutover, tmp_path, capsys)[0] == 2  # base
    assert gate(repo, root, missing, cutover, tmp_path, capsys)[0] == 2  # candidate
    assert gate(repo, root, head, missing, tmp_path, capsys)[0] == 2  # cutover
    assert gate(repo, head, head, cutover, tmp_path, capsys)[0] == 2  # empty range
    assert gate(repo, root, cutover, cutover, tmp_path, capsys)[0] == 2  # candidate trusted

    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", repo.path.as_uri(), str(shallow)],
        env=ENV,
        check=True,
        capture_output=True,
    )
    code = DELTA["main"](
        [
            "--repo",
            str(shallow),
            "--baseline",
            str(baseline(tmp_path, cutover)),
            "--candidate",
            head,
            "--trusted-base",
            root,
        ]
    )
    assert code == 2 and "shallow_repository" in capsys.readouterr().err

    for broken in (
        {"schema_version": 2},
        {"reviewed_cutover": {"sha": "not-a-sha", "reason": "x"}},
        {"reviewed_cutover": {"sha": cutover, "reason": ""}},
        {"trusted_bot_coauthor_addresses": [{"local": "alice", "domain": "corp.invalid"}]},
    ):
        bad = baseline(tmp_path, cutover, **broken)
        code = DELTA["main"](
            [
                "--repo",
                str(repo.path),
                "--baseline",
                str(bad),
                "--candidate",
                head,
                "--trusted-base",
                root,
            ]
        )
        assert code == 2
    (tmp_path / "garbage.json").write_text("{", encoding="utf-8")
    code = DELTA["main"](
        [
            "--repo",
            str(repo.path),
            "--baseline",
            str(tmp_path / "garbage.json"),
            "--candidate",
            head,
            "--trusted-base",
            root,
        ]
    )
    assert code == 2 and "baseline_invalid" in capsys.readouterr().err


# 10 -----------------------------------------------------------------------------------


def test_failure_output_is_redacted(history, tmp_path, capsys) -> None:
    repo, root, cutover = history
    repo.commit(f"mail {OTHER_EMAIL}", leak__deep__name_DOT_txt=f"{TOKEN} {USER_PATH}\n")
    head = repo.commit("cleanup", leak__deep__name_DOT_txt=None)
    code, text, data = gate(repo, root, head, cutover, tmp_path, capsys)  # asserts redaction
    assert code == 1 and data is not None
    assert "leak/deep/name" not in text and "leak/deep/name" not in json.dumps(data)
    assert {f["detector"] for f in data["findings"]} >= {
        "hugging-face-token",
        "absolute-user-path",
        "email-address",
    }


# 11 -----------------------------------------------------------------------------------

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
MAIN_SHA = "c" * 40


def test_event_resolution_never_compares_head_with_itself() -> None:
    resolve = DELTA["resolve_range"]
    error = DELTA["GateError"]
    pr = {"pull_request": {"head": {"sha": HEAD_SHA}, "base": {"sha": BASE_SHA}}}
    rng = resolve("pull_request", pr, main_sha=MAIN_SHA)
    assert (rng.candidate, rng.trusted_base) == (HEAD_SHA, BASE_SHA)
    branch = {"ref": "refs/heads/feature", "before": BASE_SHA, "after": HEAD_SHA}
    rng = resolve("push", branch, main_sha=MAIN_SHA)
    assert (rng.candidate, rng.trusted_base) == (HEAD_SHA, MAIN_SHA)  # not the branch's before
    to_main = {"ref": "refs/heads/main", "before": BASE_SHA, "after": HEAD_SHA}
    rng = resolve("push", to_main, main_sha=HEAD_SHA)
    assert (rng.candidate, rng.trusted_base) == (HEAD_SHA, BASE_SHA)  # pre-push SHA
    for event, payload, kwargs in (
        ("push", {**to_main, "before": "0" * 40}, {"main_sha": HEAD_SHA}),
        ("push", branch, {"main_sha": None}),
        ("push", {**branch, "after": MAIN_SHA}, {"main_sha": MAIN_SHA}),
        ("pull_request", {"pull_request": {}}, {"main_sha": None}),
        ("workflow_dispatch", {}, {"main_sha": MAIN_SHA}),
    ):
        with pytest.raises(error):
            resolve(event, payload, **kwargs)


def test_cli_github_event_mode_uses_the_event_payload(history, tmp_path, capsys) -> None:
    repo, root, cutover = history
    head = repo.commit("leak", z_DOT_txt=f"{TOKEN}\n")
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"head": {"sha": head}, "base": {"sha": root}}}),
        encoding="utf-8",
    )
    code = DELTA["main"](
        [
            "--repo",
            str(repo.path),
            "--baseline",
            str(baseline(tmp_path, cutover)),
            "--github-event",
            "pull_request",
            "--github-event-path",
            str(event),
        ]
    )
    assert code == 1 and TOKEN not in capsys.readouterr().out
    code = DELTA["main"](
        [
            "--repo",
            str(repo.path),
            "--baseline",
            str(baseline(tmp_path, cutover)),
            "--github-event",
            "push",
            "--github-event-path",
            str(event),
        ]
    )
    assert code == 2  # push event without an origin/main ref: fail closed


# 12 -----------------------------------------------------------------------------------


def test_reviewed_fixture_and_typescript_rules_hold_in_the_delta_gate(
    history, tmp_path, capsys
) -> None:
    repo, root, cutover = history
    reviewed = SCANNER["REVIEWED_MEDIA"]
    path, _blob = sorted(reviewed)[0]
    content = (ROOT / path).read_bytes()
    repo.write(path, content)
    repo.git("add", path)
    head = repo.commit("reviewed fixture + typescript", site__a_DOT_ts="export const x = 1;\n")
    code, _, data = gate(repo, root, head, cutover, tmp_path, capsys)
    assert code == 0 and data is not None
    edited = repo.commit("edit fixture", **{path.replace(".", "_DOT_"): content + b"\0"})
    code, _, data = gate(repo, root, edited, cutover, tmp_path, capsys)
    assert code == 1 and data is not None
    assert {f["detector"] for f in data["findings"]} == {"media-file-path"}


def test_shipped_baseline_pins_the_pr34_review_cutover() -> None:
    loaded = DELTA["load_baseline"](ROOT / "tools" / "public_history_baseline.json")
    assert loaded.cutover == "51dc90fa8bbdbe3c80c5e8adc3f96811677c6cb5"
    assert loaded.bot_addresses == frozenset({BOT.encode()})


# PR #34 P2 (placeholder detection): the delta gate shares the scanner's typed rules.

WIN_PATH = "C:" + "\\" + "Users" + "\\" + "alice" + "\\" + "notes"
MARKER_TOKEN = "hf_" + "test" + "Q7zLmN4pRs8TuVw2"


@pytest.mark.parametrize("value", [WIN_PATH, MARKER_TOKEN], ids=["t09-windows-path", "t10-token"])
def test_t09_t10_marker_substring_values_added_then_deleted_fail(
    history, tmp_path, capsys, value
) -> None:
    repo, root, cutover = history
    repo.commit("add", leak_DOT_txt=f"{value}\n")
    repo.commit("delete", leak_DOT_txt=None)
    head = repo.commit("clean the known pre-cutover file", known_DOT_txt="contact removed\n")
    assert SCANNER["scan_worktree"](repo.path)["summary"]["blocking_findings"] == 0
    code, text, data = gate(repo, root, head, cutover, tmp_path, capsys)
    assert code == 1 and data is not None
    assert value not in text and value not in json.dumps(data)
