"""Create and verify the backups required before a repository history rewrite."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _run(root: Path, *command: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, cwd=root, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"backup command failed: {command[0]} {command[1]}")
    return result


def _git(root: Path, *args: str) -> str:
    return _run(root, "git", *args).stdout


def _refs(root: Path) -> list[dict[str, str]]:
    lines = _git(
        root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(objecttype)%00%(*objectname)",
    ).splitlines()
    return [
        dict(zip(("ref", "object", "type", "peeled"), line.split("\0"), strict=True))
        for line in lines
    ]


def _release_metadata(root: Path) -> list[dict[str, object]]:
    if shutil.which("gh") is None:
        return []
    result = subprocess.run(
        [
            "gh",
            "release",
            "list",
            "--limit",
            "100",
            "--json",
            "name,tagName,isDraft,isPrerelease,publishedAt",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    payload = json.loads(result.stdout)
    return payload if isinstance(payload, list) else []


def create_backup(root: Path, destination: Path) -> dict[str, object]:
    root = root.resolve()
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError("backup destination already exists")
    try:
        relative = destination.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("backup destination must be inside the repository") from exc
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", relative], cwd=root, check=False
    )
    if ignored.returncode != 0:
        raise ValueError("backup destination must be Git-ignored")

    destination.mkdir(parents=True)
    refs = _refs(root)
    state = {
        "schema_version": 1,
        "head": _git(root, "rev-parse", "HEAD").strip(),
        "head_tree": _git(root, "rev-parse", "HEAD^{tree}").strip(),
        "branch": _git(root, "branch", "--show-current").strip(),
        "refs": refs,
        "releases": _release_metadata(root),
    }
    (destination / "pre-rewrite-state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    bundle = destination / "all-refs.bundle"
    _run(root, "git", "bundle", "create", str(bundle), "--all")
    _run(root, "git", "bundle", "verify", str(bundle))

    mirror = destination / "mirror.git"
    _run(root, "git", "clone", "--mirror", "--no-hardlinks", str(root), str(mirror))
    _run(mirror, "git", "fsck", "--full")
    mirror_refs = _refs(mirror)
    if refs != mirror_refs:
        raise AssertionError("mirror ref inventory differs from the source repository")

    restore = destination / "restore-test.git"
    _run(root, "git", "clone", "--mirror", str(bundle), str(restore))
    _run(restore, "git", "fsck", "--full")
    bundle_refs = {item["ref"]: item["object"] for item in _refs(restore)}
    required = {
        item["ref"]: item["object"]
        for item in refs
        if item["ref"].startswith(("refs/heads/", "refs/remotes/", "refs/tags/"))
    }
    if any(bundle_refs.get(ref) != object_id for ref, object_id in required.items()):
        raise AssertionError("bundle restore is missing a branch, remote-tracking ref, or tag")

    result = {
        "refs": len(refs),
        "releases": len(state["releases"]),
        "bundle_verified": True,
        "mirror_verified": True,
        "restore_verified": True,
        "head_tree": state["head_tree"],
    }
    (destination / "verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = create_backup(args.repo, args.output)
    print(
        "history-backup: "
        f"refs={result['refs']} releases={result['releases']} "
        "bundle=true mirror=true restore=true"
    )
    print("backup paths and repository-specific values are intentionally omitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
