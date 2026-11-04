"""Relative links in the current-architecture entry points resolve (files and anchors)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
ENTRY_POINTS = (
    ROOT / "AGENTS.md",
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "docs" / "architecture" / "README.md",
    *sorted((ROOT / "docs" / "architecture" / "backend").glob("*.md")),
)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def github_slug(heading: str) -> str:
    """Approximate GitHub's heading anchor: lowercase, drop punctuation, spaces -> '-'."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {github_slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)}


@pytest.mark.parametrize("document", ENTRY_POINTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(document: Path) -> None:
    text = FENCE.sub("", document.read_text(encoding="utf-8"))
    broken: list[str] = []
    for target in LINK.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:
            continue
        file_part, _, fragment = target.partition("#")
        resolved = (document.parent / file_part).resolve() if file_part else document
        if not resolved.exists():
            broken.append(target)
            continue
        if fragment and resolved.suffix == ".md" and fragment not in anchors(resolved):
            broken.append(target)
    assert not broken, f"{document.relative_to(ROOT)}: {broken}"
