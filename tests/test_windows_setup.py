"""Windows setup regressions observed during #22 integration acceptance."""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from utteran_gui.setup_executor import ProductionStepAdapter


def test_setup_children_do_not_inherit_a_different_powershell_module_path(tmp_path):
    inherited = {"Path": "synthetic", "PSModulePath": "incompatible-modules"}
    adapter = ProductionStepAdapter(
        SimpleNamespace(repo_root=tmp_path, environment=lambda _: inherited.copy())
    )
    assert adapter.environment("cpu") == {"Path": "synthetic"}
    assert inherited["PSModulePath"] == "incompatible-modules"


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 native stderr semantics")
@pytest.mark.parametrize("stderr", [False, True], ids=["nonzero", "store-alias-stderr"])
def test_optional_python_probe_does_not_prevent_locked_uv_setup(tmp_path, stderr):
    stub = tmp_path / "python-stub.cmd"
    stub.write_text(
        "@echo off\n" + ("echo unavailable 1>&2\n" if stderr else "") + "exit /b 9009\n",
        encoding="ascii",
    )
    source = Path(__file__).parents[1] / "setup.ps1"

    def literal(path):
        return "'" + str(path).replace("'", "''") + "'"

    # Execute the actual function but stop at Install-Uv: no downloads or sync.
    script = f"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile({literal(source)},
    [ref]$tokens, [ref]$errors)
$function = $ast.Find({{param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Invoke-ProfileSetup'}}, $true)
Invoke-Expression $function.Extent.Text
$ProjectRoot = {literal(tmp_path)}
$ProfileExtras = @{{cpu = @('cpu')}}
function Get-VenvRoot {{ return $ProjectRoot }}
function Get-ProfileVenvPath {{ return $ProjectRoot }}
function Show-LegacyEnvironmentNotice {{}}
function Write-Step {{}}
function Get-Command {{ return [pscustomobject]@{{Source = {literal(stub)}}} }}
function Install-Uv {{ throw 'reached-locked-uv-stage' }}
try {{ Invoke-ProfileSetup -ProfileName cpu }}
catch {{
    if ($_.Exception.Message -eq 'reached-locked-uv-stage') {{ exit 0 }}
    Write-Error $_ -ErrorAction Continue
    exit 1
}}
exit 2
"""
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
        ],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
