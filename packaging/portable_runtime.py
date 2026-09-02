"""Build-selected defaults for the portable PyInstaller artifact."""

from __future__ import annotations

import os
import sys
from pathlib import Path

application_dir = Path(sys.executable).resolve().parent
data_root = application_dir / "data"
cache_root = data_root / "cache"
os.environ["UTTERAN_DATA_ROOT"] = str(data_root)
os.environ["UTTERAN_DISTRIBUTION"] = "portable"
os.environ["UTTERAN_TOKEN_MODE"] = "session"
os.environ["UV_CACHE_DIR"] = str(cache_root / "uv")
os.environ["HF_HOME"] = str(cache_root / "huggingface")
os.environ["TORCH_HOME"] = str(cache_root / "torch")
os.environ["XDG_CACHE_HOME"] = str(cache_root)
os.environ["PIP_CACHE_DIR"] = str(cache_root / "pip")
os.environ["WEBVIEW2_USER_DATA_FOLDER"] = str(cache_root / "webview2")
