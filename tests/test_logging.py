from __future__ import annotations

import json
import logging
from pathlib import Path

from utteran.logging import configure_logging, mask_secrets, register_secret


def test_mask_secrets_masks_hf_tokens_and_registered_values() -> None:
    register_secret("custom-secret-value")

    masked = mask_secrets("hf_abcdefgh and custom-secret-value")

    assert masked == "hf_**** and ****"


def test_structured_log_never_contains_token(tmp_path: Path) -> None:
    log_path = tmp_path / "utteran.log"
    configure_logging("info", log_path)

    logging.getLogger("test").error("failed with %s", "hf_supersecret")

    content = log_path.read_text(encoding="utf-8")
    assert "hf_supersecret" not in content
    assert json.loads(content)["message"] == "failed with hf_****"
