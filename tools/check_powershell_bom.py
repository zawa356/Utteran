"""Require UTF-8 BOM on every tracked PowerShell script."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

UTF8_BOM = b"\xef\xbb\xbf"


def tracked_powershell_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.ps1"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [root / raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw]


def files_without_bom(paths: list[Path]) -> list[Path]:
    return [path for path in paths if not path.read_bytes().startswith(UTF8_BOM)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.repo.resolve()
    paths = tracked_powershell_files(root)
    missing = files_without_bom(paths)
    if missing:
        for path in missing:
            print(f"UTF-8 BOM missing: {path.relative_to(root)}", file=sys.stderr)
        return 1
    print(f"PowerShell BOM check passed: {len(paths)} tracked file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
