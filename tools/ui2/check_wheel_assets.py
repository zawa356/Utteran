"""UI-2 packaging check: a built wheel, installed into a NEW isolated venv, serves /next/.

    uv build --wheel
    uv run --no-project python tools/ui2/check_wheel_assets.py dist/utteran-<ver>-py3-none-any.whl

Creates a temporary venv with uv, installs ``<wheel>[gui]`` plus httpx (TestClient), and runs
the probe from a temporary working directory so the source checkout cannot be imported by
accident. Exit 0 only if: the package is imported from the venv, the default app has no
/next/, and the opt-in app serves the bundled index with a matching per-response nonce and
every referenced hashed asset. Prints no paths outside the temporary directory.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PROBE = r"""
import re, sys
from pathlib import Path
from fastapi.testclient import TestClient
import utteran_gui
from utteran_gui.api import create_app

package = Path(utteran_gui.__file__).resolve()
assert Path(sys.prefix).resolve() in package.parents, "utteran_gui was not imported from the venv"
bundle = package.parent / "web-next"
assert (bundle / "index.html").is_file(), "web-next/index.html missing from the installed wheel"

root = Path.cwd()
legacy = TestClient(create_app("wheel-probe-session", repo_root=root))
assert legacy.get("/next/").status_code == 404
assert "nonce" not in legacy.get("/").headers["content-security-policy"]

client = TestClient(create_app("wheel-probe-session", repo_root=root, enable_next_ui=True))
launch = client.get("/launch?session=wheel-probe-session", follow_redirects=False)
assert launch.status_code == 303 and launch.headers["location"] == "/next/"
page = client.get("/next/")
assert page.status_code == 200, page.status_code
policy = page.headers["content-security-policy"]
nonce = re.search(r"'nonce-([A-Za-z0-9_-]+)'", policy).group(1)
assert f'content="{nonce}"' in page.text
assert "__UTTERAN_CSP_NONCE__" not in page.text
refs = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', page.text)
assert refs, "no hashed assets referenced"
for ref in refs:
    asset = client.get("/next/" + ref)
    assert asset.status_code == 200 and len(asset.content) > 0, ref
    assert "immutable" in asset.headers["cache-control"], ref
assert client.get("/api/settings").status_code == 200
print(f"wheel probe ok: {len(refs)} assets, nonce per response, legacy default unchanged")
"""


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    wheel = Path(sys.argv[1]).resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        print("expected a .whl file", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="utteran-wheel-") as temp:
        venv = Path(temp) / "venv"
        work = Path(temp) / "work"
        work.mkdir()
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "PYTHONPATH"}}
        env["UTTERAN_DATA_ROOT"] = str(Path(temp) / "data")
        subprocess.run(
            ["uv", "venv", "--quiet", "--python", "3.12", str(venv)], check=True, env=env
        )
        subprocess.run(
            ["uv", "pip", "install", "--quiet", "--python", str(python), f"{wheel}[gui]", "httpx"],
            check=True,
            env=env,
        )
        result = subprocess.run([str(python), "-I", "-c", PROBE], cwd=work, env=env, check=False)
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
