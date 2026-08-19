# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the utteran GUI shell (Phase 5d installer).

Bundles only ``src/utteran_gui`` and its FastAPI/uvicorn/pywebview
dependencies - never the ``utteran`` inference core or its
torch/faster-whisper/pyannote dependencies. Those stay several GB and are
built on the user's machine by ``setup.ps1`` after install, matching the
"インストーラーに推論コアを含めない" requirement in
``docs/utteran_Phase5d_指示書.md``.

Invoked by ``build.ps1`` via:
    python -m PyInstaller --noconfirm --distpath dist --workpath build packaging/gui.spec

onedir (not onefile) was chosen deliberately: onefile re-extracts its
payload to a temp directory on every launch, adding a multi-second delay
before the first window paint every single time. onedir pays that cost once
at install time and starts in about a second afterward, at the price of
shipping a visible folder of support files next to the exe instead of one
file. For a desktop app a user launches repeatedly, startup latency matters
more than a tidier install folder, so onedir wins here.
"""

from __future__ import annotations

import os

from PyInstaller.utils.hooks import collect_submodules

REPO_ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # noqa: F821  (SPECPATH is packaging/'s own dir)
SRC_ROOT = os.path.join(REPO_ROOT, "src")
WEB_ASSETS_DIR = os.path.join(SRC_ROOT, "utteran_gui", "web")

# Never let the inference core (or the frameworks a profile venv installs
# it through) slip into this bundle. This is a build-time guard on top of
# the source-level independence already enforced by an AST regression test
# (tests/test_gui.py, Phase 5a) - that test only sees the source tree, not
# what PyInstaller's dependency walker actually decided to collect.
FORBIDDEN_MODULES = ("utteran", "torch", "faster_whisper", "pyannote", "ctranslate2")

hidden_imports = (
    collect_submodules("uvicorn")
    + collect_submodules("webview")
    + collect_submodules("fastapi")
)

datas = [(WEB_ASSETS_DIR, os.path.join("utteran_gui", "web"))]

a = Analysis(  # noqa: F821
    [os.path.join(SRC_ROOT, "utteran_gui", "__main__.py")],
    pathex=[SRC_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    excludes=list(FORBIDDEN_MODULES),
    noarchive=False,
)

for module_name, _module_path, _typecode in a.pure:
    if any(module_name == forbidden or module_name.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_MODULES):
        raise SystemExit(
            "packaging/gui.spec: refusing to build - the distributable would embed "
            f"the inference-core module '{module_name}'. The GUI must only launch "
            "profile executables as subprocesses, never import them."
        )

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="utteran-gui",
    console=False,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="utteran-gui",
)
