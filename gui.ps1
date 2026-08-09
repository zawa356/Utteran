[CmdletBinding()]
param(
    [string]$VenvDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = if ($VenvDir) {
    [IO.Path]::GetFullPath($VenvDir)
}
elseif ($env:UTTERAN_VENV_DIR) {
    [IO.Path]::GetFullPath($env:UTTERAN_VENV_DIR)
}
else {
    Join-Path $ProjectRoot ".venvs"
}
$GuiExecutable = Join-Path $Root "win-gui\Scripts\utteran-gui.exe"

if (-not (Test-Path -LiteralPath $GuiExecutable -PathType Leaf)) {
    Write-Error "GUI環境がありません。先に '.\setup.ps1 -Profile gui' を実行してください。"
    exit 1
}

$env:UTTERAN_PROJECT_ROOT = $ProjectRoot
$env:PYTHONIOENCODING = "utf-8"
& $GuiExecutable
exit $LASTEXITCODE
