from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
FORBIDDEN = {
    "utteran",
    "utteran_gui",
    "torch",
    "torchaudio",
    "openvino",
    "openvino_genai",
    "faster_whisper",
    "ctranslate2",
    "pyannote",
    "onnxruntime",
    "sudachipy",
    "webview",
}


def test_contract_source_import_direction() -> None:
    for file in (ROOT / "src/utteran_contracts").glob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [x.name for x in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(name.split(".", 1)[0] in FORBIDDEN for name in names), file


def test_import_and_export_have_no_execution_side_effects(tmp_path: Path) -> None:
    code = r"""
import sys, importlib.abc
blocked = set(sys.argv[1].split(','))
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('forbidden import')
sys.meta_path.insert(0, Guard())
def audit(event, args):
    if event.startswith(('subprocess.', 'socket.connect', 'socket.bind', 'os.system', 'os.fork')):
        raise AssertionError('execution side effect')
    if event in {'os.mkdir','os.remove','os.rename','os.rmdir','os.unlink'}:
        raise AssertionError('filesystem mutation')
    if event == 'open' and (args[2] & (64 | 512 | 1024 | 1 | 2)):
        raise AssertionError('file write')
sys.addaudithook(audit)
import utteran_contracts
from utteran_contracts.schema import schema_bytes
from utteran_contracts.worker_schema import schema_bytes as worker_schema_bytes
assert schema_bytes() and worker_schema_bytes()
assert not blocked.intersection(sys.modules)
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code, ",".join(sorted(FORBIDDEN))],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert list(tmp_path.iterdir()) == []


def test_gui_can_import_contracts_without_core() -> None:
    code = """
import sys
import utteran_gui.api
import utteran_gui.worker_client
import utteran_contracts
from utteran_contracts.schema import schema_bytes
from utteran_contracts.worker_schema import schema_bytes as worker_schema_bytes
assert schema_bytes() and worker_schema_bytes()
assert not any(name in sys.modules for name in (
 'utteran','torch','torchaudio','openvino','openvino_genai','faster_whisper','pyannote','ctranslate2'))
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert completed.returncode == 0, completed.stderr


def test_distribution_includes_contract_package() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert (
        "src/utteran_contracts" in config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    )
    assert "utteran_contracts" in config["tool"]["mypy"]["packages"]
