"""Build-selected defaults for the portable PyInstaller artifact."""

from __future__ import annotations

import os
import sys
from pathlib import Path

application_dir = Path(sys.executable).resolve().parent
os.environ["UTTERAN_DATA_ROOT"] = str(application_dir / "data")
os.environ["UTTERAN_DISTRIBUTION"] = "portable"
os.environ["UTTERAN_TOKEN_MODE"] = "session"
