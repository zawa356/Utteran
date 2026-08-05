from __future__ import annotations

from pathlib import Path


def test_history_backup_does_not_embed_workspace_or_private_values() -> None:
    source = (Path(__file__).parents[1] / "tools" / "history_backup.py").read_text(encoding="utf-8")

    assert "--mirror" in source
    assert "--no-hardlinks" in source
    assert '"bundle", "verify"' in source
    assert ".env" not in source
